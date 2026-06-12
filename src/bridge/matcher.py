from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)
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
    at_segment,
    text_segment,
)

if TYPE_CHECKING:
    from bridge.bind_manager import BindManager


class UserMatcher:
    def __init__(self, bind_manager: BindManager | None = None) -> None:
        self._cache: dict[str, dict[str, str]] = {}
        self._bind_manager = bind_manager

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

    def get_display_name(self, platform: str, user_id: str) -> str | None:
        cache = self._cache.get(platform, {})
        return cache.get(user_id)

    def has_user(self, platform: str, user_id: str) -> bool:
        return user_id in self._cache.get(platform, {})

    def search_users_by_display(self, name: str, platform: str) -> list[tuple[str, str]]:
        """Search users by display name. Exact match first, then fall back to substring match."""
        cache = self._cache.get(platform, {})
        if cache is None:
            return []
        exact: list[tuple[str, str]] = []
        fuzzy: list[tuple[str, str]] = []
        for uid, display in cache.items():
            if display == name:
                exact.append((uid, display))
            elif name in display or display in name:
                fuzzy.append((uid, display))
        if fuzzy:
            logger.warning(
                "Fuzzy match used for '%s' on %s: exact=%d fuzzy=%d",
                name, platform, len(exact), len(fuzzy),
            )
        return exact + fuzzy

    def _resolve_qq_display_name(self, user_id: str) -> str | None:
        return self.get_display_name("qq", user_id)

    def resolve_mention(
        self,
        source_platform: str,
        source_user_id: str,
        target_platform: str,
    ) -> tuple[str, str] | None:
        """绑定感知的 @ 提及解析.

        查 source 用户是否有绑定到 target 平台的账号。
        如有绑定，返回 (target_user_id, target_display_name).

        Returns:
            (target_user_id, target_display_name) 或 None.
        """
        if self._bind_manager is None:
            return None
        bound_id = self._bind_manager.get_counterpart(source_platform, source_user_id)
        if bound_id is None:
            return None
        target_cache = self._cache.get(target_platform, {})
        target_display = target_cache.get(bound_id, bound_id)
        logger.debug(
            "Mention resolved via binding: %s:%s → %s:%s (%s)",
            source_platform, source_user_id,
            target_platform, bound_id, target_display,
        )
        return bound_id, target_display

    def register_converter_rules(self, converter: SegmentConverter) -> None:
        matcher = self

        def qq_to_discord(segment: MessageSegment, context: Any) -> MessageSegment | None:
            user_id = segment.data.get("user_id", "")
            display = segment.data.get("display", "")
            # 优先查绑定
            result = matcher.resolve_mention("qq", user_id, "discord")
            if result is not None:
                target_id, display_name = result
                return text_segment(f"<@{target_id}>")
            # 无绑定 → 原有行为
            qq_display_name = matcher._resolve_qq_display_name(user_id) or display or user_id
            return text_segment(f"@{qq_display_name}")

        def discord_to_qq(segment: MessageSegment, context: Any) -> MessageSegment | None:
            user_id = segment.data.get("user_id", "")
            display = segment.data.get("display", "")
            # 优先查绑定
            result = matcher.resolve_mention("discord", user_id, "qq")
            if result is not None:
                target_id, display_name = result
                return at_segment("qq", target_id, display_name)
            # 无绑定 → 原有行为
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
