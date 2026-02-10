"""配置管理模块"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MinecraftConsoleConfig:
    """Minecraft RCON 控制台插件配置"""

    enabled: bool = True

    # 权限
    admins: list[str] = field(default_factory=list)

    # RCON
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = ""
    timeout: float = 5.0

    # 输出控制
    max_output: int = 1500

    def __post_init__(self):
        self.admins = self._parse_list(self.admins)

    @staticmethod
    def _parse_list(value) -> list[str]:
        """解析列表配置（支持字符串多行格式，每行一个）"""
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [
                line.strip()
                for line in value.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
        return []

    @classmethod
    def from_dict(cls, config: dict) -> MinecraftConsoleConfig:
        """从字典创建配置对象（只取声明过的字段）"""
        return cls(**{k: v for k, v in config.items() if k in cls.__annotations__})

    @property
    def is_rcon_ready(self) -> bool:
        return bool(self.rcon_host and self.rcon_port and self.rcon_password.strip())
