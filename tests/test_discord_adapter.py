from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

mock_discord = MagicMock()
mock_discord.Client = MagicMock
sys.modules["discord"] = mock_discord

from src.adapters.discord.adapter import DiscordAdapter
from src.bridge.segment.base import MessageSegment
from src.bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_AT_ALL,
    SEGMENT_EMOJI,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_STICKER,
    SEGMENT_TEXT,
)


@pytest.fixture
def adapter() -> DiscordAdapter:
    return DiscordAdapter(token="test_token", channel_id="123456")


@pytest.mark.asyncio
async def test_send_message(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = AsyncMock()
    mock_message = MagicMock()
    mock_message.id = 98765
    mock_channel.send = AsyncMock(return_value=mock_message)
    mock_client.get_channel = MagicMock(return_value=mock_channel)

    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Hello"})]
    result = await adapter.send_message("123456", segments)

    assert result == "98765"
    mock_channel.send.assert_awaited_once_with(content="Hello")


@pytest.mark.asyncio
async def test_send_message_with_reply(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = AsyncMock()
    mock_message = MagicMock()
    mock_message.id = 98766
    mock_channel.send = AsyncMock(return_value=mock_message)
    mock_client.get_channel = MagicMock(return_value=mock_channel)

    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Reply text"})]
    result = await adapter.send_message("123456", segments, reply_to="555")

    assert result == "98766"
    mock_channel.send.assert_awaited_once_with(content="Reply text", reference=555)


@pytest.mark.asyncio
async def test_send_message_fetch_channel_fallback(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = AsyncMock()
    mock_message = MagicMock()
    mock_message.id = 98767
    mock_channel.send = AsyncMock(return_value=mock_message)
    mock_client.get_channel = MagicMock(return_value=None)
    mock_client.fetch_channel = AsyncMock(return_value=mock_channel)

    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Fallback"})]
    result = await adapter.send_message("123456", segments)

    assert result == "98767"
    mock_client.fetch_channel.assert_awaited_once_with(123456)
    mock_channel.send.assert_awaited_once_with(content="Fallback")


@pytest.mark.asyncio
async def test_edit_message(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = AsyncMock()
    mock_message = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_message)
    mock_client.get_channel = MagicMock(return_value=mock_channel)

    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Edited content"})]
    await adapter.edit_message("123456", "111", segments)

    mock_message.edit.assert_awaited_once_with(content="Edited content")


@pytest.mark.asyncio
async def test_edit_message_fetch_channel_fallback(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = AsyncMock()
    mock_message = AsyncMock()
    mock_channel.fetch_message = AsyncMock(return_value=mock_message)
    mock_client.get_channel = MagicMock(return_value=None)
    mock_client.fetch_channel = AsyncMock(return_value=mock_channel)

    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Edit fallback"})]
    await adapter.edit_message("123456", "111", segments)

    mock_client.fetch_channel.assert_awaited_once_with(123456)
    mock_message.edit.assert_awaited_once_with(content="Edit fallback")


@pytest.mark.asyncio
async def test_list_members(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = MagicMock()
    mock_guild = MagicMock()

    mock_member1 = MagicMock()
    mock_member1.id = 111
    mock_member1.display_name = "User1"

    mock_member2 = MagicMock()
    mock_member2.id = 222
    mock_member2.display_name = "User2"

    mock_guild.members = [mock_member1, mock_member2]
    mock_channel.guild = mock_guild
    mock_client.get_channel = MagicMock(return_value=mock_channel)

    result = await adapter.list_members("123456")

    assert result == {"111": "User1", "222": "User2"}


@pytest.mark.asyncio
async def test_list_members_fetch_channel_fallback(adapter: DiscordAdapter) -> None:
    mock_client = MagicMock()
    adapter._client = mock_client

    mock_channel = MagicMock()
    mock_guild = MagicMock()

    mock_member = MagicMock()
    mock_member.id = 333
    mock_member.display_name = "Alice"

    mock_guild.members = [mock_member]
    mock_channel.guild = mock_guild
    mock_client.get_channel = MagicMock(return_value=None)
    mock_client.fetch_channel = AsyncMock(return_value=mock_channel)

    result = await adapter.list_members("123456")

    assert result == {"333": "Alice"}
    mock_client.fetch_channel.assert_awaited_once_with(123456)


def test_segments_to_string_text() -> None:
    segments = [MessageSegment(type=SEGMENT_TEXT, data={"text": "Hello world"})]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "Hello world"


def test_segments_to_string_image() -> None:
    segments = [
        MessageSegment(type=SEGMENT_IMAGE, data={"file": "https://example.com/img.png"})
    ]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "https://example.com/img.png"


def test_segments_to_string_at() -> None:
    segments = [
        MessageSegment(
            type=SEGMENT_AT, data={"platform": "discord", "user_id": "12345", "display": "User"}
        )
    ]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "<@12345>"


def test_segments_to_string_at_all() -> None:
    segments = [MessageSegment(type=SEGMENT_AT_ALL, data={})]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "@everyone"


def test_segments_to_string_emoji() -> None:
    segments = [MessageSegment(type=SEGMENT_EMOJI, data={"unicode": "😀"})]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "😀"


def test_segments_to_string_reply() -> None:
    segments = [
        MessageSegment(
            type=SEGMENT_REPLY,
            data={"platform": "discord", "msg_id": "999", "content": "Quoted text"},
        )
    ]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "Quoted text"


def test_segments_to_string_sticker() -> None:
    segments = [
        MessageSegment(
            type=SEGMENT_STICKER,
            data={"name": "wave", "url": "https://example.com/sticker.png"},
        )
    ]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "https://example.com/sticker.png"


def test_segments_to_string_mixed() -> None:
    segments = [
        MessageSegment(type=SEGMENT_TEXT, data={"text": "Hello"}),
        MessageSegment(type=SEGMENT_IMAGE, data={"file": "https://example.com/img.png"}),
        MessageSegment(type=SEGMENT_EMOJI, data={"unicode": "🔥"}),
    ]
    result = DiscordAdapter._segments_to_string(segments)
    assert result == "Hello https://example.com/img.png 🔥"


def test_segments_to_string_empty() -> None:
    result = DiscordAdapter._segments_to_string([])
    assert result == ""
