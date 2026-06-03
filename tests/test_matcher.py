from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge.matcher import UserMatcher
from bridge.segment.base import MessageSegment
from bridge.segment.converter import (
    DIR_DISCORD_TO_QQ,
    DIR_QQ_TO_DISCORD,
    SegmentConverter,
)
from bridge.segment.types import SEGMENT_AT, at_segment, text_segment


@pytest.fixture
def matcher() -> UserMatcher:
    return UserMatcher()


@pytest.fixture
def populated_matcher(matcher: UserMatcher) -> UserMatcher:
    matcher._cache["qq"] = {
        "10001": "Alice",
        "10002": "Bob",
        "10003": "Charlie_Zhang",
    }
    matcher._cache["discord"] = {
        "90001": "Alice",
        "90002": "Bob_Smith",
        "90003": "Charlie",
    }
    return matcher


class TestExactMatch:
    def test_exact_match_found(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.exact_match("Alice", "discord") == "90001"

    def test_exact_match_not_found(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.exact_match("Unknown", "discord") is None

    def test_exact_match_case_sensitive(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.exact_match("alice", "discord") is None

    def test_exact_match_platform_not_cached(self, matcher: UserMatcher) -> None:
        assert matcher.exact_match("Alice", "telegram") is None


class TestFuzzyMatch:
    def test_fuzzy_match_display_in_name(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.fuzzy_match("Bob", "discord") == "90002"

    def test_fuzzy_match_name_in_display(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.fuzzy_match("Charlie_Zhang", "discord") == "90003"

    def test_fuzzy_match_exact_also_matches(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.fuzzy_match("Alice", "discord") == "90001"

    def test_fuzzy_match_not_found(self, populated_matcher: UserMatcher) -> None:
        assert populated_matcher.fuzzy_match("Zoe", "discord") is None

    def test_fuzzy_match_platform_not_cached(self, matcher: UserMatcher) -> None:
        assert matcher.fuzzy_match("Alice", "telegram") is None


class TestMatchUser:
    def test_exact_match_returns_user_id_and_name(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        result = populated_matcher.match_user("Alice", "discord")
        assert result == ("90001", "Alice")

    def test_fuzzy_match_fallback(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        result = populated_matcher.match_user("Bob", "discord")
        assert result == ("90002", "Bob_Smith")

    def test_no_match_returns_none(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        assert populated_matcher.match_user("Nobody", "discord") is None

    def test_exact_precedence_over_fuzzy(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        populated_matcher._cache["discord"]["99999"] = "Bob"
        result = populated_matcher.match_user("Bob", "discord")
        assert result == ("99999", "Bob")

    def test_platform_not_cached_returns_none(
        self,
        matcher: UserMatcher,
    ) -> None:
        assert matcher.match_user("Alice", "telegram") is None


class TestRefreshCache:
    @pytest.mark.asyncio
    async def test_refresh_populates_cache(self, matcher: UserMatcher) -> None:
        mock_adapter = MagicMock()
        mock_adapter.list_members = AsyncMock(
            return_value={"u1": "User1", "u2": "User2"},
        )

        await matcher.refresh_cache("qq", mock_adapter, "channel_123")

        assert matcher._cache["qq"] == {"u1": "User1", "u2": "User2"}
        mock_adapter.list_members.assert_awaited_once_with("channel_123")

    @pytest.mark.asyncio
    async def test_refresh_overwrites_old_cache(self, matcher: UserMatcher) -> None:
        matcher._cache["qq"] = {"old": "OldUser"}
        mock_adapter = MagicMock()
        mock_adapter.list_members = AsyncMock(
            return_value={"new": "NewUser"},
        )

        await matcher.refresh_cache("qq", mock_adapter, "channel_456")

        assert matcher._cache["qq"] == {"new": "NewUser"}

    @pytest.mark.asyncio
    async def test_refresh_multiple_platforms(self, matcher: UserMatcher) -> None:
        qq_adapter = MagicMock()
        qq_adapter.list_members = AsyncMock(return_value={"q1": "QQ_User"})
        dc_adapter = MagicMock()
        dc_adapter.list_members = AsyncMock(return_value={"d1": "DC_User"})

        await matcher.refresh_cache("qq", qq_adapter, "ch1")
        await matcher.refresh_cache("discord", dc_adapter, "ch2")

        assert matcher._cache["qq"] == {"q1": "QQ_User"}
        assert matcher._cache["discord"] == {"d1": "DC_User"}


class TestRegisterConverterRules:
    def test_qq_to_discord_match_converts_to_mention(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segment = at_segment("qq", "10001", "Alice")
        result = converter.convert(DIR_QQ_TO_DISCORD, segment)

        assert result is not None
        assert result.type == "text"
        assert result.data["text"] == "<@90001>"

    def test_qq_to_discord_no_match_falls_back_to_text(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segment = at_segment("qq", "99999", "UnknownUser")
        result = converter.convert(DIR_QQ_TO_DISCORD, segment)

        assert result is not None
        assert result.type == "text"
        assert result.data["text"] == "@UnknownUser"

    def test_discord_to_qq_match_converts_to_at_segment(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segment = at_segment("discord", "90001", "Alice")
        result = converter.convert(DIR_DISCORD_TO_QQ, segment)

        assert result is not None
        assert result.type == SEGMENT_AT
        assert result.data["platform"] == "qq"
        assert result.data["user_id"] == "10001"
        assert result.data["display"] == "Alice"

    def test_discord_to_qq_no_match_falls_back_to_text(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segment = at_segment("discord", "99999", "UnknownUser")
        result = converter.convert(DIR_DISCORD_TO_QQ, segment)

        assert result is not None
        assert result.type == "text"
        assert result.data["text"] == "@UnknownUser"

    def test_convert_all_applies_rules_correctly(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segments = [
            text_segment("Hello "),
            at_segment("qq", "10001", "Alice"),
            text_segment(" "),
            at_segment("qq", "99999", "Stranger"),
        ]

        result = converter.convert_all(DIR_QQ_TO_DISCORD, segments)

        assert len(result) == 2
        assert result[0].data["text"] == "<@90001>"
        assert result[1].data["text"] == "@Stranger"

    def test_unregistered_direction_returns_none(
        self,
        populated_matcher: UserMatcher,
    ) -> None:
        converter = SegmentConverter()
        populated_matcher.register_converter_rules(converter)

        segment = at_segment("qq", "10001", "Alice")
        result = converter.convert("qq_to_telegram", segment)

        assert result is None
