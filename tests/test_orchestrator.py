from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from adapters.base import MessageEvent, PlatformAdapter
from bridge.bind_manager import BindManager
from bridge.matcher import UserMatcher
from bridge.orchestrator import DIR_DISCORD_TO_QQ, DIR_QQ_TO_DISCORD, PendingTranslation, Orchestrator
from bridge.segment.base import MessageSegment
from bridge.segment.converter import SegmentConverter
from bridge.segment.types import at_segment, text_segment
from bridge.translator import Translator
from bridge.verification import VerificationManager
from models.config_model import BridgeConfig


@pytest.fixture
def bridge_config(tmp_path) -> BridgeConfig:
    return BridgeConfig(data_dir=str(tmp_path / "data"))


@pytest.fixture
def mock_discord_adapter() -> MagicMock:
    adapter = MagicMock(spec=PlatformAdapter)
    adapter.send_message = AsyncMock(return_value="discord_msg_1")
    adapter.edit_message = AsyncMock()
    adapter.set_on_message = MagicMock()
    adapter.start = AsyncMock()
    adapter.stop = AsyncMock()
    return adapter


@pytest.fixture
def mock_qq_adapter() -> MagicMock:
    adapter = MagicMock(spec=PlatformAdapter)
    adapter.send_message = AsyncMock(return_value="qq_msg_1")
    adapter.set_on_message = MagicMock()
    adapter.start = AsyncMock()
    adapter.stop = AsyncMock()
    adapter.bot_user_id = None
    return adapter


@pytest.fixture
def mock_translator() -> MagicMock:
    translator = MagicMock(spec=Translator)
    translator.should_skip = MagicMock(return_value=False)
    translator.translate = AsyncMock(return_value="translated text")
    translator.extract_text_segments = MagicMock(
        side_effect=lambda segments: (
            [i for i, s in enumerate(segments) if s.type == "text"],
            "".join(s.data.get("text", "") for s in segments if s.type == "text"),
        )
    )
    return translator


@pytest.fixture
def mock_converter() -> MagicMock:
    converter = MagicMock(spec=SegmentConverter)
    converter.convert_all = MagicMock(side_effect=lambda direction, segments, context=None: list(segments))
    return converter


@pytest.fixture
def mock_message_store() -> MagicMock:
    store = MagicMock()
    store.get_counterpart = MagicMock(return_value=None)
    store.record = MagicMock()
    return store


def make_qq_event(
    message_id: str = "qq_1",
    author_name: str = "QQUser",
    segments: list | None = None,
) -> MessageEvent:
    return MessageEvent(
        message_id=message_id,
        platform="qq",
        channel_id="qq_group_1",
        author_id="12345",
        author_name=author_name,
        segments=segments or [text_segment("Hello from QQ")],
    )


def make_discord_event(
    message_id: str = "dc_1",
    author_name: str = "DiscordUser",
    segments: list | None = None,
) -> MessageEvent:
    return MessageEvent(
        message_id=message_id,
        platform="discord",
        channel_id="dc_channel_1",
        author_id="67890",
        author_name=author_name,
        segments=segments or [text_segment("Hello from Discord")],
    )


class TestHandleQQMessage:
    @pytest.mark.asyncio
    async def test_no_translation_skipped(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = True
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_discord_adapter.send_message.assert_awaited_once()
        args, _ = mock_discord_adapter.send_message.await_args
        channel = args[0]
        segments = args[1]
        assert channel == "dc_channel_1"
        assert segments[0].data["text"] == "`QQUser`: "
        assert segments[1].data["text"] == "Hello from QQ"

        mock_translator.translate.assert_not_awaited()
        assert event.message_id not in orch._pending

    @pytest.mark.asyncio
    async def test_with_translation_success(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = "你好"
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_discord_adapter.send_message.assert_awaited_once()
        mock_translator.translate.assert_awaited_once_with("Hello from QQ", target_lang="英文")
        args, _ = mock_discord_adapter.send_message.await_args
        segments = args[1]
        assert len(segments) == 1
        assert "QQUser" in segments[0].data["text"]
        assert "你好" in segments[0].data["text"]
        assert "Hello from QQ" in segments[0].data["text"]

    @pytest.mark.asyncio
    async def test_with_translation_failure(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = None
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_discord_adapter.send_message.assert_awaited_once()
        mock_translator.translate.assert_awaited_once_with("Hello from QQ", target_lang="英文")
        args, _ = mock_discord_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "`QQUser`: "
        assert segments[1].data["text"] == "Hello from QQ"

    @pytest.mark.asyncio
    async def test_distrans_skips_translation_and_sends_original(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("/distrans Hello"),
        ])
        await orch.handle_qq_message(event)

        mock_translator.translate.assert_not_awaited()
        mock_discord_adapter.send_message.assert_awaited_once()
        args, _ = mock_discord_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "`QQUser`: "
        assert segments[1].data["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_identical_translation_is_not_output(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = "Hello from QQ"
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_translator.translate.assert_awaited_once_with("Hello from QQ", target_lang="英文")
        args, _ = mock_discord_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "`QQUser`: "
        assert segments[1].data["text"] == "Hello from QQ"

    @pytest.mark.asyncio
    async def test_identical_translation_ignores_unicode_normalization_and_whitespace(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = "Hello　from\nQQ"
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_translator.translate.assert_awaited_once_with("Hello from QQ", target_lang="英文")
        mock_discord_adapter.send_message.assert_awaited_once()
        args, _ = mock_discord_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "`QQUser`: "
        assert segments[1].data["text"] == "Hello from QQ"

    @pytest.mark.asyncio
    async def test_no_translator_skips_translation(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.bot_user_id = "12345"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = None
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_discord_adapter.send_message.assert_awaited_once()
        assert event.message_id not in orch._pending

    @pytest.mark.asyncio
    async def test_send_message_returns_none(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.bot_user_id = "12345"
        mock_discord_adapter.send_message.return_value = None

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch._discord_channel_id = "dc_channel_1"

        event = make_qq_event(segments=[
            at_segment(platform="qq", user_id="12345", display="Bot"),
            text_segment("Hello from QQ"),
        ])
        await orch.handle_qq_message(event)

        mock_discord_adapter.send_message.assert_awaited_once()
        mock_translator.translate.assert_not_awaited()
        assert event.message_id not in orch._pending

    @pytest.mark.asyncio
    async def test_no_discord_adapter_returns_early(
        self,
        bridge_config: BridgeConfig,
        mock_translator: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = None
        orch.translator = mock_translator

        event = make_qq_event()
        await orch.handle_qq_message(event)

        mock_translator.extract_text_segments.assert_not_called()


class TestHandleDiscordMessage:
    @pytest.mark.asyncio
    async def test_no_translation_skipped(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = True

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        args, _ = mock_qq_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "DiscordUser："
        assert len(segments) == 2
        assert segments[1].data["text"] == "Hello from Discord"

        mock_translator.translate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_translation_success(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = "你好"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        args, _ = mock_qq_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "DiscordUser：你好\n└─ "
        assert segments[1].data["text"] == "Hello from Discord"

        mock_translator.translate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_translation_failure(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = None

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        args, _ = mock_qq_adapter.send_message.await_args
        segments = args[1]
        assert len(segments) == 2
        assert segments[0].data["text"] == "DiscordUser："
        assert segments[1].data["text"] == "Hello from Discord"

        mock_translator.translate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_identical_translation_is_not_output_in_discord_to_qq(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_converter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.should_skip.return_value = False
        mock_translator.translate.return_value = "Hello from Discord"

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch.converter = mock_converter
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        mock_translator.translate.assert_awaited_once()
        args, _ = mock_qq_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "DiscordUser："
        assert segments[1].data["text"] == "Hello from Discord"

    @pytest.mark.asyncio
    async def test_no_text_segments_sends_without_translation(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_translator: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_translator.extract_text_segments.return_value = ([], "")

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = mock_translator
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event(segments=[
            MessageSegment(type="image", data={"file": "pic.png"}),
        ])
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        mock_translator.translate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_translator_skips_translation(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.translator = None
        orch._qq_group_id = "qq_group_1"

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_qq_adapter.send_message.assert_awaited_once()
        args, _ = mock_qq_adapter.send_message.await_args
        segments = args[1]
        assert segments[0].data["text"] == "DiscordUser："

    @pytest.mark.asyncio
    async def test_no_qq_adapter_returns_early(
        self,
        bridge_config: BridgeConfig,
        mock_translator: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = None
        orch.translator = mock_translator

        event = make_discord_event()
        await orch.handle_discord_message(event)

        mock_translator.extract_text_segments.assert_not_called()


class TestOnTranslationComplete:
    @pytest.mark.asyncio
    async def test_qq_to_discord_edits_message(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)

        original_segments = [
            text_segment("[QQ] User: "),
            text_segment("Hello"),
        ]

        pending = PendingTranslation(
            source_adapter=mock_discord_adapter,
            target_channel_id="dc_channel_1",
            message_id="discord_msg_1",
            original_segments=original_segments,
            text_indices=[0, 1],
            direction=DIR_QQ_TO_DISCORD,
            event_id="event_1",
            author_name="User",
            original_text="Hello",
        )
        pending.translated_text = "你好"

        await orch._on_translation_complete(pending)

        mock_discord_adapter.edit_message.assert_awaited_once()
        args, _ = mock_discord_adapter.edit_message.await_args
        assert args[0] == "dc_channel_1"
        assert args[1] == "discord_msg_1"
        assert len(args[2]) == 1
        assert "User" in args[2][0].data["text"]
        assert "你好" in args[2][0].data["text"]

    @pytest.mark.asyncio
    async def test_qq_to_discord_no_translation_returns_early(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)

        pending = PendingTranslation(
            source_adapter=mock_discord_adapter,
            target_channel_id="dc_channel_1",
            message_id="discord_msg_1",
            original_segments=[text_segment("[QQ] User: ")],
            text_indices=[0],
            direction=DIR_QQ_TO_DISCORD,
            event_id="event_2",
        )

        await orch._on_translation_complete(pending)

        mock_discord_adapter.edit_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discord_to_qq_is_noop(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)

        pending = PendingTranslation(
            source_adapter=mock_qq_adapter,
            target_channel_id="qq_group_1",
            message_id="qq_msg_1",
            original_segments=[text_segment("[Discord] User: ")],
            text_indices=[0],
            direction=DIR_DISCORD_TO_QQ,
            event_id="event_3",
        )
        pending.translated_text = "你好"

        await orch._on_translation_complete(pending)

        mock_qq_adapter.edit_message.assert_not_awaited()
        mock_qq_adapter.send_message.assert_not_awaited()


class TestRegisterAdapters:
    @pytest.mark.asyncio
    async def test_registers_adapters_and_callbacks(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)

        await orch.register_adapters(
            discord_adapter=mock_discord_adapter,
            qq_adapter=mock_qq_adapter,
            discord_channel_id="dc_channel_1",
            qq_group_id="qq_group_1",
        )

        assert orch.discord_adapter is mock_discord_adapter
        assert orch.qq_adapter is mock_qq_adapter
        assert orch._discord_channel_id == "dc_channel_1"
        assert orch._qq_group_id == "qq_group_1"

        mock_discord_adapter.set_on_message.assert_called_once_with(orch.handle_discord_message)
        mock_qq_adapter.set_on_message.assert_called_once_with(orch.handle_qq_message)


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_starts_both_adapters(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter

        await orch.start()

        mock_discord_adapter.start.assert_awaited_once()
        mock_qq_adapter.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_stops_both_adapters(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter

        await orch.stop()

        mock_discord_adapter.stop.assert_awaited_once()
        mock_qq_adapter.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_with_none_adapters_does_not_raise(
        self,
        bridge_config: BridgeConfig,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = None
        orch.qq_adapter = None

        await orch.start()

    @pytest.mark.asyncio
    async def test_stop_with_none_adapters_does_not_raise(
        self,
        bridge_config: BridgeConfig,
        mock_message_store: MagicMock,
    ) -> None:
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = None
        orch.qq_adapter = None

        await orch.stop()


class TestHandleBindCommand:
    @pytest.mark.asyncio
    async def test_bind_from_qq(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_discord_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter

        event = MessageEvent(
            message_id="pv_1",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/bind DiscordUser")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "格式错误" in call_text

    @pytest.mark.asyncio
    async def test_bind_from_discord_with_qq_number(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_discord_adapter.send_dm = AsyncMock(return_value=True)
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        matcher = MagicMock(spec=UserMatcher)
        matcher.has_user = MagicMock(return_value=True)

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.matcher = matcher

        event = MessageEvent(
            message_id="pv_2",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment("/bind 10001")],
        )
        await orch.handle_private_message(event)

        assert mock_discord_adapter.send_dm.await_count >= 1
        call_text = mock_discord_adapter.send_dm.call_args.args[1]
        assert "verification" in call_text.lower()

    @pytest.mark.asyncio
    async def test_bind_rejected_already_bound(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter

        event = MessageEvent(
            message_id="pv_3",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/bind DiscordUser")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "格式错误" in call_text

    @pytest.mark.asyncio
    async def test_bind_target_not_found(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter

        event = MessageEvent(
            message_id="pv_4",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/bind NonExistentUser")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "格式错误" in call_text

    @pytest.mark.asyncio
    async def test_bind_invalid_format(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter

        event = MessageEvent(
            message_id="pv_5",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/bind")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "格式错误" in call_text


class TestHandleUnbindCommand:
    @pytest.mark.asyncio
    async def test_unbind_from_qq(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter
        orch._bind_manager.bind(qq_id="10001", discord_id="discord_user_1")

        event = MessageEvent(
            message_id="pv_6",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/unbind")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "解绑" in call_text
        assert not orch._bind_manager.is_bound("qq", "10001")

    @pytest.mark.asyncio
    async def test_unbind_from_discord(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_discord_adapter.send_dm = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch._bind_manager.bind(qq_id="10001", discord_id="discord_user_1")

        event = MessageEvent(
            message_id="pv_7",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment("/unbind")],
        )
        await orch.handle_private_message(event)

        mock_discord_adapter.send_dm.assert_called_once()
        call_text = mock_discord_adapter.send_dm.call_args.args[1]
        assert "Unbound" in call_text
        assert not orch._bind_manager.is_bound("discord", "discord_user_1")

    @pytest.mark.asyncio
    async def test_unbind_not_bound(
        self,
        bridge_config: BridgeConfig,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        orch = Orchestrator(bridge_config, mock_message_store)
        orch.qq_adapter = mock_qq_adapter

        event = MessageEvent(
            message_id="pv_8",
            platform="qq",
            channel_id="",
            author_id="10001",
            author_name="QQUser",
            is_private=True,
            segments=[text_segment("/unbind")],
        )
        await orch.handle_private_message(event)

        mock_qq_adapter.send_private_msg.assert_called_once()
        call_text = mock_qq_adapter.send_private_msg.call_args.args[1]
        assert "尚未绑定" in call_text


class TestHandleVerificationReply:
    @pytest.mark.asyncio
    async def test_verification_success_then_bind(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_qq_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_discord_adapter.send_dm = AsyncMock(return_value=True)
        mock_qq_adapter.send_private_msg = AsyncMock(return_value=True)
        matcher = MagicMock(spec=UserMatcher)
        matcher.has_user = MagicMock(return_value=True)

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.qq_adapter = mock_qq_adapter
        orch.matcher = matcher

        code_event = MessageEvent(
            message_id="pv_9",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment("/bind 20001")],
        )
        await orch.handle_private_message(code_event)
        mock_discord_adapter.send_dm.reset_mock()

        pending = list(orch._verification_manager._pending.values())[0]
        verify_code = pending.code

        verify_event = MessageEvent(
            message_id="pv_10",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment(verify_code)],
        )
        await orch.handle_private_message(verify_event)

        assert mock_discord_adapter.send_dm.await_count >= 1
        call_text = mock_discord_adapter.send_dm.call_args.args[1]
        assert "successful" in call_text.lower() or "成功" in call_text
        assert orch._bind_manager.is_bound("discord", "discord_user_1")

    @pytest.mark.asyncio
    async def test_verification_wrong_code(
        self,
        bridge_config: BridgeConfig,
        mock_discord_adapter: MagicMock,
        mock_message_store: MagicMock,
    ) -> None:
        mock_discord_adapter.send_dm = AsyncMock(return_value=True)
        matcher = MagicMock(spec=UserMatcher)
        matcher.match_user = MagicMock(return_value=("discord_user_1", "DiscordUser"))
        matcher.has_user = MagicMock(return_value=False)

        orch = Orchestrator(bridge_config, mock_message_store)
        orch.discord_adapter = mock_discord_adapter
        orch.matcher = matcher

        code_event = MessageEvent(
            message_id="pv_11",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment("/bind 10001")],
        )
        await orch.handle_private_message(code_event)
        mock_discord_adapter.send_dm.reset_mock()

        verify_event = MessageEvent(
            message_id="pv_12",
            platform="discord",
            channel_id="",
            author_id="discord_user_1",
            author_name="DiscordUser",
            is_private=True,
            segments=[text_segment("000000")],
        )
        await orch.handle_private_message(verify_event)

        assert mock_discord_adapter.send_dm.await_count >= 1
        call_text = mock_discord_adapter.send_dm.call_args.args[1]
        assert "错误" in call_text or "Invalid" in call_text
        assert not orch._bind_manager.is_bound("discord", "discord_user_1")


class TestResolveAuthorDisplayName:
    def test_bound_user_returns_bound_name(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        bm.bind(qq_id="10001", discord_id="discord_user_1")

        matcher = UserMatcher()
        matcher._cache = {
            "qq": {"10001": "QQUser"},
            "discord": {"discord_user_1": "DiscordUser"},
        }

        orch = Orchestrator.__new__(Orchestrator)
        orch._bind_manager = bm
        orch.matcher = matcher

        result = orch._resolve_author_display_name("qq", "10001", "original_name")
        assert result == "DiscordUser"

    def test_unbound_user_returns_original(self, tmp_path) -> None:
        bm = BindManager(data_dir=str(tmp_path))
        matcher = UserMatcher()

        orch = Orchestrator.__new__(Orchestrator)
        orch._bind_manager = bm
        orch.matcher = matcher

        result = orch._resolve_author_display_name("qq", "10001", "OriginalName")
        assert result == "OriginalName"


class TestParseBindTarget:
    def test_bind_regex_handles_multiple_spaces(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind   DiscordUser", from_platform="qq")
        assert platform is None
        assert ident == ""

    def test_bind_with_qq_prefix(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind QQ:10001", from_platform="discord")
        assert platform == "qq"
        assert ident == "10001"

    def test_bind_with_discord_prefix_from_qq(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind Discord:DiscordUser", from_platform="qq")
        assert platform == "discord"
        assert ident == "DiscordUser"

    def test_bind_empty_after_prefix(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind", from_platform="discord")
        assert platform is None
        assert ident == ""

    def test_bind_just_whitespace(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind   ", from_platform="discord")
        assert platform is None
        assert ident == ""

    def test_bind_digits_from_discord_is_qq(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind 10001", from_platform="discord")
        assert platform == "qq"
        assert ident == "10001"

    def test_bind_digits_from_qq_rejected(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        platform, ident = orch._parse_bind_target("/bind 12345", from_platform="qq")
        assert platform is None
        assert ident == ""
