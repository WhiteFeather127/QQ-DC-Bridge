from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Self

logger = logging.getLogger(__name__)

_MAX_VERIFICATION_ATTEMPTS = 5


@dataclass
class VerificationCode:
    """一条待验证的绑定请求."""

    code: str
    source_platform: str
    source_user_id: str
    target_platform: str
    target_user_id: str
    expires_at: float
    attempts: int = 0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @classmethod
    def create(
        cls,
        source_platform: str,
        source_user_id: str,
        target_platform: str,
        target_user_id: str,
        ttl: float = 300.0,
    ) -> Self:
        code = f"{random.randint(0, 999999):06d}"
        return cls(
            code=code,
            source_platform=source_platform,
            source_user_id=source_user_id,
            target_platform=target_platform,
            target_user_id=target_user_id,
            expires_at=time.monotonic() + ttl,
        )


class VerificationManager:
    """验证码管理器.

    管理待验证的绑定请求，支持生成、校验和惰性过期清理。
    """

    def __init__(self) -> None:
        # key: f"{source_platform}:{source_user_id}"
        self._pending: dict[str, VerificationCode] = {}

    def create(
        self,
        source_platform: str,
        source_user_id: str,
        target_platform: str,
        target_user_id: str,
    ) -> str:
        """生成并存储一个验证码.

        Returns:
            6 位数字验证码字符串.
        """
        self._evict_expired()
        vc = VerificationCode.create(
            source_platform=source_platform,
            source_user_id=source_user_id,
            target_platform=target_platform,
            target_user_id=target_user_id,
        )
        key = self._key(source_platform, source_user_id)
        self._pending[key] = vc
        logger.info(
            "Verification code created: %s:%s → %s:%s (code=%s, expires in 300s)",
            source_platform, source_user_id,
            target_platform, target_user_id,
            vc.code,
        )
        return vc.code

    def verify(
        self,
        source_platform: str,
        source_user_id: str,
        code: str,
    ) -> tuple[str, str] | None:
        """校验验证码.

        Args:
            source_platform: 来源平台
            source_user_id: 来源用户 ID
            code: 用户提交的验证码

        Returns:
            校验成功返回 (target_platform, target_user_id)，失败返回 None.
        """
        key = self._key(source_platform, source_user_id)
        vc = self._pending.pop(key, None)
        if vc is None:
            logger.warning(
                "Verification failed: no pending code for %s:%s",
                source_platform, source_user_id,
            )
            return None
        if vc.is_expired:
            logger.warning(
                "Verification failed: code expired for %s:%s",
                source_platform, source_user_id,
            )
            return None
        if vc.code != code:
            vc.attempts += 1
            if vc.attempts >= _MAX_VERIFICATION_ATTEMPTS:
                logger.warning(
                    "Verification failed: too many attempts for %s:%s (%d/%d), removing code",
                    source_platform, source_user_id, vc.attempts, _MAX_VERIFICATION_ATTEMPTS,
                )
                return None
            logger.warning(
                "Verification failed: wrong code for %s:%s (expected=%s, got=%s, attempt=%d/%d)",
                source_platform, source_user_id, vc.code, code, vc.attempts, _MAX_VERIFICATION_ATTEMPTS,
            )
            # 验证码错误，放回 pending（允许重试）
            self._pending[key] = vc
            return None
        logger.info(
            "Verification successful: %s:%s ↔ %s:%s",
            source_platform, source_user_id,
            vc.target_platform, vc.target_user_id,
        )
        return vc.target_platform, vc.target_user_id

    def cancel(self, source_platform: str, source_user_id: str) -> None:
        """取消正在进行的验证（如解绑时清理残留）. """
        key = self._key(source_platform, source_user_id)
        removed = self._pending.pop(key, None)
        if removed is not None:
            logger.info(
                "Verification cancelled: %s:%s → %s:%s",
                source_platform, source_user_id,
                removed.target_platform, removed.target_user_id,
            )

    def _evict_expired(self) -> None:
        expired = [
            key for key, vc in self._pending.items() if vc.is_expired
        ]
        for key in expired:
            logger.debug("Evicting expired verification: %s", key)
            del self._pending[key]

    @staticmethod
    def _key(platform: str, user_id: str) -> str:
        return f"{platform}:{user_id}"
