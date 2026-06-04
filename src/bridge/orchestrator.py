from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from typing import TYPE_CHECKING, Any

from bridge.message_store import MessageStore
from bridge.segment.base import MessageSegment
from bridge.segment.converter import (
    DIR_DISCORD_TO_QQ,
    DIR_QQ_TO_DISCORD,
    SegmentConverter,
)
from bridge.segment.types import (
    SEGMENT_AT,
    SEGMENT_IMAGE,
    SEGMENT_REPLY,
    SEGMENT_TEXT,
    at_segment,
    text_segment,
)

MENTION_TEXT_RE = re.compile(r"@([^\s]+)")

if TYPE_CHECKING:
    from adapters.base import MessageEvent, PlatformAdapter
    from bridge.translator import Translator
    from models.config_model import BridgeConfig

from adapters.qq.adapter import QQAdapter

__all__ = [
    "PendingTranslation",
    "Orchestrator",
]


class PendingTranslation:
    source_adapter: PlatformAdapter
    target_channel_id: str
    message_id: str
    original_segments: list[MessageSegment]
    translated_text: str | None
    text_indices: list[int]
    direction: str
    event_id: str
    author_name: str
    original_text: str

    def __init__(
        self,
        source_adapter: PlatformAdapter,
        target_channel_id: str,
        message_id: str,
        original_segments: list[MessageSegment],
        text_indices: list[int],
        direction: str,
        event_id: str,
        author_name: str = "",
        original_text: str = "",
    ) -> None:
        self.source_adapter = source_adapter
        self.target_channel_id = target_channel_id
        self.message_id = message_id
        self.original_segments = original_segments
        self.translated_text = None
        self.text_indices = text_indices
        self.direction = direction
        self.event_id = event_id
        self.author_name = author_name
        self.original_text = original_text


def _debug_message_preview(segments: list, max_len: int = 60) -> str:
    texts: list[str] = []
    for seg in segments:
        if seg.type == SEGMENT_TEXT:
            texts.append(seg.data.get("text", ""))
    preview = "".join(texts).strip()
    if not preview:
        return "(无文本)"
    if len(preview) > max_len:
        preview = preview[:max_len] + "..."
    return preview


def _build_prefix(platform_tag: str, author_name: str) -> MessageSegment:
    return text_segment(f"{author_name}：")


def _build_translation_sep(text: str) -> MessageSegment:
    return text_segment(f"\n{'─' * 6} {'翻译'} {'─' * 6}\n{text}")


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize('NFKC', text)
    return ''.join(normalized.split())


def _extract_reply_segment(
    segments: list[MessageSegment],
) -> tuple[list[MessageSegment], str | None, str | None]:
    remaining: list[MessageSegment] = []
    reply_platform: str | None = None
    reply_msg_id: str | None = None
    for seg in segments:
        if seg.type == SEGMENT_REPLY and reply_msg_id is None:
            reply_platform = seg.data.get("platform")
            reply_msg_id = seg.data.get("msg_id")
        else:
            remaining.append(seg)
    if reply_platform and reply_msg_id:
        return remaining, reply_platform, reply_msg_id
    return segments, None, None


class Orchestrator:
    def __init__(
        self,
        config: BridgeConfig,
        message_store: MessageStore,
        debug: bool = False,
    ) -> None:
        self._config = config
        self._message_store = message_store
        self._debug = debug
        self._start_time = time.monotonic()

        self._pending: dict[str, PendingTranslation] = {}

        self.discord_adapter: PlatformAdapter | None = None
        self.qq_adapter: PlatformAdapter | None = None
        self.translator: Translator | None = None
        self.converter: SegmentConverter = SegmentConverter()
        self.matcher: Any = None

        self._discord_channel_id: str = ""
        self._qq_group_id: str = ""

        self._adapter_tasks: list[asyncio.Task[None]] = []

    async def handle_qq_message(self, event: MessageEvent) -> None:
        if self.discord_adapter is None:
            return

        if self._debug:
            preview = _debug_message_preview(event.segments)
            print(f"[DEBUG] 收到 QQ 消息 | {event.author_name}: {preview}", flush=True)

        segments, reply_platform, reply_msg_id = _extract_reply_segment(event.segments)

        bot_qq_id = getattr(self.qq_adapter, "bot_user_id", None)

        has_bot_mention = False
        if bot_qq_id:
            cleaned_segments = []
            for seg in segments:
                if (seg.type == SEGMENT_AT
                        and seg.data.get("platform") == "qq"
                        and seg.data.get("user_id") == bot_qq_id):
                    has_bot_mention = True
                    continue
                cleaned_segments.append(seg)
            segments = cleaned_segments

        has_bot_reply = False
        reply_to: str | None = None
        if reply_platform == "qq" and reply_msg_id:
            resolved = self._message_store.get_counterpart("qq", reply_msg_id)
            if resolved:
                has_bot_reply = True
                reply_to = resolved

        if not has_bot_mention and not has_bot_reply:
            if self._debug:
                print(f"[DEBUG] 跳过 QQ 消息 | {event.author_name}: 未@bot 且未回复 bot 消息", flush=True)
            return

        segments = self._resolve_text_mentions(segments, "discord")
        converted = self.converter.convert_all(DIR_QQ_TO_DISCORD, segments)

        for i, seg in enumerate(converted):
            if seg.type == SEGMENT_IMAGE:
                file_str = seg.data.get("file", "")
                if file_str and not (
                    file_str.startswith("http")
                    or file_str.startswith("file://")
                    or file_str.startswith("base64://")
                ):
                    url = None
                    if isinstance(self.qq_adapter, QQAdapter):
                        try:
                            url = await self.qq_adapter.resolve_image_url(file_str)
                        except Exception:
                            if self._debug:
                                print(f"[DEBUG] resolve_image_url exception for {file_str}", flush=True)
                    if url:
                        converted[i] = MessageSegment(
                            type=SEGMENT_IMAGE,
                            data={"file": url},
                        )
                        if self._debug:
                            print(f"[DEBUG] 已解析 QQ 图片: {file_str} -> {url[:60]}...", flush=True)
                    else:
                        if self._debug:
                            print(f"[DEBUG] 无法解析 QQ 图片，使用文本占位: {file_str}", flush=True)
                        converted[i] = text_segment("[图片]")

        if reply_platform and reply_msg_id and reply_to is None:
            converted.insert(0, text_segment("[回复消息]"))

        _, original_text = self.translator.extract_text_segments(converted) if self.translator else ("", "")

        translated: str | None = None
        if self.translator and original_text:
            skip_translation = self.translator.should_skip(original_text)
            if not skip_translation and "/distrans" in original_text:
                skip_translation = True
                if self._debug:
                    print(f"[DEBUG] 跳过翻译 (QQ→Discord) | /distrans detected", flush=True)

            if not skip_translation:
                if self._debug:
                    print(f"[DEBUG] 开始翻译 (QQ→Discord) | {original_text[:40]}...", flush=True)
                translated = await self.translator.translate(original_text, target_lang="英文")
                if translated is not None:
                    if self._debug:
                        print(f"[DEBUG] 翻译完成 | length={len(translated)}", flush=True)
                    if _normalize_text(translated) == _normalize_text(original_text):
                        if self._debug:
                            print(f"[DEBUG] 跳过翻译结果 | 译文与原文相同（规范化后）", flush=True)
                        translated = None
            text = f"`{event.author_name}`: {translated}"
            if original_text:
                text += "\n-# └─ " + original_text.replace("\n", "\n-# ")
            segments_to_send = [text_segment(text)]
        else:
            prefix = _build_prefix("QQ", event.author_name)
            segments_to_send = [prefix] + converted

        msg_id = await self.discord_adapter.send_message(
            self._discord_channel_id,
            segments_to_send,
            reply_to=reply_to,
        )
        if msg_id is None:
            if self._debug:
                print(f"[DEBUG] 发送到 Discord 失败 | {event.author_name}", flush=True)
            return

        self._message_store.record(qq_msg_id=event.message_id, discord_msg_id=msg_id)

        if self._debug:
            print(f"[DEBUG] 发送到 Discord 成功 | message_id={msg_id}", flush=True)

    async def handle_discord_message(self, event: MessageEvent) -> None:
        if self.qq_adapter is None:
            return

        if self._debug:
            preview = _debug_message_preview(event.segments)
            print(f"[DEBUG] 收到 Discord 消息 | {event.author_name}: {preview}", flush=True)

        segments, reply_platform, reply_msg_id = _extract_reply_segment(event.segments)

        reply_to: str | None = None
        if reply_platform == "discord" and reply_msg_id:
            resolved = self._message_store.get_counterpart("discord", reply_msg_id)
            if resolved:
                reply_to = resolved

        segments = self._resolve_text_mentions(segments, "qq")
        converted = self.converter.convert_all(DIR_DISCORD_TO_QQ, segments)

        if reply_platform and reply_msg_id and reply_to is None:
            converted.insert(0, text_segment("[回复消息]"))

        prefix = _build_prefix("Discord", event.author_name)

        _, original_text = self.translator.extract_text_segments(converted) if self.translator else ("", "")

        if self.translator and original_text and not self.translator.should_skip(original_text):
            if self._debug:
                print(f"[DEBUG] 开始翻译 (Discord→QQ) | {original_text[:40]}...", flush=True)
            translated = await self.translator.translate(original_text, target_lang="中文")
            if translated is not None:
                if self._debug:
                    print(f"[DEBUG] 翻译完成 | length={len(translated)}", flush=True)
                new_prefix = text_segment(f"{event.author_name}：{translated}\n└─ ")
                segments_to_send = [new_prefix] + converted
            else:
                if self._debug:
                    print(f"[DEBUG] 翻译失败，仅发送原文", flush=True)
                segments_to_send = [prefix] + converted
        else:
            segments_to_send = [prefix] + converted

        msg_id = await self.qq_adapter.send_message(
            self._qq_group_id,
            segments_to_send,
            reply_to=reply_to,
        )
        if msg_id:
            self._message_store.record(qq_msg_id=msg_id, discord_msg_id=event.message_id)
        if self._debug:
            if msg_id:
                print(f"[DEBUG] 发送到 QQ 成功 | message_id={msg_id}", flush=True)
            else:
                print(f"[DEBUG] 发送到 QQ 失败 | {event.author_name}", flush=True)

    async def _on_translation_complete(self, pending: PendingTranslation) -> None:
        self._pending.pop(pending.event_id, None)
        if pending.direction == DIR_QQ_TO_DISCORD:
            if pending.translated_text is None:
                return
            if self._debug:
                print(f"[DEBUG] 翻译完成，编辑 Discord 消息 | message_id={pending.message_id}", flush=True)

            # 新版格式（Discord markdown）：
            # [QQ] 昵称：Translation
            # └─ Chinese original text
            text = f"`{pending.author_name}`: {pending.translated_text}"
            if pending.original_text:
                text += "\n-# └─ " + pending.original_text.replace("\n", "\n-# ")
            # 仅编辑文本，图片已在原始消息中以附件形式发送
            new_segments = [text_segment(text)]

            await pending.source_adapter.edit_message(
                pending.target_channel_id,
                pending.message_id,
                new_segments,
            )
            if self._debug:
                print(f"[DEBUG] Discord 消息已更新 | message_id={pending.message_id}", flush=True)

    async def register_adapters(
        self,
        discord_adapter: PlatformAdapter,
        qq_adapter: PlatformAdapter,
        discord_channel_id: str,
        qq_group_id: str,
    ) -> None:
        self.discord_adapter = discord_adapter
        self.qq_adapter = qq_adapter
        self._discord_channel_id = discord_channel_id
        self._qq_group_id = qq_group_id

        discord_adapter.set_on_message(self.handle_discord_message)
        qq_adapter.set_on_message(self.handle_qq_message)

    def _resolve_text_mentions(
        self, segments: list[MessageSegment], target_platform: str,
    ) -> list[MessageSegment]:
        if self.matcher is None:
            return segments
        result: list[MessageSegment] = []
        for seg in segments:
            if seg.type != SEGMENT_TEXT:
                result.append(seg)
                continue
            text = seg.data.get("text", "")
            if not text or "@" not in text:
                result.append(seg)
                continue
            last_end = 0
            for match in MENTION_TEXT_RE.finditer(text):
                start, end = match.start(), match.end()
                if start > last_end:
                    result.append(text_segment(text[last_end:start]))
                name = match.group(1)
                matched = self.matcher.match_user(name, target_platform)
                if matched is not None:
                    user_id, display_name = matched
                    if self._debug:
                        print(f"[DEBUG] 文本 @{name} -> 匹配 {target_platform} 用户 {display_name} ({user_id})", flush=True)
                    if target_platform == "discord":
                        result.append(text_segment(f"<@{user_id}>"))
                    else:
                        result.append(at_segment("qq", user_id, display_name))
                else:
                    if self._debug:
                        print(f"[DEBUG] 文本 @{name} -> 未在 {target_platform} 中找到匹配", flush=True)
                    result.append(text_segment(f"@{name}"))
                last_end = end
            if last_end < len(text):
                result.append(text_segment(text[last_end:]))
        return result

    async def start(self) -> None:
        tasks = []
        if self.discord_adapter is not None:
            tasks.append(asyncio.create_task(self.discord_adapter.start()))
        if self.qq_adapter is not None:
            tasks.append(asyncio.create_task(self.qq_adapter.start()))
        if self._debug:
            tasks.append(asyncio.create_task(self._heartbeat_loop()))
        if not tasks:
            return

        tasks.append(asyncio.create_task(self._cache_refresh_loop()))

        self._adapter_tasks = tasks
        await asyncio.gather(*tasks)

    async def _cache_refresh_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            if self.discord_adapter is not None and self.matcher is not None:
                try:
                    await self.matcher.refresh_cache(
                        "discord", self.discord_adapter, self._discord_channel_id
                    )
                    if self._debug:
                        dc_count = len(self.matcher._cache.get("discord", {}))
                        print(f"[DEBUG] 缓存刷新: Discord 成员数={dc_count}", flush=True)
                except Exception:
                    pass
            if self.qq_adapter is not None and self.matcher is not None:
                try:
                    await self.matcher.refresh_cache(
                        "qq", self.qq_adapter, self._qq_group_id
                    )
                    if self._debug:
                        qq_count = len(self.matcher._cache.get("qq", {}))
                        print(f"[DEBUG] 缓存刷新: QQ 成员数={qq_count}", flush=True)
                except Exception:
                    pass
            await asyncio.sleep(300)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(600)
            elapsed = time.monotonic() - self._start_time
            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                uptime = f"{hours}时{mins}分{secs}秒"
            else:
                uptime = f"{mins}分{secs}秒"

            parts = [f"═══ 心跳状态 (运行时间: {uptime}) ═══"]

            if self.discord_adapter:
                info = self.discord_adapter.status_info()
                status = "● 已连接" if info.get("connected") else "○ 未连接"
                user = info.get("user") or "-"
                parts.append(f"  Discord  │ {status} │ {user}")

            if self.qq_adapter:
                info = self.qq_adapter.status_info()
                status = "● 已连接" if info.get("connected") else "○ 未连接"
                group = info.get("group_id", "-")
                parts.append(f"  QQ       │ {status} │ 群 {group}")

            if self.translator:
                cache_size = len(self.translator._cache)
                parts.append(f"  翻译器   │ 缓存: {cache_size} 条")

            parts.append(f"  待处理   │ {len(self._pending)} 条待翻译")
            parts.append("═" * max(30, len(parts[0])))

            print("\n" + "\n".join(parts), flush=True)

    async def stop(self) -> None:
        for task in self._adapter_tasks:
            if not task.done():
                task.cancel()
        if self._adapter_tasks:
            await asyncio.wait(self._adapter_tasks)

        if self.discord_adapter is not None:
            try:
                await self.discord_adapter.stop()
            except Exception:
                pass
        if self.qq_adapter is not None:
            try:
                await self.qq_adapter.stop()
            except Exception:
                pass
