"""辅助工具模块"""

from __future__ import annotations


def parse_command_args(event_message_str: str, command: str) -> str | None:
    """
    解析指令参数，兼容以下输入：
    1) "/mc-command say hello"
    2) "mc-command say hello"
    3) "say hello"（框架已剥离命令名）

    返回：只包含参数部分，例如 "say hello"
    """
    s = " ".join((event_message_str or "").split())
    if not s:
        return None

    # 兼容全角斜杠
    if s.startswith("／"):
        s = "/" + s[1:]

    cmd = command.strip().lstrip("/").lower()
    s_lower = s.lower()

    # 情况1：以 "/command" 开头
    prefix1 = f"/{cmd}"
    if s_lower.startswith(prefix1):
        args = s[len(prefix1):].strip()
        return args if args else None

    # 情况2：以 "command" 开头（无斜杠）
    prefix2 = cmd
    if s_lower.startswith(prefix2):
        # 只有完全等于 command 时表示没参数
        if len(s) == len(prefix2):
            return None
        # 如果后面紧跟空格/制表符等，剥离掉
        args = s[len(prefix2):].strip()
        return args if args else None

    # 情况3：框架已剥离命令名，整串就是参数
    return s

def truncate_text(text: str, max_len: int) -> str:
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n...（已截断，原长度 {len(text)} 字符）"
