import json
import logging
import threading
import typing
from datetime import date, datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        msg_id = getattr(record, "msg_id", None)
        if msg_id is not None:
            log_entry["msg_id"] = msg_id
        event = getattr(record, "event", None)
        if event is not None:
            log_entry["event"] = event
        latency_ms = getattr(record, "latency_ms", None)
        if latency_ms is not None:
            log_entry["latency_ms"] = latency_ms
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class DailyRotatingFileHandler(logging.Handler):
    """每日切分 + 每月目录的日志 handler.

    文件路径: <base_dir>/YYYY/MM/<name>-YYYY-MM-DD.log
    每天第一次写入时自动切换到新文件（以日期为界）。
    """

    def __init__(
        self,
        base_dir: str = "logs",
        name: str = "phobos",
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level)
        self._base_dir = Path(base_dir)
        self._name = name
        self._current_date: date | None = None
        self._file: typing.IO | None = None
        self._lock = threading.Lock()

    def _file_path(self, dt: date) -> Path:
        return (
            self._base_dir
            / str(dt.year)
            / f"{dt.month:02d}"
            / f"{self._name}-{dt.isoformat()}.log"
        )

    def _rotate(self) -> None:
        today = date.today()
        if self._current_date == today:
            return
        if self._file is not None:
            self._file.close()
            self._file = None
        self._current_date = today
        path = self._file_path(today)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            try:
                self._rotate()
                msg = self.format(record)
                self._file.write(msg + "\n")
                self._file.flush()
            except Exception:
                self.handleError(record)

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
        super().close()


class DiscordReconnectFilter(logging.Filter):
    """Suppress huge exc_info tracebacks from discord.py's internal reconnection logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "discord.client" and "Attempting a reconnect" in record.getMessage():
            record.exc_info = None
            record.exc_text = None
        return True


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    return logger


def suppress_discord_reconnect_traceback() -> None:
    discord_logger = logging.getLogger("discord.client")
    discord_logger.addFilter(DiscordReconnectFilter())
