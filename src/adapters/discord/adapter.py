from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
from typing import Any
from urllib.parse import urlparse

import discord
import httpx

from bridge.segment.base import MessageSegment
from bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_AT_ALL,
    SEGMENT_EMOJI,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_STICKER,
    SEGMENT_TEXT,
    at_segment,
    reply_segment,
    text_segment,
)
from adapters.base import MessageEvent, PlatformAdapter

logger = logging.getLogger(__name__)

# Combined regex to parse both custom emoji and Discord @mentions in a single pass
COMBINED_MARKUP_RE = re.compile(r"(<a?:(\w+):(\d+)>|<@!?(\d+)>)")

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


class _DiscordClient(discord.Client):
    def __init__(self, adapter: DiscordAdapter) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents, proxy=adapter._proxy)
        self._adapter = adapter

    async def on_ready(self) -> None:
        logger.info("Discord client logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if str(message.channel.id) != self._adapter._channel_id:
            return
        await self._adapter._on_discord_message(message)


class DiscordAdapter(PlatformAdapter):
    def __init__(self, token: str, channel_id: str, proxy: str | None = None) -> None:
        super().__init__()
        self._token = token
        self._channel_id = channel_id
        self._proxy = proxy
        self._client = _DiscordClient(self)

    async def start(self) -> None:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                self._client = _DiscordClient(self)
                await self._client.start(self._token)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    delay = (attempt + 1) * 2.0
                    logger.warning(
                        "Discord connection failed (attempt %d/3), retrying in %.0fs",
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
        raise RuntimeError("Discord connection failed after 3 attempts") from last_exc

    @property
    def bot_user_id(self) -> str | None:
        if self._client.user is None:
            return None
        return str(self._client.user.id)

    def status_info(self) -> dict:
        return {
            "type": "Discord",
            "name": "Discord",
            "connected": self._client.is_ready(),
            "user": str(self._client.user) if self._client.user else None,
        }

    async def stop(self) -> None:
        await self._client.close()

    async def send_message(
        self,
        channel_id: str,
        segments: list[Any],
        reply_to: str | None = None,
    ) -> str | None:
        try:
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(channel_id))

            text_content = ""
            files: list[discord.File] = []

            for seg in segments:
                if seg.type == SEGMENT_TEXT:
                    text_content += seg.data.get("text", "")
                elif seg.type == SEGMENT_IMAGE:
                    await self._try_attach_file(seg.data.get("file", ""), "image", files)
                elif seg.type == SEGMENT_STICKER:
                    await self._try_attach_file(
                        seg.data.get("url", ""),
                        seg.data.get("name", "sticker"),
                        files,
                    )
                elif seg.type == SEGMENT_EMOJI:
                    text_content += seg.data.get("unicode", "")
                elif seg.type == SEGMENT_AT:
                    user_id = seg.data.get("user_id", "")
                    text_content += f"<@{user_id}>"
                elif seg.type == SEGMENT_AT_ALL:
                    text_content += "@everyone"
                elif seg.type == SEGMENT_REPLY:
                    text_content += seg.data.get("content", "")

            text_content = text_content.strip()

            kwargs: dict[str, Any] = {}
            if text_content:
                kwargs["content"] = text_content
            if files:
                kwargs["files"] = files
            if reply_to is not None:
                kwargs["reference"] = channel.get_partial_message(int(reply_to))

            if not kwargs:
                return None

            msg = await channel.send(**kwargs)
            return str(msg.id)
        except Exception:
            logger.exception("Failed to send Discord message")
            return None

    async def _try_attach_file(
        self,
        url: str,
        base_name: str,
        files: list[discord.File],
    ) -> None:
        if not url:
            return
        file_data = await self._download_file(url)
        if file_data is None:
            return
        ext = self._guess_extension(url, file_data)
        fp = io.BytesIO(file_data)
        files.append(discord.File(fp, filename=f"{base_name}.{ext}"))

    async def _download_file(self, url: str) -> bytes | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://multimedia.nt.qq.com.cn/",
        }
        client_kwargs: dict[str, Any] = {
            "timeout": 15.0,
            "follow_redirects": True,
            "headers": headers,
        }
        proxy = self._proxy
        if proxy:
            client_kwargs["proxy"] = proxy

        last_error: Exception | None = None
        for attempt, use_proxy in enumerate([True, False]):
            if attempt == 1 and proxy:
                logger.debug("Image download retrying without proxy: %s", url[:80])
                del client_kwargs["proxy"]
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.content
                    logger.warning(
                        "Image download failed (HTTP %d): %s",
                        resp.status_code,
                        url[:80],
                    )
                    return None
            except httpx.ProxyError as e:
                logger.warning("Image download proxy error, will retry directly: %s", url[:80])
                last_error = e
            except httpx.TimeoutException as e:
                logger.warning("Image download timed out: %s", url[:80])
                last_error = e
            except Exception as e:
                logger.warning("Image download exception: %s - %s", type(e).__name__, url[:80])
                last_error = e
                break

        if last_error:
            logger.warning("Image download failed after all attempts: %s", url[:80])
        return None

    @staticmethod
    def _guess_extension(url: str, data: bytes) -> str:
        parsed = urlparse(url)
        path = parsed.path
        if "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            if ext in IMAGE_EXTENSIONS:
                return ext
        if data[:3] == b"\xff\xd8\xff":
            return "jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"\x89PNG":
            return "png"
        return "png"

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        segments: list[Any],
    ) -> None:
        try:
            channel = self._client.get_channel(int(channel_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(channel_id))

            msg = await channel.fetch_message(int(message_id))
            content = self._segments_to_string(segments)
            await msg.edit(content=content)
        except Exception:
            logger.exception("Failed to edit Discord message")

    async def list_members(self, channel_id: str) -> dict[str, str]:
        channel = self._client.get_channel(int(channel_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(channel_id))

        guild = channel.guild
        return {str(m.id): m.display_name for m in guild.members}

    async def _on_discord_message(self, message: discord.Message) -> None:
        segments: list[MessageSegment] = []

        if message.reference and message.reference.message_id:
            segments.append(
                reply_segment(
                    platform="discord",
                    msg_id=str(message.reference.message_id),
                    content="",
                )
            )

        if message.content:
            last_end = 0
            for match in COMBINED_MARKUP_RE.finditer(message.content):
                start, end = match.start(), match.end()
                if start > last_end:
                    segments.append(text_segment(message.content[last_end:start]))

                if match.group(4) is not None:
                    uid = match.group(4)
                    member = message.guild.get_member(int(uid))
                    display_name = member.display_name if member else uid
                    segments.append(at_segment(platform="discord", user_id=uid, display=display_name))
                else:
                    emoji_id = match.group(3)
                    is_animated = match.group(0).startswith("<a:")
                    ext = "gif" if is_animated else "png"
                    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
                    segments.append(
                        MessageSegment(type=SEGMENT_IMAGE, data={"file": url})
                    )

                last_end = end

            if last_end < len(message.content):
                segments.append(text_segment(message.content[last_end:]))

        for attachment in message.attachments:
            # 优先在 Discord 侧下载图片（带代理支持），base64 编码后直传下游，
            # 避免 URL 被 OneBot CQ 码解析器截断或 CDN 被墙导致下载失败
            b64 = None
            file_data = await self._download_file(attachment.url)
            if file_data:
                b64 = base64.b64encode(file_data).decode()
                logger.debug("Downloaded attachment via CDN: %s (%d bytes)", attachment.url[:60], len(file_data))
            else:
                file_data = await self._download_file(attachment.proxy_url)
                if file_data:
                    b64 = base64.b64encode(file_data).decode()
                    logger.debug("Downloaded attachment via proxy: %s (%d bytes)", attachment.proxy_url[:60], len(file_data))
                else:
                    logger.warning("Failed to download attachment: %s", attachment.url[:80])

            if b64:
                segments.append(
                    MessageSegment(type=SEGMENT_IMAGE, data={"file": f"base64://{b64}"})
                )
            else:
                # 下载完全失败时仍传原始 URL，让下游 OneBot 尝试（虽然可能被截断或墙）
                segments.append(
                    MessageSegment(type=SEGMENT_IMAGE, data={"file": attachment.url})
                )

        for sticker in message.stickers:
            segments.append(
                MessageSegment(
                    type=SEGMENT_STICKER,
                    data={
                        "name": sticker.name,
                        "url": sticker.url,
                    },
                )
            )

        event = MessageEvent(
            message_id=str(message.id),
            platform="discord",
            channel_id=str(message.channel.id),
            author_id=str(message.author.id),
            author_name=message.author.display_name,
            segments=segments,
            timestamp=message.created_at,
        )
        await self._trigger_on_message(event)

    @staticmethod
    def _segments_to_string(segments: list[Any]) -> str:
        parts: list[str] = []
        for seg in segments:
            if seg.type == SEGMENT_TEXT:
                parts.append(seg.data.get("text", ""))
            elif seg.type == SEGMENT_IMAGE:
                parts.append(seg.data.get("file", ""))
            elif seg.type == SEGMENT_AT:
                user_id = seg.data.get("user_id", "")
                parts.append(f"<@{user_id}>")
            elif seg.type == SEGMENT_AT_ALL:
                parts.append("@everyone")
            elif seg.type == SEGMENT_EMOJI:
                parts.append(seg.data.get("unicode", ""))
            elif seg.type == SEGMENT_REPLY:
                parts.append(seg.data.get("content", ""))
            elif seg.type == SEGMENT_STICKER:
                parts.append(seg.data.get("url", ""))
        return " ".join(parts).strip()
