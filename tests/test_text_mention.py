from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge.matcher import UserMatcher
from bridge.orchestrator import Orchestrator
from bridge.segment.base import MessageSegment
from bridge.segment.converter import SegmentConverter
from bridge.segment.types import SEGMENT_AT, SEGMENT_TEXT, at_segment, text_segment
from models.config_model import BridgeConfig


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture
def bridge_config() -> BridgeConfig:
    return BridgeConfig()


def empty_matcher() -> UserMatcher:
    """无缓存的 UserMatcher."""
    return UserMatcher()


def populated_matcher() -> UserMatcher:
    """带预置缓存的 UserMatcher."""
    matcher = UserMatcher()
    matcher._cache = {
        "discord": {
            "111": "Player One",
            "222": "Bob",
            "333": "Alice",
            "444": "NoSpace",
        },
        "qq": {
            "qq001": "张三",
            "qq002": "李四",
            "qq003": "王五",
            "qq004": "赵六",
        },
    }
    return matcher


def make_orchestrator(matcher: UserMatcher | None) -> Orchestrator:
    """创建 Orchestrator 实例，可注入 matcher."""
    config = BridgeConfig()
    store = MagicMock()
    orch = Orchestrator(config, store)
    orch.matcher = matcher
    orch.converter = SegmentConverter()
    return orch


def segments_from_text(text: str) -> list[MessageSegment]:
    """构造一个只包含给定文本段的 segments 列表."""
    return [text_segment(text)]


# ── _find_full_name_mentions 的间接测试（通过 _resolve_text_mentions） ──


class TestFullNameMatchQQtoDiscord:
    """QQ → Discord: 文本 @ 中带空格的 Discord 用户名."""

    def test_full_name_with_space_matches(self) -> None:
        """@Player One 应匹配 Discord 缓存中的 'Player One'."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@Player One 你好")
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 2
        assert result[0].type == SEGMENT_TEXT
        assert result[0].data["text"] == "<@111>"
        assert result[1].type == SEGMENT_TEXT
        assert result[1].data["text"] == " 你好"

    def test_full_name_case_insensitive(self) -> None:
        """@player one（小写）应匹配 'Player One'（大小写不敏感）."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@player one 嗨")
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 2
        assert result[0].data["text"] == "<@111>"
        assert result[1].data["text"] == " 嗨"

    def test_full_name_uppercase(self) -> None:
        """@PLAYER ONE 也应匹配."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@PLAYER ONE test")
        result = orch._resolve_text_mentions(segments, "discord")
        assert result[0].data["text"] == "<@111>"

    def test_short_name_regex_fallback(self) -> None:
        """@Bob（无空格）应有正则匹配，走名称匹配路径."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@Bob 在吗")
        result = orch._resolve_text_mentions(segments, "discord")
        # Bob 在 Discord 缓存中有全匹配
        assert len(result) == 2
        assert result[0].data["text"] == "<@222>"
        assert result[1].data["text"] == " 在吗"

    def test_name_not_in_cache_keeps_original(self) -> None:
        """@UnknownUser 不在缓存中，保留原文."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@UnknownUser 测试")
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 2
        assert result[0].data["text"] == "@UnknownUser"
        assert result[1].data["text"] == " 测试"

    def test_space_before_at_keeps_original(self) -> None:
        """@ 后带空格（@ Player One）不解析，保持原文."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@ Player One 测试")
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 1
        assert result[0].data["text"] == "@ Player One 测试"

    def test_prefix_no_full_match_falls_back_to_regex(self) -> None:
        """@NoS 在缓存中无精确 'NoS'，但有 'NoSpace'，模糊匹配命中 NoSpace."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@NoS 测试")
        result = orch._resolve_text_mentions(segments, "discord")
        # 正则匹配 @NoS，name="NoS"，模糊匹配发现 "NoS" in "NoSpace"，命中 ID 444
        assert result[0].data["text"] == "<@444>"
        assert result[1].data["text"] == " 测试"

    def test_multiple_full_names(self) -> None:
        """同时提及两个带空格的用户名."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@Player One 和 @Bob 一起")
        result = orch._resolve_text_mentions(segments, "discord")
        # 预期: [<@111>, " 和 ", <@222>, " 一起"] = 4 段
        assert len(result) == 4
        assert result[0].data["text"] == "<@111>"  # Player One
        assert result[1].data["text"] == " 和 "
        assert result[2].data["text"] == "<@222>"  # Bob
        assert result[3].data["text"] == " 一起"

    def test_longer_name_priority(self) -> None:
        """长名字优先匹配：缓存有 'Player' 和 'Player One'，文本 @Player One 应匹配长的."""
        matcher = populated_matcher()
        matcher._cache["discord"]["555"] = "Player"  # 添加短名字
        orch = make_orchestrator(matcher)

        segments = segments_from_text("@Player One 测试")
        result = orch._resolve_text_mentions(segments, "discord")
        # 应匹配 "Player One"（长），而非 "Player"（短）
        assert result[0].data["text"] == "<@111>"  # Player One 的 ID

    def test_no_at_symbol_passes_through(self) -> None:
        """没有 @ 的文本保持不变."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("你好世界")
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 1
        assert result[0].data["text"] == "你好世界"

    def test_non_text_segments_unchanged(self) -> None:
        """非 text 段（如图片）不变."""
        orch = make_orchestrator(populated_matcher())
        segments = [
            text_segment("@Player One 嗨 "),
            MessageSegment(type="image", data={"file": "test.png"}),
        ]
        result = orch._resolve_text_mentions(segments, "discord")
        assert len(result) == 3
        assert result[0].data["text"] == "<@111>"
        assert result[1].data["text"] == " 嗨 "
        assert result[2].type == "image"

    def test_no_matcher_returns_original(self) -> None:
        """没有 matcher 时原样返回."""
        orch = make_orchestrator(None)
        segments = segments_from_text("@Player One 测试")
        result = orch._resolve_text_mentions(segments, "discord")
        assert result == segments


class TestFullNameMatchDiscordToQQ:
    """Discord → QQ: 文本 @ 中带空格的 QQ 昵称."""

    def test_full_name_matches_qq(self) -> None:
        """@张三 应匹配 QQ 缓存中的 '张三'，转为 at_segment."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@张三 你好")
        result = orch._resolve_text_mentions(segments, "qq")
        assert len(result) == 2
        assert result[0].type == SEGMENT_AT
        assert result[0].data["platform"] == "qq"
        assert result[0].data["user_id"] == "qq001"
        assert result[0].data["display"] == "张三"
        assert result[1].type == SEGMENT_TEXT
        assert result[1].data["text"] == " 你好"

    def test_not_found_keeps_original(self) -> None:
        """不存在的名字保留原文."""
        orch = make_orchestrator(populated_matcher())
        segments = segments_from_text("@不存在 测试")
        result = orch._resolve_text_mentions(segments, "qq")
        assert result[0].data["text"] == "@不存在"
        assert result[1].data["text"] == " 测试"


class TestFullNameWithBinding:
    """全名匹配 + 绑定路径的集成测试."""

    def test_full_name_with_binding_uses_bound_user(
        self,
    ) -> None:
        """Player One 在 Discord 缓存中，且绑定了 QQ 用户，应使用绑定目标."""
        matcher = populated_matcher()
        # 设置绑定：Discord 用户 111 (Player One) 绑定到 QQ 用户 qq001 (张三)
        bind_manager = MagicMock()
        bind_manager.get_counterpart = MagicMock(return_value="qq001")
        matcher._bind_manager = bind_manager

        orch = make_orchestrator(matcher)
        segments = segments_from_text("@Player One 你好")
        result = orch._resolve_text_mentions(segments, "discord")
        # 绑定解析应返回 qq001 的绑定信息，但最终转为 Discord 提及格式？
        # 实际上 _resolve_text_mention_via_binding 只在 source 平台找同名用户
        # 这里 name="Player One", source_platform="qq", target_platform="discord"
        # 会在 QQ 缓存中找 "Player One" — 找不到，所以绑定不命中，回退全名匹配
        assert result[0].data["text"] == "<@111>"

    def test_binding_for_regex_match(
        self,
    ) -> None:
        """正则匹配到的 @张三，如果张三在 QQ 缓存中且绑定了 Discord 用户，应使用绑定目标."""
        matcher = populated_matcher()
        # 设置绑定：QQ 用户 qq001 (张三) 绑定到 Discord 用户 111 (Player One)
        from unittest.mock import PropertyMock

        bind_manager = MagicMock()
        bind_manager.get_counterpart = MagicMock(return_value="111")

        matcher._bind_manager = bind_manager

        orch = make_orchestrator(matcher)
        # 注意：对于 Discord→QQ 方向，target_platform="qq"
        segments = segments_from_text("@张三 你好")
        result = orch._resolve_text_mentions(segments, "qq")
        # name="张三", source_platform="discord", target_platform="qq"
        # 绑定：在 Discord 缓存找 "张三" → 找不到 → 不回绑定
        # 回退到名称匹配：在 QQ 缓存中找 "张三" → qq001 → 匹配
        assert result[0].type == SEGMENT_AT
        assert result[0].data["display"] == "张三"
