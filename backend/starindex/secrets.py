"""Local secret storage for GitHub tokens, provider API keys and OAuth credentials.

Preferred backend is the operating system keyring (Windows Credential Manager, macOS
Keychain, Secret Service). When no usable keyring exists (headless Linux, CI) secrets fall
back to a per-user file written atomically with owner-only permissions. Secrets never go
into the settings table, logs, API responses or reports.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

SERVICE = "StarIndex"
# Windows Credential Manager caps a credential blob at 2560 bytes; OAuth records exceed it.
_KEYRING_CHUNK = 1000


class SecretStore(Protocol):
    backend_name: str

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class FileSecretStore:
    """JSON file with 0600 permissions. Used when the OS keyring is unavailable."""

    backend_name = "file"

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "secrets.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.error("secret file is unreadable; treating as empty")
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".secrets-", suffix=".tmp")
        try:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                self._save(data)


class KeyringSecretStore:
    """OS keyring. Long values are split across entries to respect per-credential size limits."""

    backend_name = "keyring"

    def __init__(self) -> None:
        import keyring

        self._kr = keyring
        self.backend_name = f"keyring:{type(keyring.get_keyring()).__name__}"

    def get(self, key: str) -> str | None:
        head = self._kr.get_password(SERVICE, key)
        if head is None:
            return None
        if not head.startswith("\x00chunks:"):
            return head
        count = int(head.split(":", 1)[1])
        parts = [self._kr.get_password(SERVICE, f"{key}#{i}") or "" for i in range(count)]
        return "".join(parts)

    def set(self, key: str, value: str) -> None:
        self.delete(key)
        if len(value) <= _KEYRING_CHUNK:
            self._kr.set_password(SERVICE, key, value)
            return
        parts = [value[i : i + _KEYRING_CHUNK] for i in range(0, len(value), _KEYRING_CHUNK)]
        for i, part in enumerate(parts):
            self._kr.set_password(SERVICE, f"{key}#{i}", part)
        self._kr.set_password(SERVICE, key, f"\x00chunks:{len(parts)}")

    def delete(self, key: str) -> None:
        import keyring.errors

        head = None
        try:
            head = self._kr.get_password(SERVICE, key)
        except keyring.errors.KeyringError:
            return
        if head is None:
            return
        if head.startswith("\x00chunks:"):
            for i in range(int(head.split(":", 1)[1])):
                try:
                    self._kr.delete_password(SERVICE, f"{key}#{i}")
                except keyring.errors.KeyringError:
                    pass
        try:
            self._kr.delete_password(SERVICE, key)
        except keyring.errors.KeyringError:
            pass


def _keyring_usable() -> bool:
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return False
        name = type(backend).__module__
        # Plaintext/null backends offer no protection beyond our own 0600 file.
        return "null" not in name and "plaintext" not in name.lower()
    except Exception:  # keyring import/initialisation can fail in many platform-specific ways
        return False


def open_secret_store(secrets_dir: Path, *, disable_keyring: bool = False) -> SecretStore:
    if not disable_keyring and _keyring_usable():
        try:
            return KeyringSecretStore()
        except Exception as exc:
            log.warning("keyring unavailable (%s); using protected file storage", type(exc).__name__)
    return FileSecretStore(secrets_dir)


# Key naming helpers -- keep every secret key in one place.
GITHUB_TOKEN = "github:token"


def provider_key(provider_id: str) -> str:
    return f"provider:{provider_id}:api_key"


def chatgpt_account_key(account_id: str) -> str:
    return f"chatgpt:account:{account_id}"


def redact(value: str | None) -> str:
    """Safe representation for logs and diagnostics."""
    if not value:
        return "<none>"
    return f"<redacted:{len(value)} chars>"
