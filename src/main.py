import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure src/ is on sys.path so bare imports (from config, from bridge, etc.) work
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import load_config
from adapters.discord.adapter import DiscordAdapter
from adapters.qq.adapter import QQAdapter
from bridge.translator import Translator
from bridge.matcher import UserMatcher
from bridge.message_store import MessageStore
from bridge.segment.converter import SegmentConverter
from bridge.orchestrator import Orchestrator
from utils.logger import JSONFormatter, DailyRotatingFileHandler, suppress_discord_reconnect_traceback


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QQ-DC-Bridge - Cross-platform message bridge",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="data/config.yaml",
        help="Path to configuration file (default: data/config.yaml)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug output: heartbeat, message flow, send status",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    config = load_config(args.config)

    log_level_val = getattr(logging, config.bridge.log_level.upper(), logging.INFO)

    if args.debug:
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
    else:
        handler = DailyRotatingFileHandler(
            base_dir="logs",
            name="phobos",
        )

    handler.setFormatter(JSONFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_val)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    suppress_discord_reconnect_traceback()

    if args.debug and config.discord.debug_channel_id:
        dc_channel_id = config.discord.debug_channel_id
    else:
        dc_channel_id = config.discord.channel_id

    if args.debug and config.qq.debug_group_id:
        qq_gid = config.qq.debug_group_id
    else:
        qq_gid = config.qq.group_id

    discord_adapter = DiscordAdapter(
        token=config.discord.token,
        channel_id=dc_channel_id,
        proxy=config.discord.proxy,
    )

    qq_adapter = QQAdapter(
        bot_qq=config.qq.bot_qq,
        group_id=qq_gid,
        onebot_ws_url=config.qq.onebot_ws_url,
    )

    translator = Translator(config.deepseek)

    matcher = UserMatcher()

    converter = SegmentConverter()
    matcher.register_converter_rules(converter)

    message_store = MessageStore(data_dir=config.bridge.data_dir)

    orchestrator = Orchestrator(config.bridge, message_store, debug=args.debug)
    orchestrator.translator = translator
    orchestrator.matcher = matcher
    orchestrator.converter = converter

    await orchestrator.register_adapters(
        discord_adapter=discord_adapter,
        qq_adapter=qq_adapter,
        discord_channel_id=dc_channel_id,
        qq_group_id=str(qq_gid),
    )

    logger = logging.getLogger("phobos")
    logger.info(
        "QQ-DC-Bridge starting",
        extra={"event": "startup"},
    )

    try:
        await orchestrator.start()
    except asyncio.CancelledError:
        logger.info("Shutdown requested")
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        await orchestrator.stop()
        logger.info("QQ-DC-Bridge stopped")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
