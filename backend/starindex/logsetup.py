"""Structured logging: JSON lines to a rotating file, concise text to the console.

Log records never contain secrets: tokens are passed around as opaque values and are not
formatted into messages; a filter additionally masks anything that looks like a credential.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime
from pathlib import Path

_SECRET = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|"
    r"(?:id_token_hint|access_token|refresh_token|api[_-]?key|code)=[^&\s\"']{8,})",
    re.IGNORECASE,
)


def mask(text: str) -> str:
    return _SECRET.sub("<redacted>", text)


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        masked = mask(message)
        if masked != message:
            record.msg, record.args = masked, ()
        return True


class _Json(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = mask(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


def configure(logs_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_starindex_configured", False):
        return
    root.setLevel(level)
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(logs_dir / "starindex.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(_Json())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    for handler in (file_handler, console):
        handler.addFilter(_Redact())
        root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "fastembed", "huggingface_hub", "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._starindex_configured = True  # type: ignore[attr-defined]
