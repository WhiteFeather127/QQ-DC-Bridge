import os
from pathlib import Path

import yaml

from models.config_model import (
    AppConfig,
    BridgeConfig,
    DeepSeekConfig,
    DiscordConfig,
    QQConfig,
)


def load_config(config_path: str) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    discord_cfg = DiscordConfig(
        token=_env_or("PHOBOS_DISCORD_TOKEN", raw["discord"]["token"]),
        channel_id=str(raw["discord"]["channel_id"]),
        debug_channel_id=str(raw["discord"]["debug_channel_id"]) if raw["discord"].get("debug_channel_id") else None,
        proxy=raw["discord"].get("proxy", None),
    )

    qq_cfg = QQConfig(
        bot_qq=int(_env_or("PHOBOS_QQ_ACCOUNT", str(raw["qq"]["bot_qq"]))),
        group_id=int(raw["qq"]["group_id"]),
        debug_group_id=int(raw["qq"]["debug_group_id"]) if raw["qq"].get("debug_group_id") else None,
        onebot_ws_url=str(raw["qq"]["onebot_ws_url"]),
    )

    deepseek_cfg = DeepSeekConfig(
        api_key=_env_or("PHOBOS_DEEPSEEK_KEY", raw["deepseek"]["api_key"]),
        api_base=str(raw["deepseek"].get("api_base", "https://api.deepseek.com")),
        model=str(raw["deepseek"].get("model", "deepseek-chat")),
    )

    bridge_raw = raw.get("bridge", {})
    bridge_cfg = BridgeConfig(
        max_segments_per_msg=int(bridge_raw.get("max_segments_per_msg", 20)),
        qq_rate_limit=float(bridge_raw.get("qq_rate_limit", 1.0)),
        translation_timeout=int(bridge_raw.get("translation_timeout", 10)),
        log_level=str(bridge_raw.get("log_level", "INFO")),
        data_dir=str(bridge_raw.get("data_dir", "./data")),
    )

    return AppConfig(
        discord=discord_cfg,
        qq=qq_cfg,
        deepseek=deepseek_cfg,
        bridge=bridge_cfg,
    )


def _env_or(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)
