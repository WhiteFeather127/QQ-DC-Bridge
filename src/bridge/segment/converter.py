from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import MessageSegment

DIR_QQ_TO_DISCORD = "qq_to_discord"
DIR_DISCORD_TO_QQ = "discord_to_qq"


class SegmentConverter:
    _rules: dict[str, dict[str, Callable[[MessageSegment, Any], MessageSegment | None]]]

    def __init__(self) -> None:
        self._rules = {}

    def register(
        self,
        direction: str,
        segment_type: str,
        converter: Callable[[MessageSegment, Any], MessageSegment | None],
    ) -> None:
        if direction not in self._rules:
            self._rules[direction] = {}
        self._rules[direction][segment_type] = converter

    def convert(
        self,
        direction: str,
        segment: MessageSegment,
        context: Any = None,
    ) -> MessageSegment | None:
        rules_for_direction = self._rules.get(direction)
        if rules_for_direction is None:
            return None
        converter = rules_for_direction.get(segment.type)
        if converter is None:
            return None
        return converter(segment, context)

    def convert_all(
        self,
        direction: str,
        segments: list[MessageSegment],
        context: Any = None,
    ) -> list[MessageSegment]:
        result: list[MessageSegment] = []
        for segment in segments:
            converted = self.convert(direction, segment, context)
            if converted is not None:
                result.append(converted)
        return result
