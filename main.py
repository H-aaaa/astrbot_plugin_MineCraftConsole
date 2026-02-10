"""
AstrBot Minecraft Console Plugin
通过 RCON 执行 Minecraft 指令：/mc-command <command>
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .config import MinecraftConsoleConfig
from .message_formatter import MessageFormatter
from .rcon_client import AsyncRconClient, RconAuthError, RconConfig
from .utils import parse_command_args, truncate_text



@register("minecraftconsole", "MineCraft控制台", "使用RCON发送命令至MC", "1.0.0")
class MinecraftConsole(Star):
    """Minecraft RCON 控制台插件"""

    _instance_running = False
    _instance_lock = asyncio.Lock()

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.context = context
        self.config = MinecraftConsoleConfig.from_dict(config)
        self.formatter = MessageFormatter()

        self._rcon_lock = asyncio.Lock()
        self._client: AsyncRconClient | None = None
        self._client_cfg: RconConfig | None = None

        # 启动插件（仿照示例：不阻塞 __init__，用 task 启动）
        if not self.config.enabled:
            logger.info("[MC-RCON] 插件未启用")
        elif not self.config.is_rcon_ready:
            logger.warning("[MC-RCON] 未配置 rcon_password，/mc-command 暂不可用")
        else:
            asyncio.create_task(self._safe_start())

    async def _safe_start(self):
        """安全启动，防止重复启动（仿照示例插件的写法）"""
        async with MinecraftConsole._instance_lock:
            if MinecraftConsole._instance_running:
                logger.warning("[MC-RCON] 已有实例在运行，跳过启动")
                return
            MinecraftConsole._instance_running = True

        logger.info("[MC-RCON] 插件已启用，准备就绪")
        self._ensure_client()

    def _ensure_client(self) -> None:
        """根据配置构建/更新 RCON Client（连接在第一次执行命令时建立）"""
        if not self.config.is_rcon_ready:
            self._client = None
            self._client_cfg = None
            return

        cfg = RconConfig(
            host=self.config.rcon_host,
            port=int(self.config.rcon_port),
            password=self.config.rcon_password,
            timeout=float(self.config.timeout),
        )

        if self._client_cfg != cfg:
            self._client_cfg = cfg
            self._client = AsyncRconClient(cfg)

    def _is_admin(self, user_id) -> bool:
        if user_id is None:
            return False
        return str(user_id) in {str(x) for x in (self.config.admins or [])}

    @filter.command("mc-command")
    async def mc_command(self, event: AstrMessageEvent):
        """执行 MC 指令：/mc-command <command>"""
        logger.info(f"[MC-RCON] raw event.message_str = {event.message_str!r}")
        if not self.config.enabled:
            yield event.plain_result(self.formatter.format_not_enabled())
            return

        if not self._is_admin(event.get_sender_id()):
            yield event.plain_result(self.formatter.format_no_permission())
            return

        args = parse_command_args(event.message_str, "mc-command")
        if not args:
            yield event.plain_result(self.formatter.format_usage())
            return

        # 确保 client 已按最新配置创建
        self._ensure_client()
        if self._client is None or self._client_cfg is None:
            yield event.plain_result(self.formatter.format_not_configured())
            return

        async with self._rcon_lock:
            try:
                output = await self._client.exec(args)
                output = output if output else "(无输出)"
                output = truncate_text(output, int(self.config.max_output))
                yield event.plain_result(self.formatter.format_exec_result(args, output))

            except RconAuthError:
                # 认证失败时重建 client，避免半死连接
                try:
                    await self._client.close()
                except Exception:
                    pass
                self._client = AsyncRconClient(self._client_cfg)
                yield event.plain_result(self.formatter.format_auth_failed())

            except Exception as e:
                logger.error(f"[MC-RCON] 执行失败: {e}", exc_info=True)
                try:
                    await self._client.close()
                except Exception:
                    pass
                self._client = AsyncRconClient(self._client_cfg)
                yield event.plain_result(self.formatter.format_exec_failed())

    async def terminate(self):
        """插件停止时调用"""
        logger.info("[MC-RCON] 正在停止插件...")

        async with MinecraftConsole._instance_lock:
            MinecraftConsole._instance_running = False

        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass

        logger.info("[MC-RCON] 已停止")
