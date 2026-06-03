from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path


class MessageStore:
    def __init__(self, data_dir: str = "./data") -> None:
        self._base_dir = Path(data_dir) / "message_store"
        self._qq_to_discord: dict[str, str] = {}
        self._discord_to_qq: dict[str, str] = {}
        self._load_all()

    @staticmethod
    def _daily_path(base_dir: Path, dt: date | datetime = None) -> Path:
        if dt is None:
            dt = date.today()
        elif isinstance(dt, datetime):
            dt = dt.date()
        return base_dir / str(dt.year) / f"{dt.month:02d}" / f"{dt.isoformat()}.json"

    def _load_all(self) -> None:
        if not self._base_dir.exists():
            return
        for json_file in sorted(self._base_dir.rglob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                raw = data.get("qq_to_discord", {})
                self._qq_to_discord.update(raw)
                self._discord_to_qq.update({v: k for k, v in raw.items()})
            except (json.JSONDecodeError, OSError):
                continue

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

    def record(self, qq_msg_id: str, discord_msg_id: str) -> None:
        self._qq_to_discord[qq_msg_id] = discord_msg_id
        self._discord_to_qq[discord_msg_id] = qq_msg_id
        self._save()

    def get_counterpart(self, platform: str, msg_id: str) -> str | None:
        if platform == "qq":
            return self._qq_to_discord.get(msg_id)
        if platform == "discord":
            return self._discord_to_qq.get(msg_id)
        return None
