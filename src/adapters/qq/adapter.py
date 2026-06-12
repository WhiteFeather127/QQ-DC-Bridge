from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import httpx
import websockets

from adapters.base import MessageEvent, PlatformAdapter
from adapters.qq.segment_builder import build_cq_code
from adapters.qq.segment_parser import parse_cq_code, parse_onebot_array
from bridge.segment.base import MessageSegment
from bridge.segment.types import SEGMENT_IMAGE
from utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)


class QQAdapter(PlatformAdapter):
    def __init__(
        self,
        bot_qq: int,
        group_id: int,
        onebot_ws_url: str,
        qq_rate_limit: float = 1.0,
    ) -> None:
        super().__init__()
        self._bot_qq = bot_qq
        self._group_id = group_id
        self._ws_url = onebot_ws_url
        self._rate_limiter = RateLimiter(interval=qq_rate_limit, burst=3)

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._listener_task: asyncio.Task[None] | None = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        self._pending_actions: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def start(self) -> None:
        self._running = True
        self._listener_task = asyncio.create_task(self._ws_event_loop())

    def status_info(self) -> dict:
        return {
            "type": "QQ",
            "name": "QQ",
            "connected": self._ws is not None and self._running,
            "group_id": self._group_id,
            "ws_url": self._ws_url,
        }

    @property
    def bot_user_id(self) -> str | None:
        return str(self._bot_qq)

    async def stop(self) -> None:
        self._running = False
        for future in self._pending_actions.values():
            if not future.done():
                future.cancel()
        self._pending_actions.clear()
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_message(
        self,
        channel_id: str,
        segments: list,
        reply_to: str | None = None,
    ) -> str | None:
        processed_segments = await self._preprocess_image_segments(segments)
        cq_message = build_cq_code(processed_segments)
        if not cq_message and reply_to is None:
            return None

        if reply_to is not None:
            cq_message = f"[CQ:reply,id={reply_to}]" + cq_message

        await self._rate_limiter.acquire()

        try:
            result = await self._ws_api_call(
                "send_group_msg",
                group_id=int(channel_id),
                message=cq_message,
            )
            if result is None:
                return None
            data = result.get("data", {}) if isinstance(result, dict) else {}
            message_id = data.get("message_id")
            return str(message_id) if message_id is not None else None
        except Exception:
            logger.exception("Failed to send QQ group message")
            return None

    async def resolve_image_url(self, file_uuid: str) -> str | None:
        logger.debug("Resolving image URL for: %s", file_uuid)
        try:
            result = await self._ws_api_call("get_image", file=file_uuid)
        except Exception:
            logger.exception("get_image API call failed for: %s", file_uuid)
            return None
        if not result:
            logger.warning("get_image returned None for: %s", file_uuid)
            return None
        if not isinstance(result, dict):
            logger.warning("get_image unexpected type for: %s, type=%s", file_uuid, type(result).__name__)
            return None
        data = result.get("data", {})
        url = data.get("url") if isinstance(data, dict) else None
        if url:
            logger.debug("Resolved image URL: %s (length=%d)", url[:80], len(url))
            return str(url)
        logger.warning("get_image response has no url field: %s", result)
        return None

    async def get_image_data(self, file_uuid: str) -> bytes | None:
        """Get image bytes from NapCat's local cache, bypassing CDN entirely.

        Calls get_image API to get the local file path, then reads it directly.
        Falls back to downloading from the API-returned URL if local file is unavailable.
        """
        try:
            result = await self._ws_api_call("get_image", file=file_uuid)
        except Exception:
            logger.debug("get_image API call failed for: %s", file_uuid[:20])
            return None
        if not result or not isinstance(result, dict):
            return None
        data = result.get("data", {})
        if not isinstance(data, dict):
            return None

        file_path = data.get("file", "")
        if file_path and os.path.isfile(file_path):
            try:
                with open(file_path, "rb") as f:
                    return f.read()
            except Exception:
                logger.debug("Failed to read local image file: %s", file_path)

        url = data.get("url", "")
        if url:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.content
            except Exception:
                logger.debug("Failed to download image from URL: %s", url[:80])

        return None

    async def _preprocess_image_segments(
        self, segments: list,
    ) -> list:
        processed: list = []
        for seg in segments:
            if seg.type == SEGMENT_IMAGE:
                file_str = seg.data.get("file", "")
                if file_str and file_str.startswith("http"):
                    b64 = await self._download_image_as_base64(file_str)
                    if b64:
                        seg = MessageSegment(
                            type=SEGMENT_IMAGE,
                            data={"file": f"base64://{b64}"},
                        )
            processed.append(seg)
        return processed

    async def _download_image_as_base64(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return base64.b64encode(resp.content).decode()
        except Exception:
            logger.warning("Failed to download image: %s", url[:80])
        return None

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        segments: list,
    ) -> None:
        logger.warning(
            "QQ OneBot does not support message editing, "
            "skipped edit for message %s in channel %s",
            message_id,
            channel_id,
        )

    async def list_members(self, channel_id: str) -> dict[str, str]:
        try:
            result = await self._ws_api_call(
                "get_group_member_list",
                group_id=int(channel_id),
            )
            if result is None:
                return {}
            raw_list: list = []
            if isinstance(result, dict):
                raw_list = result.get("data", [])
            if not isinstance(raw_list, list):
                raw_list = []
            members: dict[str, str] = {}
            for member in raw_list:
                uid = str(member.get("user_id", ""))
                card = member.get("card", "") or member.get("nickname", "")
                if uid:
                    members[uid] = card
            return members
        except Exception:
            logger.exception("Failed to list QQ group members")
            return {}

    async def _ws_api_call(self, action: str, **params: Any) -> dict[str, Any] | None:
        if self._ws is None:
            logger.warning("WebSocket not connected, cannot call action: %s", action)
            return None

        echo = str(uuid.uuid4())
        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("API call timed out: %s (echo=%s)", action, echo)
            return None
        except Exception:
            logger.exception("API call failed: %s", action)
            return None
        finally:
            self._pending_actions.pop(echo, None)

    async def _ws_event_loop(self) -> None:
        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0
                    logger.info("Connected to OneBot WebSocket at %s", self._ws_url)
                    async for raw in ws:
                        try:
                            await self._dispatch_ws_message(raw)
                        except Exception:
                            logger.exception(
                                "Unhandled error dispatching WebSocket message"
                            )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning(
                    "WebSocket disconnected, reconnecting in %.1fs",
                    self._reconnect_delay,
                )
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self._max_reconnect_delay,
                    )

    async def _dispatch_ws_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from OneBot WebSocket: %s", raw[:200])
            return

        echo = data.get("echo")
        if echo is not None:
            future = self._pending_actions.pop(echo, None)
            if future is not None and not future.done():
                future.set_result(data)
            return

        post_type = data.get("post_type")
        if post_type == "message":
            message_type = data.get("message_type")
            if message_type == "group":
                asyncio.create_task(self._on_group_message(data))
            elif message_type == "private":
                asyncio.create_task(self._on_private_message(data))

    async def _on_group_message(self, data: dict[str, Any]) -> None:
        group_id = str(data.get("group_id", ""))
        if group_id != str(self._group_id):
            return

        sender = data.get("sender", {})
        user_id = str(sender.get("user_id", data.get("user_id", "")))

        if str(user_id) == str(self._bot_qq):
            return

        raw_message = data.get("message", "")
        if isinstance(raw_message, str):
            segments = parse_cq_code(raw_message)
        elif isinstance(raw_message, list):
            segments = parse_onebot_array(raw_message)
        else:
            segments = []

        nickname = sender.get("card", "") or sender.get("nickname", "")

        event = MessageEvent(
            message_id=str(data.get("message_id", "")),
            platform="qq",
            channel_id=group_id,
            author_id=user_id,
            author_name=nickname,
            segments=segments,
            timestamp=datetime.now(),
        )

        await self._trigger_on_message(event)

    async def _on_private_message(self, data: dict[str, Any]) -> None:
        """处理 QQ 私信消息."""
        sender = data.get("sender", {})
        user_id = str(sender.get("user_id", data.get("user_id", "")))

        if str(user_id) == str(self._bot_qq):
            return

        raw_message = data.get("message", "")
        if isinstance(raw_message, str):
            segments = parse_cq_code(raw_message)
        elif isinstance(raw_message, list):
            segments = parse_onebot_array(raw_message)
        else:
            segments = []

        nickname = sender.get("card", "") or sender.get("nickname", "")

        logger.info("Private message from QQ user %s (%s)", user_id, nickname)

        event = MessageEvent(
            message_id=str(data.get("message_id", "")),
            platform="qq",
            channel_id="",
            author_id=user_id,
            author_name=nickname,
            segments=segments,
            timestamp=datetime.now(),
            is_private=True,
        )

        await self._trigger_on_message(event)

    async def send_private_msg(self, user_id: int, message: str) -> bool:
        """向 QQ 用户发送私信 (调用 OneBot send_private_msg). """
        try:
            result = await self._ws_api_call(
                "send_private_msg",
                user_id=user_id,
                group_id=self._group_id,
                message=message,
            )
            if result is None:
                logger.warning("send_private_msg returned None for user %s", user_id)
                return False
            data = result.get("data") if isinstance(result, dict) else None
            status = result.get("status")
            success = data is not None or status == "ok"
            if success:
                logger.info("Private message sent to QQ user %s", user_id)
            else:
                logger.warning(
                    "send_private_msg failed for user %s: status=%s data=%s",
                    user_id, status, data,
                )
            return bool(success)
        except Exception:
            logger.exception("Failed to send private message to QQ user %s", user_id)
            return False