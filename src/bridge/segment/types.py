from __future__ import annotations

from .base import MessageSegment

SEGMENT_TEXT = "text"
SEGMENT_IMAGE = "image"
SEGMENT_AT = "at"
SEGMENT_AT_ALL = "at_all"
SEGMENT_REPLY = "reply"
SEGMENT_EMOJI = "emoji"
SEGMENT_STICKER = "sticker"
SEGMENT_UNSUPPORTED = "unsupported"


def text_segment(text: str) -> MessageSegment:
    return MessageSegment(type=SEGMENT_TEXT, data={"text": text})


def image_segment(file: str) -> MessageSegment:
    return MessageSegment(type=SEGMENT_IMAGE, data={"file": file})


def at_segment(platform: str, user_id: str, display: str) -> MessageSegment:
    return MessageSegment(
        type=SEGMENT_AT,
        data={"platform": platform, "user_id": user_id, "display": display},
    )


def at_all_segment() -> MessageSegment:
    return MessageSegment(type=SEGMENT_AT_ALL, data={})


def reply_segment(platform: str, msg_id: str, content: str) -> MessageSegment:
    return MessageSegment(
        type=SEGMENT_REPLY,
        data={"platform": platform, "msg_id": msg_id, "content": content},
    )


def emoji_segment(unicode_char: str) -> MessageSegment:
    return MessageSegment(type=SEGMENT_EMOJI, data={"unicode": unicode_char})


def unsupported_segment(hint: str) -> MessageSegment:
    return MessageSegment(type=SEGMENT_UNSUPPORTED, data={"hint": hint})
