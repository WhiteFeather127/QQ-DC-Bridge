from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class BindError(Exception):
    """绑定操作失败时抛出的异常."""


class BindManager:
    """跨平台账号绑定管理器.

    维护 QQ ↔ Discord 的双向映射关系，持久化到 JSON 文件。
    参考 MessageStore 的按月分目录存储模式。
    """

    def __init__(self, data_dir: str = "./data") -> None:
        self._base_dir = Path(data_dir) / "bindings"
        self._qq_to_discord: dict[str, str] = {}
        self._discord_to_qq: dict[str, str] = {}
        self._load_all()

    # ── 公开方法 ──────────────────────────────────────────────

    def bind(self, qq_id: str, discord_id: str) -> None:
        """建立 QQ ↔ Discord 双向绑定.

        Raises:
            BindError: 如果任一端已被绑定.
        """
        if qq_id in self._qq_to_discord:
            existing_discord = self._qq_to_discord[qq_id]
            logger.warning(
                "Bind conflict: QQ %s already bound to Discord %s",
                qq_id, existing_discord,
            )
            raise BindError(
                f"该 QQ 账号 ({qq_id}) 已绑定到 Discord 用户 {existing_discord}，请先解绑"
            )
        if discord_id in self._discord_to_qq:
            existing_qq = self._discord_to_qq[discord_id]
            logger.warning(
                "Bind conflict: Discord %s already bound to QQ %s",
                discord_id, existing_qq,
            )
            raise BindError(
                f"该 Discord 账号 ({discord_id}) 已绑定到 QQ 用户 {existing_qq}，请先解绑"
            )

        self._qq_to_discord[qq_id] = discord_id
        self._discord_to_qq[discord_id] = qq_id
        self._save()
        logger.info(
            "Binding created: QQ %s ↔ Discord %s",
            qq_id, discord_id,
        )

    def unbind(self, platform: str, user_id: str) -> bool:
        """解除绑定.

        Args:
            platform: "qq" 或 "discord"
            user_id: 平台用户 ID

        Returns:
            True 如果找到并解除了绑定，False 如果该用户未绑定.
        """
        if platform == "qq":
            discord_id = self._qq_to_discord.pop(user_id, None)
            if discord_id is not None:
                self._discord_to_qq.pop(discord_id, None)
                self._save()
                logger.info("Binding removed: QQ %s ↔ Discord %s", user_id, discord_id)
                return True
        elif platform == "discord":
            qq_id = self._discord_to_qq.pop(user_id, None)
            if qq_id is not None:
                self._qq_to_discord.pop(qq_id, None)
                self._save()
                logger.info("Binding removed: QQ %s ↔ Discord %s", qq_id, user_id)
                return True
        logger.info("Unbind attempt for unbounded user: %s:%s", platform, user_id)
        return False

    def get_counterpart(self, platform: str, user_id: str) -> str | None:
        """查询绑定关系，获取对方平台用户 ID.

        Args:
            platform: 来源平台 ("qq" 或 "discord")
            user_id: 来源平台用户 ID

        Returns:
            对方平台用户 ID，或 None（未绑定）.
        """
        if platform == "qq":
            return self._qq_to_discord.get(user_id)
        if platform == "discord":
            return self._discord_to_qq.get(user_id)
        return None

    def is_bound(self, platform: str, user_id: str) -> bool:
        """检查用户是否已绑定."""
        return self.get_counterpart(platform, user_id) is not None

    def get_all_bindings(self) -> list[dict[str, str]]:
        """获取所有绑定关系列表（用于调试/管理）. """
        return [
            {"qq": qq_id, "discord": discord_id}
            for qq_id, discord_id in self._qq_to_discord.items()
        ]

    # ── 持久化 ────────────────────────────────────────────────

    @staticmethod
    def _daily_path(base_dir: Path, dt: date | datetime = None) -> Path:
        if dt is None:
            dt = date.today()
        elif isinstance(dt, datetime):
            dt = dt.date()
        return base_dir / str(dt.year) / f"{dt.month:02d}" / f"{dt.isoformat()}.json"

    def _load_all(self) -> None:
        if not self._base_dir.exists():
            logger.info("No bindings data directory, starting fresh: %s", self._base_dir)
            return
        loaded = 0
        for json_file in sorted(self._base_dir.rglob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                raw = data.get("qq_to_discord", {})
                self._qq_to_discord.update(raw)
                self._discord_to_qq.update({v: k for k, v in raw.items()})
                loaded += len(raw)
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load bindings file: %s", json_file)
                continue
        if loaded:
            logger.info("Loaded %d bindings from %s", loaded, self._base_dir)

    def _save(self) -> None:
        path = self._daily_path(self._base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"qq_to_discord": self._qq_to_discord},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.debug("Bindings saved to %s (%d entries)", path, len(self._qq_to_discord))
