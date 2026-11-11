"""Process-level configuration: filesystem locations and environment overrides.

User-editable preferences live in the database (see ``settings_store``); this module only
covers what must be known before the database is opened. Application data is always kept
outside the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "StarIndex"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "starindex.db"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    def ensure(self) -> None:
        for p in (self.data_dir, self.vector_dir, self.models_dir, self.backups_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class RuntimeConfig:
    """Values that come from the environment or CLI flags, never from the browser."""

    paths: Paths
    host: str
    port: int
    access_token: str | None
    github_api_url: str
    github_raw_url: str
    qdrant_url: str | None
    log_level: str
    disable_keyring: bool

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS


def default_data_dir() -> Path:
    override = os.environ.get("STARINDEX_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    # Windows: %LOCALAPPDATA%\StarIndex ; Linux: ~/.local/share/StarIndex ; macOS: ~/Library/Application Support
    return Path(user_data_dir(APP_NAME, appauthor=False))


@lru_cache(maxsize=1)
def get_config() -> RuntimeConfig:
    env = os.environ
    return RuntimeConfig(
        paths=Paths(default_data_dir()),
        host=env.get("STARINDEX_HOST", DEFAULT_HOST),
        port=int(env.get("STARINDEX_PORT", DEFAULT_PORT)),
        access_token=env.get("STARINDEX_ACCESS_TOKEN") or None,
        github_api_url=env.get("STARINDEX_GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        github_raw_url=env.get("STARINDEX_GITHUB_RAW_URL", "https://raw.githubusercontent.com").rstrip("/"),
        qdrant_url=env.get("STARINDEX_QDRANT_URL") or None,
        log_level=env.get("STARINDEX_LOG_LEVEL", "INFO").upper(),
        disable_keyring=env.get("STARINDEX_DISABLE_KEYRING", "").lower() in {"1", "true", "yes"},
    )


def reset_config_cache() -> None:
    """Tests change the environment between cases."""
    get_config.cache_clear()
