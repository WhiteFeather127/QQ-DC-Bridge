from __future__ import annotations

from typing import Any

from aiocqhttp import Message as CQMessage

from bridge.segment.types import (
    MessageSegment,
    at_segment,
    emoji_segment,
    image_segment,
    reply_segment,
    text_segment,
    unsupported_segment,
)
from utils.emoji_map import get_emoji


def parse_cq_code(cq_string: str) -> list[MessageSegment]:
    segments: list[MessageSegment] = []
    cq_msg = CQMessage(cq_string)

    for cq_seg in cq_msg:
        converted = _convert_cq_segment(cq_seg)
        if converted is not None:
            segments.append(converted)

    return segments


def parse_onebot_array(message_array: list[dict[str, Any]]) -> list[MessageSegment]:
    segments: list[MessageSegment] = []
    cq_msg = CQMessage(message_array)

    for cq_seg in cq_msg:
        converted = _convert_cq_segment(cq_seg)
        if converted is not None:
            segments.append(converted)

    return segments


def _convert_cq_segment(cq_seg: Any) -> MessageSegment | None:
    seg_type = cq_seg.type
    data = cq_seg.data

    if seg_type == "text":
        raw_text = data.get("text", "")
        if raw_text:
            return text_segment(raw_text)
        return None

    if seg_type == "face":
        face_id = int(data.get("id", 0))
        emoji_char = get_emoji(face_id)
        if emoji_char:
            return emoji_segment(emoji_char)
        return text_segment(f"[表情:{face_id}]")

    if seg_type == "image":
        file_str = data.get("file", "")
        if file_str:
            return image_segment(file_str)
        return unsupported_segment("image")

    if seg_type == "at":
        user_id = str(data.get("qq", ""))
        if user_id == "all":
            from src.bridge.segment.types import at_all_segment
            return at_all_segment()
        return at_segment(platform="qq", user_id=user_id, display=user_id)

    if seg_type == "reply":
        reply_id = str(data.get("id", ""))
        if reply_id:
            return reply_segment(platform="qq", msg_id=reply_id, content="")

    return unsupported_segment(f"cq:{seg_type}")
