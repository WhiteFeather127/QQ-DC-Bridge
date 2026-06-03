from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MessageEvent:
    message_id: str
    platform: str
    channel_id: str
    author_id: str
    author_name: str
    segments: list[Any]
    timestamp: datetime = field(default_factory=datetime.now)


class PlatformAdapter(ABC):
    def __init__(self) -> None:
        self._on_message: Callable[[MessageEvent], Awaitable[None]] | None = None

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_message(
        self,
        channel_id: str,
        segments: list,
        reply_to: str | None = None,
    ) -> str | None: ...

    @abstractmethod
    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        segments: list,
    ) -> None: ...

    @abstractmethod
    async def list_members(self, channel_id: str) -> dict[str, str]: ...

    def status_info(self) -> dict:
        return {
            "type": type(self).__name__,
            "name": type(self).__name__,
            "connected": False,
        }

    def set_on_message(
        self,
        callback: Callable[[MessageEvent], Awaitable[None]],
    ) -> None:
        self._on_message = callback

    async def _trigger_on_message(self, event: MessageEvent) -> None:
        if self._on_message is not None:
            await self._on_message(event)
