from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MessageSegment:
    type: str
    data: dict = field(default_factory=dict)


@dataclass
class BridgeMessage:
    id: str
    platform: str
    channel_id: str
    author_id: str
    author_name: str
    segments: list[MessageSegment] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
