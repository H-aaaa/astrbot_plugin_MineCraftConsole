import asyncio
import struct
import yaml
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

RCON_TYPE_RESPONSE_VALUE = 0
RCON_TYPE_EXECCOMMAND = 2
RCON_TYPE_AUTH = 3


class RconError(Exception):
    pass


class RconAuthError(RconError):
    pass


class RconProtocolError(RconError):
    pass


@dataclass
class RconConfig:
    host: str
    port: int
    password: str
    timeout: float = 5.0


class AsyncRconClient:
    def __init__(self, cfg: RconConfig):
        self.cfg = cfg
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 10
        self._connected = False
        self._authed = False

    async def connect(self) -> None:
        if self._connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.host, self.cfg.port),
                timeout=self.cfg.timeout,
            )
            self._connected = True
        except Exception as e:
            raise RconError(f"RCON connect failed: {e}") from e

    async def close(self) -> None:
        self._authed = False
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    def _next_id(self) -> int:
        self._req_id += 1
        if self._req_id > 2_000_000_000:
            self._req_id = 10
        return self._req_id

    def _pack(self, req_id: int, ptype: int, payload: str) -> bytes:
        body = struct.pack("<ii", req_id, ptype) + payload.encode("utf-8") + b"\x00\x00"
        return struct.pack("<i", len(body)) + body

    async def _send_packet(self, req_id: int, ptype: int, payload: str) -> None:
        if not self._writer:
            raise RconError("RCON not connected")
        data = self._pack(req_id, ptype, payload)
        self._writer.write(data)
        await asyncio.wait_for(self._writer.drain(), timeout=self.cfg.timeout)

    async def _read_exactly(self, n: int) -> bytes:
        if not self._reader:
            raise RconError("RCON not connected")
        try:
            return await asyncio.wait_for(self._reader.readexactly(n), timeout=self.cfg.timeout)
        except asyncio.IncompleteReadError as e:
            raise RconProtocolError("RCON connection closed unexpectedly") from e
        except asyncio.TimeoutError as e:
            raise RconError("RCON read timeout") from e

    async def _read_packet(self) -> tuple[int, int, str]:
        raw_len = await self._read_exactly(4)
        (length,) = struct.unpack("<i", raw_len)
        if length < 10:
            raise RconProtocolError(f"Invalid RCON packet length: {length}")

        body = await self._read_exactly(length)
        req_id, ptype = struct.unpack("<ii", body[:8])
        payload_raw = body[8:]

        if len(payload_raw) < 2 or payload_raw[-2:] != b"\x00\x00":
            raise RconProtocolError("Invalid RCON payload terminator")

        payload_bytes = payload_raw[:-2]
        payload = payload_bytes.decode("utf-8", errors="replace")
        return req_id, ptype, payload

    async def auth(self) -> None:
        await self.connect()

        auth_id = self._next_id()
        await self._send_packet(auth_id, RCON_TYPE_AUTH, self.cfg.password)

        deadline = asyncio.get_running_loop().time() + self.cfg.timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON auth timeout")

            req_id, ptype, payload = await asyncio.wait_for(self._read_packet(), timeout=remaining)

            if req_id == -1:
                raise RconAuthError("RCON auth failed (bad password?)")

            if req_id == auth_id:
                self._authed = True
                return

    async def exec(self, command: str) -> str:
        if not self._connected:
            await self.connect()
        if not self._authed:
            await self.auth()

        cmd_id = self._next_id()
        end_id = self._next_id()

        await self._send_packet(cmd_id, RCON_TYPE_EXECCOMMAND, command)
        # 终止包：用空命令来标记“收完了”
        await self._send_packet(end_id, RCON_TYPE_EXECCOMMAND, "")

        chunks: list[str] = []
        deadline = asyncio.get_running_loop().time() + self.cfg.timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON command response timeout")

            req_id, ptype, payload = await asyncio.wait_for(self._read_packet(), timeout=remaining)

            if req_id == end_id:
                break

            if req_id == cmd_id and payload:
                chunks.append(payload)

        return "".join(chunks).strip("\n")

@register("minecraftconsole", "MineCraft控制台", "使用Rcon发送命令至MC", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config: dict = {}
        self.rcon_cfg: Optional[RconConfig] = None
        self._rcon_lock = asyncio.Lock()
        self._ready = False 
        self._config_path = Path("config.yml")

    def _default_config(self) -> dict:
        return {
            "admins": [123456789],
            "rcon": {
                "host": "127.0.0.1",
                "port": 25575,
                "password": "CHANGE_ME",
                "timeout": 5,
            },
        }

    def _write_default_config(self) -> None:
        data = self._default_config()
        self._config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _load_config(self) -> dict:
        return yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}

    async def initialize(self):
        """
        插件加载时：
        - 检查 config.yml 是否存在
        - 不存在则自动创建默认配置并提示
        - 读取并校验配置
        """
        try:
            if not self._config_path.exists():
                self._write_default_config()
                logger.warning(
                    f"[minecraftconsole] 未找到 {self._config_path}，已自动生成默认配置。"
                    "请修改 admins 和 rcon.password 后重启插件。"
                )
                self._ready = False
                return

            self.config = self._load_config()

            admins = self.config.get("admins", [])
            r = self.config.get("rcon") or {}
            host = r.get("host")
            port = r.get("port")
            password = r.get("password")
            timeout = r.get("timeout", 5)

            if not isinstance(admins, list) or not admins:
                logger.error("[minecraftconsole] 配置错误：admins 必须是非空列表")
                self._ready = False
                return

            if not host or not port or not password:
                logger.error("[minecraftconsole] 配置错误：rcon.host / rcon.port / rcon.password 必填")
                self._ready = False
                return

            if str(password).strip() == "CHANGE_ME":
                logger.error("[minecraftconsole] 请先把 config.yml 里的 rcon.password 从 CHANGE_ME 改成真实密码")
                self._ready = False
                return

            self.rcon_cfg = RconConfig(
                host=str(host),
                port=int(port),
                password=str(password),
                timeout=float(timeout),
            )

            self._ready = True
            logger.info("[minecraftconsole] 配置加载完成，插件已就绪")

        except Exception as e:
            logger.error(f"[minecraftconsole] 初始化失败：{e}")
            self._ready = False

    @filter.command("mc-command")
    async def mc_command(self, event: AstrMessageEvent):
        if not self._ready:
            yield event.plain_result("⚠️ 插件未就绪：请检查/修改 config.yml 后重启插件")
            return

        user_id = event.get_sender_id()
        message_str = (event.message_str or "").strip()

        parts = message_str.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：/mc-command <MC命令>")
            return

        command = parts[1].strip()
        if not command:
            yield event.plain_result("用法：/mc-command <MC命令>")
            return

        admins = {str(x) for x in self.config.get("admins", [])}
        if str(user_id) not in admins:
            yield event.plain_result("❌ 你没有权限使用该指令")
            return

        assert self.rcon_cfg is not None

        async with self._rcon_lock:
            client = AsyncRconClient(self.rcon_cfg)
            try:
                result = await client.exec(command)
                if not result:
                    result = "(无输出)"
                yield event.plain_result(f"✅ 已执行：{command}\n📤 返回：{result}")
            except RconAuthError:
                yield event.plain_result("❌ RCON 认证失败：请检查 config.yml 的 rcon.password")
            except Exception as e:
                logger.error(f"RCON 执行失败: {e}")
                yield event.plain_result("❌ RCON 执行失败：请检查服务器地址/端口/防火墙/enable-rcon")
            finally:
                await client.close()

    async def terminate(self):
        logger.info("[minecraftconsole] 插件已卸载/停用")
