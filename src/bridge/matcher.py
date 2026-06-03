from __future__ import annotations

from typing import Any

from adapters.base import PlatformAdapter
from bridge.segment.base import MessageSegment
from bridge.segment.converter import (
    DIR_DISCORD_TO_QQ,
    DIR_QQ_TO_DISCORD,
    SegmentConverter,
)
from bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_AT_ALL,
    SEGMENT_EMOJI,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_STICKER,
    SEGMENT_TEXT,
    SEGMENT_UNSUPPORTED,
    text_segment,
)


class UserMatcher:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, str]] = {}
        self._bindings: dict[str, str] = {}

    async def refresh_cache(
        self,
        platform: str,
        adapter: PlatformAdapter,
        channel_id: str,
    ) -> None:
        members = await adapter.list_members(channel_id)
        self._cache[platform] = members

    def exact_match(self, display_name: str, target_platform: str) -> str | None:
        cache = self._cache.get(target_platform)
        if cache is None:
            return None
        for user_id, name in cache.items():
            if name == display_name:
                return user_id
        return None

    def fuzzy_match(self, display_name: str, target_platform: str) -> str | None:
        cache = self._cache.get(target_platform)
        if cache is None:
            return None
        for user_id, name in cache.items():
            if display_name in name or name in display_name:
                return user_id
        return None

    def match_user(
        self,
        display_name: str,
        target_platform: str,
    ) -> tuple[str, str] | None:
        user_id = self.exact_match(display_name, target_platform)
        if user_id is not None:
            return user_id, self._cache[target_platform][user_id]

        user_id = self.fuzzy_match(display_name, target_platform)
        if user_id is not None:
            return user_id, self._cache[target_platform][user_id]

        return None

    def _resolve_qq_display_name(self, user_id: str) -> str | None:
        qq_cache = self._cache.get("qq", {})
        return qq_cache.get(user_id)

    def register_converter_rules(self, converter: SegmentConverter) -> None:
        matcher = self

        def qq_to_discord(segment: MessageSegment, context: Any) -> MessageSegment | None:
            user_id = segment.data.get("user_id", "")
            display = segment.data.get("display", "")
            qq_display_name = matcher._resolve_qq_display_name(user_id) or display or user_id
            return text_segment(f"@{qq_display_name}")

        def discord_to_qq(segment: MessageSegment, context: Any) -> MessageSegment | None:
            display = segment.data.get("display", "")
            user_id = segment.data.get("user_id", "")
            display_name = display or user_id
            return text_segment(f"@{display_name}")

        converter.register(DIR_QQ_TO_DISCORD, SEGMENT_AT, qq_to_discord)
        converter.register(DIR_DISCORD_TO_QQ, SEGMENT_AT, discord_to_qq)

        for seg_type in (
            SEGMENT_TEXT,
            SEGMENT_IMAGE,
            SEGMENT_AT_ALL,
            SEGMENT_EMOJI,
            SEGMENT_REPLY,
            SEGMENT_STICKER,
            SEGMENT_UNSUPPORTED,
        ):
            converter.register(DIR_QQ_TO_DISCORD, seg_type, lambda s, _: s)
            converter.register(DIR_DISCORD_TO_QQ, seg_type, lambda s, _: s)
