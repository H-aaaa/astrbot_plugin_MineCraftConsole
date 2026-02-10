"""消息格式化模块"""

from __future__ import annotations


class MessageFormatter:
    @staticmethod
    def format_exec_result(command: str, output: str) -> str:
        return f"✅ 已执行：{command}\n📤 返回：{output}"

    @staticmethod
    def format_no_permission() -> str:
        return "❌ 你没有权限使用该指令"

    @staticmethod
    def format_not_enabled() -> str:
        return "❌ 插件未启用"

    @staticmethod
    def format_not_configured() -> str:
        return "⚠️ RCON 未配置：请在插件配置中填写 rcon_password"

    @staticmethod
    def format_usage() -> str:
        return "用法：/mc-command <MC命令>"

    @staticmethod
    def format_auth_failed() -> str:
        return "❌ RCON 认证失败：请检查 rcon_password"

    @staticmethod
    def format_exec_failed() -> str:
        return "❌ RCON 执行失败：请检查 host/port/防火墙/enable-rcon"
