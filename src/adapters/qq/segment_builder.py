from __future__ import annotations

from aiocqhttp import Message as CQMessage
from aiocqhttp import MessageSegment as CQSegment

from bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_AT_ALL,
    SEGMENT_EMOJI,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_STICKER,
    SEGMENT_TEXT,
    SEGMENT_UNSUPPORTED,
    MessageSegment,
)


def build_cq_code(segments: list[MessageSegment]) -> str:
    cq_segments: list[CQSegment] = []

    for seg in segments:
        built = _convert_to_cq(seg)
        if built is not None:
            cq_segments.append(built)

    if not cq_segments:
        return ""

    return str(CQMessage(cq_segments))


def _convert_to_cq(seg: MessageSegment) -> CQSegment | None:
    if seg.type == SEGMENT_TEXT:
        text = seg.data.get("text", "")
        if text:
            return CQSegment.text(text)
        return None

    if seg.type == SEGMENT_IMAGE:
        file_str = seg.data.get("file", "")
        if file_str:
            return CQSegment.image(file=file_str)
        return None

    if seg.type == SEGMENT_AT:
        user_id = seg.data.get("user_id", "")
        if user_id:
            return CQSegment.at(user_id=user_id)
        return None

    if seg.type == SEGMENT_AT_ALL:
        return CQSegment.at(user_id="all")

    if seg.type == SEGMENT_REPLY:
        msg_id = seg.data.get("msg_id", "")
        if msg_id:
            return CQSegment.reply(id_=int(msg_id))
        return None

    if seg.type == SEGMENT_EMOJI:
        unicode_char = seg.data.get("unicode", "")
        if unicode_char:
            return CQSegment.text(unicode_char)
        return None

    if seg.type == SEGMENT_STICKER:
        url = seg.data.get("url", "")
        if url:
            return CQSegment.image(file=url)
        return None

    if seg.type == SEGMENT_UNSUPPORTED:
        hint = seg.data.get("hint", "?")
        return CQSegment.text(f"[{hint}]")

    return None
