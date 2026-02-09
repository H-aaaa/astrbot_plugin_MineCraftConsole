import asyncio
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import yaml
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register


# ========== RCON 协议常量 ==========
RCON_TYPE_RESPONSE_VALUE = 0
RCON_TYPE_EXECCOMMAND = 2
RCON_TYPE_AUTH = 3
RCON_TYPE_AUTH_RESPONSE = 2  # 语义上与 EXECCOMMAND 同值，但含义不同


# ========== 安全/健壮性参数 ==========
MAX_RCON_PACKET_SIZE = 1024 * 1024  # 1MB 上限：防止异常/恶意 length
DEFAULT_TIMEOUT = 5.0
MAX_CHAT_OUTPUT = 1500  # 输出截断阈值，避免刷屏


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
    timeout: float = DEFAULT_TIMEOUT


class AsyncRconClient:
    """
    纯 asyncio RCON 客户端（Source RCON / Minecraft RCON）
    - connect(): 建立 TCP
    - auth(): 认证（严格校验 AUTH_RESPONSE，避免误判）
    - exec(): 执行命令并收集多包响应（终止包技巧）
    - close(): 关闭连接
    """

    def __init__(self, cfg: RconConfig):
        self.cfg = cfg
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 10
        self._connected = False
        self._authed = False

    @property
    def connected(self) -> bool:
        return self._connected and self._writer is not None and not self._writer.is_closing()

    @property
    def authed(self) -> bool:
        return self._authed

    def _next_id(self) -> int:
        self._req_id += 1
        if self._req_id > 2_000_000_000:
            self._req_id = 10
        return self._req_id

    def _pack(self, req_id: int, ptype: int, payload: str) -> bytes:
        body = (
            struct.pack("<ii", req_id, ptype)
            + payload.encode("utf-8")
            + b"\x00\x00"
        )
        return struct.pack("<i", len(body)) + body

    async def connect(self) -> None:
        if self.connected:
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

    async def _send_packet(self, req_id: int, ptype: int, payload: str) -> None:
        if not self.connected or not self._writer:
            raise RconError("RCON not connected")
        data = self._pack(req_id, ptype, payload)
        self._writer.write(data)
        await asyncio.wait_for(self._writer.drain(), timeout=self.cfg.timeout)

    async def _read_exactly(self, n: int) -> bytes:
        if not self.connected or not self._reader:
            raise RconError("RCON not connected")

        try:
            return await asyncio.wait_for(self._reader.readexactly(n), timeout=self.cfg.timeout)
        except asyncio.IncompleteReadError as e:
            raise RconProtocolError("RCON connection closed unexpectedly") from e
        except asyncio.TimeoutError as e:
            raise RconError("RCON read timeout") from e

    async def _read_packet(self) -> Tuple[int, int, str]:
        raw_len = await self._read_exactly(4)
        (length,) = struct.unpack("<i", raw_len)

        if length < 10:
            raise RconProtocolError(f"Invalid RCON packet length: {length}")
        if length > MAX_RCON_PACKET_SIZE:
            raise RconProtocolError(f"RCON packet too large: {length} > {MAX_RCON_PACKET_SIZE}")

        body = await self._read_exactly(length)
        req_id, ptype = struct.unpack("<ii", body[:8])
        payload_raw = body[8:]

        if len(payload_raw) < 2 or payload_raw[-2:] != b"\x00\x00":
            raise RconProtocolError("Invalid RCON payload terminator")

        payload = payload_raw[:-2].decode("utf-8", errors="replace")
        return req_id, ptype, payload

    async def auth(self) -> None:
        await self.connect()

        auth_id = self._next_id()
        await self._send_packet(auth_id, RCON_TYPE_AUTH, self.cfg.password)

        # 关键修复：
        # 认证阶段必须等待真正的 AUTH_RESPONSE(type=2) 且 req_id==auth_id
        # 失败通常表现为收到 req_id==-1（很多实现会在 AUTH_RESPONSE 中返回 -1）
        deadline = asyncio.get_running_loop().time() + self.cfg.timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON auth timeout")

            req_id, ptype, _payload = await asyncio.wait_for(self._read_packet(), timeout=remaining)

            # 常见失败信号：req_id = -1（不同实现可能 type=2 或 0，但 -1 基本可判失败）
            if req_id == -1:
                raise RconAuthError("RCON auth failed (bad password?)")

            # 只接受真正的 AUTH_RESPONSE
            if req_id == auth_id and ptype == RCON_TYPE_AUTH_RESPONSE:
                self._authed = True
                return

            # 其余包（比如 RESPONSE_VALUE 噪声）忽略，继续读直到 deadline

    async def ensure_ready(self) -> None:
        if not self.connected:
            await self.connect()
        if not self.authed:
            await self.auth()

    async def exec(self, command: str) -> str:
        await self.ensure_ready()

        cmd_id = self._next_id()
        end_id = self._next_id()

        await self._send_packet(cmd_id, RCON_TYPE_EXECCOMMAND, command)
        # 终止包（空命令）：用于判定多包响应结束
        await self._send_packet(end_id, RCON_TYPE_EXECCOMMAND, "")

        chunks: list[str] = []
        deadline = asyncio.get_running_loop().time() + self.cfg.timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RconError("RCON command response timeout")

            req_id, _ptype, payload = await asyncio.wait_for(self._read_packet(), timeout=remaining)

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

        self._ready = False
        self._rcon_lock = asyncio.Lock()

        # 使用插件专属数据目录（符合 AstrBot 规范）
        data_dir: Path = StarTools.get_data_dir(self.plugin_name)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = data_dir / "config.yml"

        # 连接复用
        self._client: Optional[AsyncRconClient] = None

    def _default_config(self) -> dict:
        return {
            "admins": [111, 222, 333],
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

    def _build_rcon_cfg(self) -> Optional[RconConfig]:
        r = self.config.get("rcon") or {}
        host = r.get("host")
        port = r.get("port")
        password = r.get("password")
        timeout = r.get("timeout", DEFAULT_TIMEOUT)

        if not host or not port or not password:
            return None

        if str(password).strip() == "CHANGE_ME":
            return None

        return RconConfig(
            host=str(host),
            port=int(port),
            password=str(password),
            timeout=float(timeout),
        )

    async def initialize(self):
        try:
            if not self._config_path.exists():
                self._write_default_config()
                logger.warning(
                    "[minecraftconsole] 未找到 config.yml，已在插件数据目录生成默认配置：%s。"
                    "请修改 admins 与 rcon.password（不要留 CHANGE_ME），然后重启插件。",
                    str(self._config_path),
                )
                self._ready = False
                return

            self.config = self._load_config()

            admins = self.config.get("admins", [])
            if not isinstance(admins, list) or not admins:
                logger.error("[minecraftconsole] 配置错误：admins 必须是非空列表")
                self._ready = False
                return

            self.rcon_cfg = self._build_rcon_cfg()
            if not self.rcon_cfg:
                logger.error(
                    "[minecraftconsole] 配置错误：rcon.host/port/password 必填且 password 不能为 CHANGE_ME"
                )
                self._ready = False
                return

            # 初始化复用 client（此处不强制连接，避免启动即阻塞/失败）
            self._client = AsyncRconClient(self.rcon_cfg)

            self._ready = True
            logger.info("[minecraftconsole] 配置加载完成，插件已就绪。config=%s", str(self._config_path))

        except Exception as e:
            logger.error("[minecraftconsole] 初始化失败：%s", e, exc_info=True)
            self._ready = False

    def _is_admin(self, user_id) -> bool:
        if user_id is None:
            return False
        admins = {str(x) for x in self.config.get("admins", [])}
        return str(user_id) in admins

    def _truncate_output(self, text: str) -> str:
        if len(text) <= MAX_CHAT_OUTPUT:
            return text
        return text[:MAX_CHAT_OUTPUT] + f"\n...（已截断，原长度 {len(text)} 字符）"

    async def _get_client(self) -> AsyncRconClient:
        if not self.rcon_cfg:
            raise RconError("RCON config missing")
        if self._client is None or self._client.cfg != self.rcon_cfg:
            self._client = AsyncRconClient(self.rcon_cfg)
        return self._client

    @filter.command("mc-command")
    async def mc_command(self, event: AstrMessageEvent):
        if not self._ready:
            yield event.plain_result("⚠️ 插件未就绪：请检查插件数据目录下的 config.yml 并重启插件")
            return

        user_id = event.get_sender_id()
        if not self._is_admin(user_id):
            yield event.plain_result("❌ 你没有权限使用该指令")
            return

        message_str = (event.message_str or "").strip()
        parts = message_str.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            yield event.plain_result("用法：/mc-command <MC命令>")
            return

        command = parts[1].strip()

        async with self._rcon_lock:
            client = await self._get_client()

            try:
                # 如果连接断了/未认证，会在这里自动重连+认证
                result = await client.exec(command)
                if not result:
                    result = "(无输出)"

                result = self._truncate_output(result)
                yield event.plain_result(f"✅ 已执行：{command}\n📤 返回：{result}")

            except RconAuthError:
                # 认证失败时，强制重建连接（避免半死状态）
                await client.close()
                self._client = AsyncRconClient(self.rcon_cfg) if self.rcon_cfg else None
                yield event.plain_result("❌ RCON 认证失败：请检查 config.yml 的 rcon.password")

            except Exception as e:
                logger.error("RCON 执行失败：%s", e, exc_info=True)
                # 发生网络异常时，关掉旧连接，下一次自动重连
                try:
                    await client.close()
                except Exception:
                    pass
                self._client = AsyncRconClient(self.rcon_cfg) if self.rcon_cfg else None
                yield event.plain_result("❌ RCON 执行失败：请检查服务器地址/端口/防火墙/enable-rcon")

    async def terminate(self):
        try:
            if self._client:
                await self._client.close()
        except Exception:
            pass
        logger.info("[minecraftconsole] 插件已卸载/停用")
