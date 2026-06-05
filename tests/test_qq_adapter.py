from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.qq.segment_builder import build_cq_code
from src.adapters.qq.segment_parser import parse_cq_code, parse_onebot_array
from src.bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_AT_ALL,
    SEGMENT_EMOJI,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_TEXT,
    SEGMENT_UNSUPPORTED,
    MessageSegment,
    at_all_segment,
    at_segment,
    emoji_segment,
    image_segment,
    reply_segment,
    text_segment,
    unsupported_segment,
)


class TestSegmentParser:
    def test_parse_text_only(self) -> None:
        result = parse_cq_code("hello world")
        assert len(result) == 1
        assert result[0].type == SEGMENT_TEXT
        assert result[0].data["text"] == "hello world"

    def test_parse_face_known(self) -> None:
        result = parse_cq_code("[CQ:face,id=14]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_EMOJI
        assert result[0].data["unicode"] == "\U0001F642"

    def test_parse_face_unknown(self) -> None:
        result = parse_cq_code("[CQ:face,id=99999]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_TEXT
        assert "[表情:" in result[0].data["text"]

    def test_parse_image(self) -> None:
        result = parse_cq_code("[CQ:image,file=abc.jpg]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_IMAGE
        assert result[0].data["file"] == "abc.jpg"

    def test_parse_image_empty_file(self) -> None:
        result = parse_cq_code("[CQ:image,file=]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_UNSUPPORTED

    def test_parse_at(self) -> None:
        result = parse_cq_code("[CQ:at,qq=123456]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_AT
        assert result[0].data["user_id"] == "123456"
        assert result[0].data["platform"] == "qq"

    def test_parse_at_all(self) -> None:
        result = parse_cq_code("[CQ:at,qq=all]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_AT_ALL

    def test_parse_reply(self) -> None:
        result = parse_cq_code("[CQ:reply,id=98765]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_REPLY
        assert result[0].data["msg_id"] == "98765"
        assert result[0].data["platform"] == "qq"

    def test_parse_mixed(self) -> None:
        cq = "hello [CQ:face,id=14] world [CQ:at,qq=123]"
        result = parse_cq_code(cq)
        assert len(result) == 4
        assert result[0].type == SEGMENT_TEXT
        assert result[0].data["text"] == "hello "
        assert result[1].type == SEGMENT_EMOJI
        assert result[2].type == SEGMENT_TEXT
        assert result[2].data["text"] == " world "
        assert result[3].type == SEGMENT_AT
        assert result[3].data["user_id"] == "123"

    def test_parse_mixed_with_reply_prefix(self) -> None:
        cq = "[CQ:reply,id=1][CQ:face,id=14]nice"
        result = parse_cq_code(cq)
        assert len(result) == 3
        assert result[0].type == SEGMENT_REPLY
        assert result[0].data["msg_id"] == "1"
        assert result[1].type == SEGMENT_EMOJI
        assert result[2].type == SEGMENT_TEXT
        assert result[2].data["text"] == "nice"

    def test_parse_unsupported_cq(self) -> None:
        result = parse_cq_code("[CQ:music,id=123]")
        assert len(result) == 1
        assert result[0].type == SEGMENT_UNSUPPORTED

    def test_parse_onebot_array(self) -> None:
        arr = [
            {"type": "text", "data": {"text": "hello "}},
            {"type": "face", "data": {"id": "14"}},
            {"type": "text", "data": {"text": " world"}},
        ]
        result = parse_onebot_array(arr)
        assert len(result) == 3
        assert result[0].type == SEGMENT_TEXT
        assert result[0].data["text"] == "hello "
        assert result[1].type == SEGMENT_EMOJI
        assert result[2].type == SEGMENT_TEXT
        assert result[2].data["text"] == " world"

    def test_parse_empty_string(self) -> None:
        result = parse_cq_code("")
        assert result == []

    def test_parse_only_whitespace(self) -> None:
        result = parse_cq_code("   ")
        assert len(result) == 1
        assert result[0].type == SEGMENT_TEXT


class TestSegmentBuilder:
    def test_build_text(self) -> None:
        result = build_cq_code([text_segment("hello world")])
        assert result == "hello world"

    def test_build_image(self) -> None:
        result = build_cq_code([image_segment("test.jpg")])
        assert "[CQ:image" in result
        assert "test.jpg" in result

    def test_build_at(self) -> None:
        result = build_cq_code([at_segment("qq", "123456", "@123456")])
        assert "[CQ:at" in result
        assert "123456" in result

    def test_build_at_all(self) -> None:
        result = build_cq_code([at_all_segment()])
        assert "[CQ:at" in result
        assert "all" in result

    def test_build_reply(self) -> None:
        result = build_cq_code([reply_segment("qq", "999", "")])
        assert "[CQ:reply" in result
        assert "999" in result

    def test_build_emoji(self) -> None:
        result = build_cq_code([emoji_segment("\U0001F60A")])
        assert result == "\U0001F60A"

    def test_build_mixed(self) -> None:
        segments = [
            text_segment("hello "),
            emoji_segment("\U0001F642"),
            text_segment(" "),
            at_segment("qq", "123", "@123"),
        ]
        result = build_cq_code(segments)
        assert "hello" in result
        assert "\U0001F642" in result
        assert "[CQ:at" in result
        assert "123" in result

    def test_build_empty_list(self) -> None:
        result = build_cq_code([])
        assert result == ""

    def test_build_unsupported(self) -> None:
        result = build_cq_code([unsupported_segment("test")])
        assert "test" in result

    def test_build_sticker_as_image(self) -> None:
        seg = MessageSegment(type="sticker", data={"url": "https://example.com/sticker.webp"})
        result = build_cq_code([seg])
        assert "[CQ:image" in result
        assert "example.com" in result

    def test_build_empty_text_skipped(self) -> None:
        seg = MessageSegment(type=SEGMENT_TEXT, data={"text": ""})
        result = build_cq_code([seg])
        assert result == ""


class TestQQAdapter:
    @pytest.fixture
    def adapter(self) -> pytest.FixtureRequest:
        from src.adapters.qq.adapter import QQAdapter

        a = QQAdapter(bot_qq=123456, group_id=111222, onebot_ws_url="ws://127.0.0.1:8080")
        a._ws_api_call = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_send_message_success(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.return_value = {"data": {"message_id": 42}}
        result = await adapter.send_message("111222", [text_segment("hello")])
        assert result == "42"
        adapter._ws_api_call.assert_awaited_once_with(
            "send_group_msg",
            group_id=111222,
            message="hello",
        )

    @pytest.mark.asyncio
    async def test_send_message_with_reply(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.return_value = {"data": {"message_id": 43}}
        result = await adapter.send_message("111222", [text_segment("hello")], reply_to="100")
        assert result == "43"
        adapter._ws_api_call.assert_awaited_once()
        args, kwargs = adapter._ws_api_call.await_args
        assert args[0] == "send_group_msg"
        assert "[CQ:reply,id=100]" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_send_message_failure(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.side_effect = Exception("API error")
        result = await adapter.send_message("111222", [text_segment("hello")])
        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_empty_segments_no_reply(self, adapter: pytest.FixtureRequest) -> None:
        result = await adapter.send_message("111222", [text_segment("")])
        assert result is None

    @pytest.mark.asyncio
    async def test_edit_message_logs_warning(self, adapter: pytest.FixtureRequest) -> None:
        with patch("src.adapters.qq.adapter.logger") as mock_logger:
            await adapter.edit_message("111222", "msg_1", [text_segment("new")])
            mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_members_success(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.return_value = {"data": [
            {"user_id": 1001, "nickname": "Alice", "card": "AA"},
            {"user_id": 1002, "nickname": "Bob", "card": ""},
        ]}
        result = await adapter.list_members("111222")
        assert result == {"1001": "AA", "1002": "Bob"}

    @pytest.mark.asyncio
    async def test_list_members_failure(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.side_effect = Exception("API error")
        result = await adapter.list_members("111222")
        assert result == {}

    @pytest.mark.asyncio
    async def test_on_group_message_ignores_bot_self(self, adapter: pytest.FixtureRequest) -> None:
        callback = AsyncMock()
        adapter.set_on_message(callback)

        event_data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 111222,
            "user_id": 123456,
            "message_id": 555,
            "message": "hello",
            "sender": {
                "user_id": 123456,
                "nickname": "BotSelf",
            },
        }

        await adapter._on_group_message(event_data)
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_rate_limited(self, adapter: pytest.FixtureRequest) -> None:
        adapter._ws_api_call.return_value = {"data": {"message_id": 42}}
        adapter._rate_limiter._tokens = 0

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                adapter.send_message("111222", [text_segment("hello")]),
                timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_on_group_message_triggers_callback(self, adapter: pytest.FixtureRequest) -> None:
        callback = AsyncMock()
        adapter.set_on_message(callback)

        event_data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 111222,
            "user_id": 789012,
            "message_id": 555,
            "message": "hello [CQ:face,id=14]",
            "sender": {
                "user_id": 789012,
                "nickname": "TestUser",
                "card": "TestCard",
            },
        }

        await adapter._on_group_message(event_data)
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.platform == "qq"
        assert event.channel_id == "111222"
        assert event.author_id == "789012"
        assert event.author_name == "TestCard"
        assert len(event.segments) == 2

    @pytest.mark.asyncio
    async def test_on_group_message_wrong_group_ignored(self, adapter: pytest.FixtureRequest) -> None:
        callback = AsyncMock()
        adapter.set_on_message(callback)

        event_data = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 999999,
            "user_id": 789012,
            "message_id": 555,
            "message": "hello",
            "sender": {"user_id": 789012, "nickname": "TestUser"},
        }

        await adapter._on_group_message(event_data)
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_ws_message_ignores_echo(self, adapter: pytest.FixtureRequest) -> None:
        ws_message = json.dumps({"status": "ok", "retcode": 0, "data": None, "echo": "req_1"})
        with patch.object(adapter, "_on_group_message", new_callable=AsyncMock) as mock_handler:
            await adapter._dispatch_ws_message(ws_message)
            mock_handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_ws_message_invalid_json(self, adapter: pytest.FixtureRequest) -> None:
        with patch("src.adapters.qq.adapter.logger") as mock_logger:
            await adapter._dispatch_ws_message("not json")
            mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_ws_message_private_ignored(self, adapter: pytest.FixtureRequest) -> None:
        ws_message = json.dumps({
            "post_type": "message",
            "message_type": "private",
            "user_id": 789012,
            "message": "hello",
        })
        with patch.object(adapter, "_on_group_message", new_callable=AsyncMock) as mock_handler:
            await adapter._dispatch_ws_message(ws_message)
            mock_handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_ws_message_group(self, adapter: pytest.FixtureRequest) -> None:
        ws_message = json.dumps({
            "post_type": "message",
            "message_type": "group",
            "group_id": 111222,
            "user_id": 789012,
            "message_id": 1,
            "message": "test",
            "sender": {"user_id": 789012, "nickname": "Tester"},
        })
        callback = AsyncMock()
        adapter.set_on_message(callback)
        await adapter._dispatch_ws_message(ws_message)
        await asyncio.sleep(0)
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.platform == "qq"
        assert event.channel_id == "111222"
        assert event.author_id == "789012"
        assert event.message_id == "1"
        assert event.author_name == "Tester"
        assert len(event.segments) == 1
        assert event.segments[0].type == SEGMENT_TEXT
        assert event.segments[0].data["text"] == "test"

    @pytest.mark.asyncio
    async def test_start_stop(self, adapter: pytest.FixtureRequest) -> None:
        await adapter.start()
        assert adapter._running is True
        assert adapter._listener_task is not None

        adapter._listener_task.cancel()
        try:
            await adapter._listener_task
        except asyncio.CancelledError:
            pass

        await adapter.stop()
        assert adapter._running is False
