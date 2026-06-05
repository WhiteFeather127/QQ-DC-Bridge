from dataclasses import dataclass, field


@dataclass
class DiscordConfig:
    """Discord 平台连接配置."""
    token: str
    channel_id: str
    debug_channel_id: str | None = None
    proxy: str | None = None


@dataclass
class QQConfig:
    """QQ 平台连接配置."""
    bot_qq: int
    group_id: int
    onebot_ws_url: str
    debug_group_id: int | None = None
    proxy: str | None = None


@dataclass
class DeepSeekConfig:
    """DeepSeek 翻译引擎配置."""
    api_key: str
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"


@dataclass
class BridgeConfig:
    """桥接引擎运行时配置."""
    max_segments_per_msg: int = 20
    qq_rate_limit: float = 1.0
    translation_timeout: int = 10
    log_level: str = "INFO"
    data_dir: str = "./data"


@dataclass
class AppConfig:
    """应用顶层配置，聚合所有子配置."""
    discord: DiscordConfig
    qq: QQConfig
    deepseek: DeepSeekConfig
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
