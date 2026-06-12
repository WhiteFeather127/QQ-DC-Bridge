from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import unicodedata
from typing import TYPE_CHECKING, Any

import discord

from bridge.bind_manager import BindError, BindManager
from bridge.message_store import MessageStore

logger = logging.getLogger(__name__)
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
from bridge.verification import VerificationManager

MENTION_TEXT_RE = re.compile(r"@([^\s]+)")
MENTION_DISCORD_USER_RE = re.compile(r"<@!?(\d+)>")
BIND_COMMAND_RE = re.compile(r"^/bind\s+")

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
    return ''.join(normalized.split()).casefold()


_MD5_HEX_RE = re.compile(r'^([0-9a-fA-F]{32})')


def _try_gchatpic_new_url(file_uuid: str) -> str | None:
    match = _MD5_HEX_RE.match(file_uuid.strip())
    if match:
        md5_upper = match.group(1).upper()
        return f"http://gchat.qpic.cn/gchatpic_new/0/0-0-{md5_upper}/0"
    return None


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
        bind_manager: BindManager | None = None,
        verification_manager: VerificationManager | None = None,
        debug: bool = False,
    ) -> None:
        self._config = config
        self._message_store = message_store
        self._bind_manager = bind_manager or BindManager(config.data_dir)
        self._verification_manager = verification_manager or VerificationManager()
        self._debug = debug
        self._start_time = time.monotonic()

        self._pending: dict[str, PendingTranslation] = {}
        self._bind_lock = asyncio.Lock()

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

        # 私信消息路由到绑定命令处理器
        if event.is_private:
            await self.handle_private_message(event)
            return

        # 绑定昵称替换
        author_name = self._resolve_author_display_name(
            "qq", event.author_id, event.author_name,
        )

        # 已绑定用户：作者前缀用 <@discord_id> 格式
        bound_discord_id: str | None = None
        author_prefix: str | None = None
        if self._bind_manager is not None:
            bound_discord_id = self._bind_manager.get_counterpart("qq", event.author_id)
            if bound_discord_id is not None:
                author_prefix = f"<@{bound_discord_id}>: "

        if self._debug:
            preview = _debug_message_preview(event.segments)
            print(f"[DEBUG] 收到 QQ 消息 | {event.author_name} ({author_name}): {preview}", flush=True)

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
                print(f"[DEBUG] 跳过 QQ 消息 | {author_name}: 未@bot 且未回复 bot 消息", flush=True)
            return

        segments = self._resolve_text_mentions(segments, "discord")
        converted = self.converter.convert_all(DIR_QQ_TO_DISCORD, segments)

        # 在图片 URL 解析前检查原始消息是否有文本段，避免 [图片] 占位干扰判断
        has_original_text = any(seg.type == SEGMENT_TEXT for seg in converted)

        for i, seg in enumerate(converted):
            if seg.type == SEGMENT_IMAGE:
                file_str = seg.data.get("file", "")
                file_id = seg.data.get("file_id", "")

                # Strategy 1: Read image from NapCat local cache (bypasses broken CDN URLs)
                if file_id and isinstance(self.qq_adapter, QQAdapter):
                    image_data = await self.qq_adapter.get_image_data(file_id)
                    if image_data:
                        b64 = base64.b64encode(image_data).decode()
                        converted[i] = MessageSegment(
                            type=SEGMENT_IMAGE,
                            data={"file": f"base64://{b64}"},
                        )
                        if self._debug:
                            print(f"[DEBUG] 已从本地缓存读取图片: {file_id[:20]}...", flush=True)
                        continue

                # Strategy 2: URL conversion for gchat.qpic.cn/download links
                if file_str and (
                    file_str.startswith("http")
                    and "gchat.qpic.cn/download" in file_str
                ):
                    file_id = seg.data.get("file_id", "")
                    if file_id:
                        gchatpic_url = _try_gchatpic_new_url(file_id)
                        if gchatpic_url:
                            converted[i] = MessageSegment(
                                type=SEGMENT_IMAGE,
                                data={"file": gchatpic_url},
                            )
                            if self._debug:
                                print(f"[DEBUG] 已替换 gchatpic 直链: {file_str[:60]}... -> {gchatpic_url}", flush=True)
                            continue

                # Strategy 3: Try gchatpic or resolve_image_url for local UUIDs
                if file_str and not (
                    file_str.startswith("http")
                    or file_str.startswith("file://")
                    or file_str.startswith("base64://")
                ):
                    gchatpic_url = _try_gchatpic_new_url(file_str)
                    if gchatpic_url:
                        converted[i] = MessageSegment(
                            type=SEGMENT_IMAGE,
                            data={"file": gchatpic_url},
                        )
                        if self._debug:
                            print(f"[DEBUG] 已构造 gchatpic 直链: {file_str} -> {gchatpic_url}", flush=True)
                        continue

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
        original_text = original_text.strip()

        has_distrans = "/distrans" in original_text

        # 从 converted 中过滤掉 /distrans 命令文本，避免泄露到目标平台
        if has_distrans:
            for seg in converted:
                if seg.type == SEGMENT_TEXT:
                    seg.data["text"] = seg.data.get("text", "").replace("/distrans", "").strip()
            # 重新提取清理后的文本
            _, original_text = self.translator.extract_text_segments(converted) if self.translator else ("", "")
            original_text = original_text.strip()
            if self._debug:
                print(f"[DEBUG] 已过滤 /distrans 命令文本 (QQ→Discord)", flush=True)

        skip_translation = False
        if not has_original_text:
            skip_translation = True
            if self._debug:
                print(f"[DEBUG] 跳过翻译 (QQ→Discord) | 纯非文本消息", flush=True)
        elif self.translator and original_text:
            if self.translator.should_skip(original_text):
                skip_translation = True
            elif has_distrans:
                skip_translation = True
                if self._debug:
                    print(f"[DEBUG] 跳过翻译 (QQ→Discord) | /distrans detected", flush=True)

        if self.translator and original_text and not skip_translation:
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

            if translated is not None:
                if author_prefix:
                    text = f"{author_prefix}{translated}"
                else:
                    text = f"`{author_name}`: {translated}"
                if original_text:
                    text += "\n-# └─ " + original_text.replace("\n", "\n-# ")
                # 过滤掉文本段（已被翻译替代），保留非文本段（图片、贴纸等）
                non_text_segments = [seg for seg in converted if seg.type != SEGMENT_TEXT]
                segments_to_send = [text_segment(text)] + non_text_segments
            else:
                # 翻译失败或与原文相同，走原文转发路径
                prefix = text_segment(author_prefix or f"`{author_name}`: ")
                segments_to_send = [prefix] + converted
        else:
            prefix = text_segment(author_prefix or f"`{author_name}`: ")
            segments_to_send = [prefix] + converted

        msg_id = await self.discord_adapter.send_message(
            self._discord_channel_id,
            segments_to_send,
            reply_to=reply_to,
            allowed_mentions=self._build_allowed_mentions(segments_to_send, bound_discord_id),
        )
        if msg_id is None:
            if self._debug:
                print(f"[DEBUG] 发送到 Discord 失败 | {author_name}", flush=True)
            return

        self._message_store.record(qq_msg_id=event.message_id, discord_msg_id=msg_id)

        if self._debug:
            print(f"[DEBUG] 发送到 Discord 成功 | message_id={msg_id}", flush=True)

    async def handle_discord_message(self, event: MessageEvent) -> None:
        if self.qq_adapter is None:
            return

        # 私信消息路由到绑定命令处理器
        if event.is_private:
            await self.handle_private_message(event)
            return

        # 绑定昵称替换
        author_name = self._resolve_author_display_name(
            "discord", event.author_id, event.author_name,
        )

        if self._debug:
            preview = _debug_message_preview(event.segments)
            print(f"[DEBUG] 收到 Discord 消息 | {event.author_name} ({author_name}): {preview}", flush=True)

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

        prefix = _build_prefix("Discord", author_name)

        _, original_text = self.translator.extract_text_segments(converted) if self.translator else ("", "")
        original_text = original_text.strip()

        has_distrans = "/distrans" in original_text

        # 从 converted 中过滤掉 /distrans 命令文本，避免泄露到目标平台
        if has_distrans:
            for seg in converted:
                if seg.type == SEGMENT_TEXT:
                    seg.data["text"] = seg.data.get("text", "").replace("/distrans", "").strip()
            # 重新提取清理后的文本
            _, original_text = self.translator.extract_text_segments(converted) if self.translator else ("", "")
            original_text = original_text.strip()
            if self._debug:
                print(f"[DEBUG] 已过滤 /distrans 命令文本 (Discord→QQ)", flush=True)

        skip_translation = False
        if not any(seg.type == SEGMENT_TEXT for seg in converted):
            skip_translation = True
            if self._debug:
                print(f"[DEBUG] 跳过翻译 (Discord→QQ) | 纯非文本消息", flush=True)
        elif self.translator and original_text:
            if self.translator.should_skip(original_text):
                skip_translation = True
            elif has_distrans:
                skip_translation = True
                if self._debug:
                    print(f"[DEBUG] 跳过翻译 (Discord→QQ) | /distrans detected", flush=True)

        if self.translator and original_text and not skip_translation:
            if self._debug:
                print(f"[DEBUG] 开始翻译 (Discord→QQ) | {original_text[:40]}...", flush=True)
            translated = await self.translator.translate(original_text, target_lang="中文")
            if translated is not None:
                if self._debug:
                    print(f"[DEBUG] 翻译完成 | length={len(translated)}", flush=True)
                if _normalize_text(translated) == _normalize_text(original_text):
                    if self._debug:
                        print(f"[DEBUG] 跳过翻译结果 | 译文与原文相同（规范化后）", flush=True)
                    translated = None

            if translated is not None:
                new_prefix = text_segment(f"{author_name}：{translated}\n└─ ")
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
                print(f"[DEBUG] 发送到 QQ 失败 | {author_name}", flush=True)

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

    # ── 私信命令处理（绑定/解绑） ──────────────────────────────

    @staticmethod
    def _l10n(platform: str, zh: str, en: str) -> str:
        """根据平台返回中文或英文提示."""
        return zh if platform == "qq" else en

    async def handle_private_message(self, event: MessageEvent) -> None:
        """处理私信命令：/bind /unbind /验证码."""
        text = self._extract_private_text(event.segments)
        if not text:
            return

        logger.info(
            "Private message from %s:%s: %s",
            event.platform, event.author_id, text[:80],
        )

        if text.startswith("/bind"):
            await self._handle_bind_command(event, text)
        elif text.startswith("/unbind"):
            await self._handle_unbind_command(event)
        elif text.strip().isdigit() and 4 <= len(text.strip()) <= 6:
            await self._handle_verification_reply(event, text.strip())
        else:
            logger.info("Unknown private command from %s:%s: %s", event.platform, event.author_id, text[:50])

    def _extract_private_text(self, segments: list[MessageSegment]) -> str:
        """从私信消息段中提取纯文本内容."""
        parts: list[str] = []
        for seg in segments:
            if seg.type == SEGMENT_TEXT:
                parts.append(seg.data.get("text", ""))
        return "".join(parts).strip()

    async def _handle_bind_command(self, event: MessageEvent, text: str) -> None:
        """处理 /bind 命令."""
        p = event.platform
        # 解析目标平台和标识符
        target_platform, target_identifier = self._parse_bind_target(text, from_platform=p)
        if target_platform is None or not target_identifier:
            logger.warning(
                "Invalid bind format from %s:%s: %s",
                p, event.author_id, text,
            )
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    "格式错误。请使用：\n"
                    "  /bind <QQ号>   (从 Discord 私信)",
                    # "  /bind <用户名>  (从 QQ 私信)",  # ── 已禁用 ──
                    "Invalid format. Use:\n"
                    "  /bind <QQ number>   (from Discord DM)",
                    # "  /bind <username>  (from QQ)",  # ── 已禁用 ──
                ),
            )
            return

        # 在目标平台成员缓存中查找用户
        target_user_id = self._resolve_target_user(target_platform, target_identifier)
        if target_user_id is None:
            logger.warning(
                "Bind target not found: %s:%s → %s:%s",
                p, event.author_id,
                target_platform, target_identifier,
            )
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    f"在 {target_platform.upper()} 中未找到「{target_identifier}」。"
                    f"请确认用户名正确，或 Bot 的成员缓存已刷新（等待 5 分钟）。",
                    f"User «{target_identifier}» not found on {target_platform.upper()}.\n"
                    f"Make sure the name is correct and the member cache is refreshed (wait ~5 min).",
                ),
            )
            return

        async with self._bind_lock:
            # 检查是否已绑定（在锁内，防止并发绑定导致状态不一致）
            if self._bind_manager.is_bound(p, event.author_id):
                logger.warning(
                    "Bind rejected: source already bound %s:%s",
                    p, event.author_id,
                )
                await self._send_private_reply(
                    p, event.author_id,
                    self._l10n(p,
                        "你的账号已绑定，请先使用 /unbind 解绑。",
                        "Your account is already bound. Use /unbind first.",
                    ),
                )
                return
            if self._bind_manager.is_bound(target_platform, target_user_id):
                logger.warning(
                    "Bind rejected: target already bound %s:%s",
                    target_platform, target_user_id,
                )
                await self._send_private_reply(
                    p, event.author_id,
                    self._l10n(p,
                        f"该 {target_platform.upper()} 账号已绑定到其他用户，请先解绑。",
                        f"This {target_platform.upper()} account is already bound to another user.",
                    ),
                )
                return

            # 生成验证码并发送到目标平台
            code = self._verification_manager.create(
                source_platform=p,
                source_user_id=event.author_id,
                target_platform=target_platform,
                target_user_id=target_user_id,
            )

        sent = await self._send_verification_code(target_platform, target_user_id, code)
        if sent:
            logger.info(
                "Verification code sent: %s:%s → %s:%s (code=%s)",
                p, event.author_id,
                target_platform, target_user_id, code,
            )
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    f"验证码已发送到 {target_platform.upper()} 用户「{target_identifier}」。"
                    f"请回复该验证码完成绑定。⏳ 5 分钟内有效",
                    f"Verification code sent to {target_platform.upper()} user «{target_identifier}».\n"
                    f"Reply with the code to complete binding. ⏳ Valid for 5 minutes",
                ),
            )
        else:
            logger.error(
                "Failed to send verification code: %s:%s → %s:%s",
                p, event.author_id,
                target_platform, target_user_id,
            )
            self._verification_manager.cancel(p, event.author_id)
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    f"无法向 {target_platform.upper()} 用户发送私信。"
                    f"请确保对方已开启私信接收。",
                    f"Failed to send DM to {target_platform.upper()} user.\n"
                    f"Make sure they have DMs enabled.",
                ),
            )

    async def _handle_unbind_command(self, event: MessageEvent) -> None:
        """处理 /unbind 命令."""
        p = event.platform
        self._verification_manager.cancel(p, event.author_id)
        if self._bind_manager.unbind(p, event.author_id):
            logger.info("Unbind successful: %s:%s", p, event.author_id)
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p, "已解绑 ✅", "Unbound ✅"),
            )
        else:
            logger.info(
                "Unbind attempt for unbounded user: %s:%s",
                p, event.author_id,
            )
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    "你尚未绑定任何账号。使用 /bind 命令开始绑定。",
                    "You haven't bound any account yet. Use /bind to get started.",
                ),
            )

    async def _handle_verification_reply(self, event: MessageEvent, code: str) -> None:
        """处理验证码回复."""
        p = event.platform
        async with self._bind_lock:
            result = self._verification_manager.verify(
                source_platform=p,
                source_user_id=event.author_id,
                code=code,
            )
            if result is None:
                logger.warning(
                    "Verification failed for %s:%s (wrong/expired code)",
                    p, event.author_id,
                )
                await self._send_private_reply(
                    p, event.author_id,
                    self._l10n(p,
                        "验证码错误或已过期，请重新发送 /bind 命令。",
                        "Invalid or expired code. Please send /bind again.",
                    ),
                )
                return

            target_platform, target_user_id = result

            try:
                self._bind_manager.bind(
                    qq_id=event.author_id if p == "qq" else target_user_id,
                    discord_id=event.author_id if p == "discord" else target_user_id,
                )
            except BindError as e:
                logger.error(
                    "Bind failed during verification: %s:%s ↔ %s:%s - %s",
                    p, event.author_id,
                    target_platform, target_user_id, e,
                )
                await self._send_private_reply(
                    p, event.author_id,
                    self._l10n(p,
                        f"绑定失败：{e}",
                        f"Bind failed: {e}",
                    ),
                )
                return

            logger.info(
                "Bind successful: %s:%s ↔ %s:%s",
                p, event.author_id,
                target_platform, target_user_id,
            )
            await self._send_private_reply(
                p, event.author_id,
                self._l10n(p,
                    "绑定成功 🎉 现在跨平台转发时会自动 @ 对方并使用绑定后的名称。",
                    "Binding successful 🎉 Messages will now auto-@ the bound user and show the bound name.",
                ),
            )

    def _parse_bind_target(self, text: str, from_platform: str = "discord") -> tuple[str | None, str]:
        """解析 /bind 命令的目标平台和标识符.

        Args:
            text: 命令文本
            from_platform: 发起绑定的平台 ("qq" 或 "discord")

        Returns:
            (platform, identifier) or (None, "") 如果格式无效.
        """
        m = BIND_COMMAND_RE.match(text)
        if not m:
            return None, ""
        rest = text[m.end():].strip()
        if not rest:
            return None, ""

        # 支持 "<QQ号>" 或 "<用户名>" 格式（也兼容 "QQ:xxx" / "Discord:xxx"）
        if ":" in rest:
            platform_part, _, identifier = rest.partition(":")
            platform_part = platform_part.strip().lower()
            identifier = identifier.strip()
            if platform_part in ("qq", "discord"):
                return platform_part, identifier
            return None, ""

        # 从 Discord 发起时，纯数字视为 QQ 号
        if rest.isdigit() and from_platform == "discord":
            return "qq", rest

        # 从 QQ 发起时或非数字，当作 Discord 用户名
        # ── 已禁用：QQ→Discord 昵称绑定功能（PR review 要求移除） ──
        # if from_platform == "qq":
        #     return "discord", rest
        # return "discord", rest
        return None, ""

    def _resolve_target_user(self, platform: str, identifier: str) -> str | None:
        """在目标平台成员缓存中查找用户.

        调用方保证 identifier 即为平台用户 ID（如 QQ 号），
        直接通过成员缓存检查用户是否在群内即可。
        """
        if self.matcher is None:
            return None
        if self.matcher.has_user(platform, identifier):
            return identifier
        return None

    async def _send_verification_code(
        self, platform: str, user_id: str, code: str,
    ) -> bool:
        """向目标平台的用户发送验证码私信（双语）. """
        message = (
            f"你收到了一个跨平台绑定请求。\n"
            f"验证码：{code}\n"
            f"请将验证码回复给发起绑定的 Bot 私信以完成绑定。\n"
            f"验证码 5 分钟内有效。\n"
            f"\n"
            f"---\n"
            f"You have received a cross-platform binding request.\n"
            f"Code: {code}\n"
            f"Reply this code to the bot who initiated the binding.\n"
            f"Valid for 5 minutes."
        )
        if platform == "qq" and self.qq_adapter is not None:
            return await self.qq_adapter.send_private_msg(int(user_id), message)
        if platform == "discord" and self.discord_adapter is not None:
            return await self.discord_adapter.send_dm(user_id, message)
        return False

    async def _send_private_reply(self, platform: str, user_id: str, text: str) -> None:
        """向用户发送私信回复."""
        logger.debug(
            "Sending private reply to %s:%s: %.100s",
            platform, user_id, text,
        )
        if platform == "qq" and self.qq_adapter is not None:
            await self.qq_adapter.send_private_msg(int(user_id), text)
        elif platform == "discord" and self.discord_adapter is not None:
            await self.discord_adapter.send_dm(user_id, text)

    # ── 绑定感知的昵称解析 ──────────────────────────────────

    def _resolve_author_display_name(
        self, platform: str, author_id: str, original_name: str,
    ) -> str:
        """如果用户已绑定，返回绑定后的平台名称；否则返回原名称."""
        if self._bind_manager is None:
            return original_name
        counterpart_id = self._bind_manager.get_counterpart(platform, author_id)
        if counterpart_id is None:
            return original_name
        # 从缓存中查找绑定后的显示名
        if self.matcher is not None:
            target_platform = "discord" if platform == "qq" else "qq"
            bound_name = self.matcher.get_display_name(target_platform, counterpart_id)
            if bound_name:
                return bound_name
        return original_name

    def _build_allowed_mentions(
        self, segments: list[MessageSegment], exclude_user_id: str | None,
    ) -> discord.AllowedMentions | None:
        """从消息段中提取所有 Discord @提及的用户 ID，构建 allowed_mentions.

        将 exclude_user_id 排除在通知白名单外（用于不通知绑定用户），
        其他被 @的用户正常通知。
        """
        mentioned_ids: set[int] = set()
        for seg in segments:
            if seg.type == SEGMENT_TEXT:
                text = seg.data.get("text", "")
                for m in MENTION_DISCORD_USER_RE.finditer(text):
                    mentioned_ids.add(int(m.group(1)))
            elif seg.type == SEGMENT_AT:
                uid = seg.data.get("user_id", "")
                if uid.isdigit():
                    mentioned_ids.add(int(uid))

        if not mentioned_ids:
            return None

        # 从白名单中移除作者（绑定用户不通知）
        if exclude_user_id and exclude_user_id.isdigit():
            mentioned_ids.discard(int(exclude_user_id))

        return discord.AllowedMentions(users=list(mentioned_ids))

    def _build_name_regex(self, target_platform: str) -> re.Pattern | None:
        """为平台构建一次性匹配所有 display_name 的正则，按名字长度降序。"""
        if self.matcher is None:
            return None
        cache = self.matcher._cache.get(target_platform, {})
        if not cache:
            return None
        names = sorted((n for n in cache.values() if n), key=len, reverse=True)
        if not names:
            return None
        pattern = "@(" + "|".join(re.escape(n) for n in names) + ")"
        return re.compile(pattern, re.IGNORECASE)

    def _find_full_name_mentions(
        self, text: str, target_platform: str,
    ) -> list[tuple[int, int, str, str, str]]:
        """在文本中查找 @完整display_name 模式（含空格），大小写不敏感。

        用单个组合正则一次性匹配所有 display_name（按长度降序），
        避免对每个成员单独做 regex 扫描导致的 O(n) 性能问题。

        Returns:
            list of (start, end, name, user_id, display_name)，已按 start 排序。
        """
        if self.matcher is None:
            return []
        cache = self.matcher._cache.get(target_platform, {})
        if not cache:
            return []

        regex = self._build_name_regex(target_platform)
        if regex is None:
            return []

        name_lookup: dict[str, tuple[str, str]] = {}
        for uid, name in cache.items():
            if name:
                key = name.casefold()
                if key not in name_lookup:
                    name_lookup[key] = (uid, name)

        matches: list[tuple[int, int, str, str, str]] = []

        for m in regex.finditer(text):
            matched_name = m.group(1)
            entry = name_lookup.get(matched_name.casefold())
            if entry is None:
                continue
            user_id, display_name = entry
            start, end = m.start(), m.end()
            matches.append((start, end, display_name, user_id, display_name))

        matches.sort(key=lambda x: x[0])
        return matches

    def _resolve_text_mentions(
        self, segments: list[MessageSegment], target_platform: str,
    ) -> list[MessageSegment]:
        if self.matcher is None:
            return segments
        source_platform = "qq" if target_platform == "discord" else "discord"
        result: list[MessageSegment] = []

        for seg in segments:
            if seg.type != SEGMENT_TEXT:
                result.append(seg)
                continue
            text = seg.data.get("text", "")
            if not text or "@" not in text:
                result.append(seg)
                continue

            # ── 策略 A：全名匹配（含空格，大小写不敏感） ──
            full_matches = self._find_full_name_mentions(text, target_platform)
            full_ranges = [(s, e) for s, e, *_ in full_matches]

            def _is_covered(start: int, end: int) -> bool:
                return any(s < end and e > start for s, e in full_ranges)

            # ── 策略 B：正则匹配 @non_whitespace（跳过已覆盖区域） ──
            regex_hits: list[tuple[int, int, str]] = []
            for m in MENTION_TEXT_RE.finditer(text):
                s, e = m.start(), m.end()
                if not _is_covered(s, e):
                    regex_hits.append((s, e, m.group(1)))

            # ── 合并，按位置排序 ──
            entries: list[tuple[int, int, str, str | None, str | None, bool]] = []
            for s, e, n, uid, dn in full_matches:
                entries.append((s, e, n, uid, dn, True))
            for s, e, n in regex_hits:
                entries.append((s, e, n, None, None, False))
            entries.sort(key=lambda x: x[0])

            # ── 逐一解析并构建结果 ──
            last_end = 0
            for start, end, name, uid, dn, is_full in entries:
                if start > last_end:
                    result.append(text_segment(text[last_end:start]))

                # 优先查绑定：在 source 平台中找 @name 对应的用户，看是否有绑定
                bound_target = self._resolve_text_mention_via_binding(name, source_platform, target_platform)
                if bound_target is not None:
                    user_id, display_name = bound_target
                    if self._debug:
                        print(f"[DEBUG] 文本 @{name} -> 绑定匹配 {target_platform} 用户 {display_name} ({user_id})", flush=True)

                if is_full:
                    # 全名匹配直接使用缓存中的用户信息
                    final_id, final_display = uid, dn  # type: ignore[misc]
                    if self._debug:
                        print(f"[DEBUG] 文本 @{name} -> 全名匹配 {target_platform} 用户 {final_display} ({final_id})", flush=True)
                else:
                    # 正则匹配，尝试名称匹配
                    matched = self.matcher.match_user(name, target_platform)
                    if matched is not None:
                        final_id, final_display = matched
                        if self._debug:
                            print(f"[DEBUG] 文本 @{name} -> 名称匹配 {target_platform} 用户 {final_display} ({final_id})", flush=True)
                    else:
                        final_id, final_display = None, None
                        if self._debug:
                            print(f"[DEBUG] 文本 @{name} -> 未在 {target_platform} 中找到匹配", flush=True)

                if final_id is not None:
                    if target_platform == "discord":
                        result.append(text_segment(f"<@{final_id}>"))
                    else:
                        result.append(at_segment("qq", final_id, final_display))
                else:
                    # 回退到名称匹配
                    matched = self.matcher.match_user(name, target_platform)
                    if matched is not None:
                        user_id, display_name = matched
                        if self._debug:
                            print(f"[DEBUG] 文本 @{name} -> 名称匹配 {target_platform} 用户 {display_name} ({user_id})", flush=True)
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

    def _resolve_text_mention_via_binding(
        self, name: str, source_platform: str, target_platform: str,
    ) -> tuple[str, str] | None:
        """通过绑定关系解析文本 @ 提及.

        在 source 平台缓存中查找名为 name 的用户，
        如果该用户有绑定，返回 target 平台的 (user_id, display_name).
        """
        if self.matcher is None or self._bind_manager is None:
            return None
        candidates = self.matcher.search_users_by_display(name, source_platform)
        for source_user_id, _ in candidates:
            bound_id = self._bind_manager.get_counterpart(source_platform, source_user_id)
            if bound_id is not None:
                target_display = self.matcher.get_display_name(target_platform, bound_id) or bound_id
                return bound_id, target_display
        return None

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
                    logger.warning("Failed to refresh Discord member cache", exc_info=True)
            if self.qq_adapter is not None and self.matcher is not None:
                try:
                    await self.matcher.refresh_cache(
                        "qq", self.qq_adapter, self._qq_group_id
                    )
                    if self._debug:
                        qq_count = len(self.matcher._cache.get("qq", {}))
                        print(f"[DEBUG] 缓存刷新: QQ 成员数={qq_count}", flush=True)
                except Exception:
                    logger.warning("Failed to refresh QQ member cache", exc_info=True)
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
                logger.warning("Error stopping Discord adapter", exc_info=True)
        if self.qq_adapter is not None:
            try:
                await self.qq_adapter.stop()
            except Exception:
                logger.warning("Error stopping QQ adapter", exc_info=True)
