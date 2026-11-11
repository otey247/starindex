"""ChatGPT plan accounts: registrations, sign-in attempts, token lifecycle.

Each registration (issued client id + verified identity) is kept separate, even when two
share an email address. The non-secret part lives in the ``chatgpt_accounts`` table; access,
refresh and ID tokens live in the secret store (OS keyring, or an owner-only file).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ...api.schemas import ChatGPTAccount, ChatGPTLoginAttempt, ChatGPTStatus
from ...db import Database, dumps, loads
from ...secrets import SecretStore, chatgpt_account_key
from ...settings_store import SettingsStore
from ..base import ProviderError
from . import oauth
from .oauth import (
    DYNAMIC_CLIENT_ID,
    PLAN_USAGE_SCOPE,
    PROVIDER_ID,
    PROVIDER_KIND,
    TERMINAL_REFRESH_CODES,
    USAGE_URL,
    AuthAttempt,
    CallbackRejected,
    CallbackServer,
    OAuthClient,
    OAuthError,
    TokenSet,
)

log = logging.getLogger(__name__)

HOST_ID_SETTING = "chatgpt.ext_agent_host_id"
HOST_KEY_SECRET = "chatgpt:host_key"
ATTEMPT_TTL_S = 600
REFRESH_MARGIN_S = 300
_REVOKE_BACKOFF_S = (0.5, 1.5)


def _err(kind: str, message: str, **kw: Any) -> ProviderError:
    return ProviderError(kind, message, provider_id=PROVIDER_ID, provider_kind=PROVIDER_KIND, **kw)  # type: ignore[arg-type]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _FileLock:
    """Small cross-process lock (exclusive create) so two processes never race a rotating token."""

    def __init__(self, path: Path, *, timeout_s: float = 30.0, stale_s: float = 120.0):
        self.path = path
        self.timeout_s = timeout_s
        self.stale_s = stale_s

    def __enter__(self) -> _FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise _err("unavailable", "Another process is refreshing the ChatGPT session; try again.", retryable=True) from None
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


class ChatGPTPlanService:
    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        settings_store: SettingsStore,
        data_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        callback_port: int = oauth.PREFERRED_CALLBACK_PORT,
    ):
        self.db = db
        self.secrets = secrets
        self.settings_store = settings_store
        self.data_dir = Path(data_dir)
        self.oauth = OAuthClient(transport=transport)
        self._callback_port = callback_port
        self._attempts: dict[str, AuthAttempt] = {}
        self._servers: dict[str, CallbackServer] = {}
        self._attempt_lock = threading.RLock()
        self._refresh_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Issued client id kept after a failed exchange on a new registration (docs: retry with it).
        self._retry_client_id: tuple[str, float] | None = None
        self._revoke_backoff: tuple[float, ...] = _REVOKE_BACKOFF_S

    # ------------------------------------------------------------------ host identifier

    def host_id(self) -> str:
        """This installation's persistent ``ext_agent_host_id`` (created once, before first sign-in)."""
        current = self.settings_store.get_value(HOST_ID_SETTING)
        if oauth.is_valid_host_id(current):
            return current
        host_id, private_pem = oauth.generate_host_id()
        if private_pem:
            try:
                self.secrets.set(HOST_KEY_SECRET, private_pem)
            except Exception:  # the identifier alone is sufficient; the key is not verified by OpenAI
                log.warning("could not store the host key; continuing with the identifier only")
        self.settings_store.set_value(HOST_ID_SETTING, host_id)
        return host_id

    def host_id_set(self) -> bool:
        return oauth.is_valid_host_id(self.settings_store.get_value(HOST_ID_SETTING))

    # ------------------------------------------------------------------ accounts (storage)

    def _rows(self) -> list[Any]:
        return self.db.all("SELECT * FROM chatgpt_accounts ORDER BY saved_at, id")

    def _row(self, account_id: str) -> Any:
        row = self.db.one("SELECT * FROM chatgpt_accounts WHERE id=?", (account_id,))
        if row is None:
            raise _err("not_configured", "That ChatGPT account is not registered in StarIndex.")
        return row

    def _record(self, account_id: str) -> dict[str, Any]:
        raw = self.secrets.get(chatgpt_account_key(account_id))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _save_record(self, account_id: str, record: dict[str, Any]) -> None:
        self.secrets.set(chatgpt_account_key(account_id), json.dumps(record))

    def _account(self, row: Any) -> ChatGPTAccount:
        client_id = row["client_id"]
        return ChatGPTAccount(
            id=row["id"],
            label=row["label"],
            email=row["email"],
            client_id_suffix=client_id[-6:] if len(client_id) > 6 else client_id,
            plan_usage_enabled=bool(row["plan_usage_enabled"]),
            is_active=bool(row["is_active"]),
            signed_in=bool(row["signed_in"]),
            scopes=loads(row["scopes"], []),
            saved_at=row["saved_at"],
            expires_at=row["expires_at"],
            last_error=row["last_error"],
            welcome_pending=bool(row["plan_usage_enabled"]) and bool(row["signed_in"]) and not bool(row["welcome_shown"]),
        )

    def accounts(self) -> list[ChatGPTAccount]:
        return [self._account(r) for r in self._rows()]

    def active_account_id(self) -> str | None:
        return self.db.scalar("SELECT id FROM chatgpt_accounts WHERE is_active=1 LIMIT 1")

    def _unique_label(self, email: str | None, client_id: str, exclude_id: str | None = None) -> str:
        base = email or "ChatGPT account"
        taken = {r["label"] for r in self._rows() if r["id"] != exclude_id}
        if base not in taken:
            return base
        label = f"{base} ({client_id[-4:]})"
        n = 2
        while label in taken:
            label = f"{base} ({client_id[-4:]}-{n})"
            n += 1
        return label

    # ------------------------------------------------------------------ status

    def status(self) -> ChatGPTStatus:
        settings = self.settings_store.load()
        accounts = self.accounts()
        active = next((a for a in accounts if a.is_active), None)
        blocked = not settings.privacy.allow_cloud_models or settings.privacy.offline_mode
        if active is None:
            available, reason = False, (
                "Not signed in. Use Continue with ChatGPT to register this installation and choose whether "
                "StarIndex may use your ChatGPT plan."
                if not accounts
                else "No ChatGPT account is active. Choose one of your saved accounts."
            )
        elif not active.signed_in:
            available, reason = False, "The session for this ChatGPT account has ended. Sign in again."
        elif not active.plan_usage_enabled:
            available, reason = False, (
                "You are signed in, but permission to use your ChatGPT plan was not granted. "
                "Enable ChatGPT plan usage to use it for inference, or pick another provider."
            )
        elif blocked:
            available, reason = False, (
                "Cloud model traffic is turned off in Privacy settings, so the ChatGPT plan cannot be used."
            )
        else:
            available, reason = True, ""
        return ChatGPTStatus(
            available=available,
            reason=reason,
            host_id_set=self.host_id_set(),
            accounts=accounts,
            active_account_id=active.id if active else None,
            usage_url=USAGE_URL,
            blocked_by_privacy=blocked,
        )

    def ready_account_id(self, account_id: str | None = None) -> str:
        """Return the account to use for inference or raise the reason it cannot be used."""
        target = account_id or self.active_account_id()
        if not target:
            raise _err("not_configured", "Sign in with ChatGPT in Settings before using the ChatGPT plan.")
        row = self._row(target)
        if not row["signed_in"]:
            raise _err("auth", "The ChatGPT session has ended. Sign in again in Settings.")
        if not row["plan_usage_enabled"] or PLAN_USAGE_SCOPE not in loads(row["scopes"], []):
            raise _err(
                "consent_required",
                "This ChatGPT account has not granted StarIndex permission to use its plan. Enable ChatGPT "
                "plan usage in Settings, or choose another provider.",
            )
        return target

    # ------------------------------------------------------------------ sign-in

    def start_login(self, account_id: str | None = None, enable_plan_usage: bool = False) -> ChatGPTLoginAttempt:
        if self.settings_store.load().privacy.offline_mode:
            raise _err("cloud_disabled", "Offline mode is on; signing in with ChatGPT needs a network connection.")
        self._expire_attempts()
        host_id = self.host_id()
        id_token_hint: str | None = None
        login_hint: str | None = None
        if account_id:
            row = self._row(account_id)
            client_id = row["client_id"]
            is_new = False
            record = self._record(account_id)
            id_token_hint = record.get("id_token") or None
            login_hint = row["email"] or None
        else:
            is_new = True
            client_id = DYNAMIC_CLIENT_ID
            retry = self._retry_client_id
            if retry and time.time() - retry[1] < ATTEMPT_TTL_S:
                client_id = retry[0]

        with self._attempt_lock:
            # A new sign-in supersedes unfinished ones, which also frees the preferred callback port.
            for other_id, other in list(self._attempts.items()):
                if other.status == "pending" and not other.processing:
                    other.status = "expired"
                    other.message = "Superseded by a newer sign-in attempt."
                    self._close_server(other_id, wait=True)
            holder: dict[str, AuthAttempt] = {}
            server = CallbackServer(lambda params: self._on_callback(holder["attempt"], params), preferred_port=self._callback_port)
            attempt = oauth.new_attempt(
                redirect_uri=server.redirect_uri,
                client_id=client_id,
                is_new_registration=is_new,
                account_id=account_id,
                enable_plan_usage=enable_plan_usage,
            )
            holder["attempt"] = attempt
            attempt.auth_url = oauth.build_authorize_url(
                attempt,
                host_id=host_id,
                id_token_hint=id_token_hint,
                login_hint=login_hint,
                # Ask for consent again only when the user explicitly chose to enable plan usage.
                force_consent=enable_plan_usage and client_id != DYNAMIC_CLIENT_ID,
            )
            self._attempts[attempt.attempt_id] = attempt
            self._servers[attempt.attempt_id] = server
            server.start()
        log.info("ChatGPT sign-in started: %s", oauth.redact_url(attempt.auth_url))
        return self._attempt_view(attempt, include_url=True)

    def login_status(self, attempt_id: str) -> ChatGPTLoginAttempt:
        self._expire_attempts()
        with self._attempt_lock:
            attempt = self._attempts.get(attempt_id)
        if attempt is None:
            return ChatGPTLoginAttempt(attempt_id=attempt_id, status="expired", message="This sign-in attempt is no longer active.")
        return self._attempt_view(attempt, include_url=attempt.status == "pending")

    def _attempt_view(self, attempt: AuthAttempt, *, include_url: bool) -> ChatGPTLoginAttempt:
        return ChatGPTLoginAttempt(
            attempt_id=attempt.attempt_id,
            auth_url=attempt.auth_url if include_url else None,
            status=attempt.status,  # type: ignore[arg-type]
            message=attempt.message,
            account_id=attempt.result_account_id,
            plan_usage_enabled=attempt.plan_usage_enabled,
        )

    def _expire_attempts(self) -> None:
        now = time.time()
        with self._attempt_lock:
            for attempt_id, attempt in list(self._attempts.items()):
                age = now - attempt.created_at
                if attempt.status == "pending" and age > ATTEMPT_TTL_S:
                    attempt.status = "expired"
                    attempt.message = "The sign-in was not completed in time. Start again."
                    self._close_server(attempt_id)
                elif attempt.status != "pending" and age > ATTEMPT_TTL_S * 3:
                    self._attempts.pop(attempt_id, None)
                    self._close_server(attempt_id)

    def _close_server(self, attempt_id: str, *, wait: bool = False) -> None:
        server = self._servers.pop(attempt_id, None)
        if server is not None:
            server.close(wait=wait)

    def _finish(self, attempt: AuthAttempt, status: str, message: str) -> tuple[bool, str]:
        attempt.status = status
        attempt.message = message
        with self._attempt_lock:
            self._close_server(attempt.attempt_id)
        return status == "completed", message

    def _on_callback(self, attempt: AuthAttempt, params: dict[str, str]) -> tuple[bool, str]:
        """Runs on the loopback listener thread. Returns (ok, message for the browser page)."""
        try:
            result = oauth.validate_callback(attempt, params)
        except CallbackRejected as rejected:
            if "state mismatch" in rejected.message:
                # A stray or forged request must not cancel the real attempt.
                return False, rejected.message
            with self._attempt_lock:
                if attempt.status != "pending" or attempt.processing:
                    return False, "This sign-in attempt has already finished."
                return self._finish(attempt, rejected.status, rejected.message)
        with self._attempt_lock:
            # An authorization code is single-use: only the first valid callback is exchanged.
            if attempt.status != "pending" or attempt.processing:
                return False, "This sign-in attempt has already finished."
            attempt.processing = True
        try:
            return self.complete_login(attempt, result.code, result.client_id)
        finally:
            attempt.processing = False

    def complete_login(self, attempt: AuthAttempt, code: str, client_id: str) -> tuple[bool, str]:
        """Exchange the code, validate identity and permission, then store credentials."""
        try:
            tokens = self.oauth.exchange_code(
                client_id=client_id, code=code, code_verifier=attempt.code_verifier, redirect_uri=attempt.redirect_uri
            )
        except OAuthError as exc:
            if attempt.is_new_registration:
                # Keep the issued id so the fresh authorization the docs call for reuses it.
                self._retry_client_id = (client_id, time.time())
            if exc.code == "invalid_grant":
                return self._finish(attempt, "failed", "The authorization code was rejected. Start the sign-in again.")
            return self._finish(attempt, "failed", f"Token exchange failed: {exc.message}")
        except httpx.HTTPError:
            if attempt.is_new_registration:
                self._retry_client_id = (client_id, time.time())
            return self._finish(attempt, "failed", "Could not reach OpenAI to finish signing in. Check your connection.")

        if not tokens.id_token:
            return self._finish(attempt, "failed", "OpenAI did not return an ID token, so the identity could not be verified.")
        try:
            claims = self.oauth.validate_id_token(tokens.id_token, client_id=client_id, nonce=attempt.nonce)
        except OAuthError as exc:
            return self._finish(attempt, "failed", f"Identity verification failed: {exc.message}")
        except httpx.HTTPError:
            return self._finish(attempt, "failed", "Could not load OpenAI's signing keys to verify your identity.")

        subject = str(claims["sub"])
        issuer = str(claims.get("iss") or oauth.ISSUER)
        email = claims.get("email") if isinstance(claims.get("email"), str) else None

        if attempt.account_id:
            existing = self._row(attempt.account_id)
            if existing["subject"] != subject or existing["issuer"] != issuer:
                # Never overwrite one registration's credentials with another identity's tokens.
                return self._finish(
                    attempt, "failed",
                    "You signed in as a different ChatGPT account than the one selected. Its credentials were not changed.",
                )
            account_id = attempt.account_id
        else:
            match = self.db.one(
                "SELECT id FROM chatgpt_accounts WHERE client_id=? AND subject=? AND issuer=?",
                (client_id, subject, issuer),
            )
            account_id = match["id"] if match else uuid.uuid4().hex

        plan_enabled = PLAN_USAGE_SCOPE in tokens.scopes
        self._store_tokens(
            account_id, client_id=client_id, subject=subject, issuer=issuer, email=email, tokens=tokens, activate=True
        )
        self._retry_client_id = None
        attempt.result_account_id = account_id
        attempt.plan_usage_enabled = plan_enabled
        if plan_enabled:
            return self._finish(attempt, "completed", "Signed in. StarIndex can use your ChatGPT plan.")
        return self._finish(
            attempt, "completed",
            "Signed in, but permission to use your ChatGPT plan was not granted. ChatGPT plan usage is off.",
        )

    def _store_tokens(
        self,
        account_id: str,
        *,
        client_id: str,
        subject: str,
        issuer: str,
        email: str | None,
        tokens: TokenSet,
        activate: bool,
    ) -> None:
        previous = self._record(account_id)
        record = {
            "email": email,
            "issuer": issuer,
            "subject": subject,
            "client_id": client_id,
            "ext_agent_host_id": self.host_id(),
            "id_token": tokens.id_token or previous.get("id_token"),
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token or previous.get("refresh_token"),
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
            "scopes": tokens.scopes,
            "earliest_refresh_at": tokens.earliest_refresh_at,
            "saved_at": _iso(tokens.received_at),
            "saved_at_epoch": tokens.received_at,
        }
        self._save_record(account_id, record)
        plan_enabled = PLAN_USAGE_SCOPE in tokens.scopes
        expires_at = _iso(tokens.received_at + tokens.expires_in)
        with self.db.tx() as c:
            exists = c.execute("SELECT 1 FROM chatgpt_accounts WHERE id=?", (account_id,)).fetchone()
            if activate:
                c.execute("UPDATE chatgpt_accounts SET is_active=0")
            if exists:
                c.execute(
                    "UPDATE chatgpt_accounts SET email=?, scopes=?, plan_usage_enabled=?, signed_in=1, last_error=NULL, "
                    "saved_at=?, expires_at=?, is_active=CASE WHEN ? THEN 1 ELSE is_active END, label=? WHERE id=?",
                    (
                        email, dumps(tokens.scopes), int(plan_enabled), record["saved_at"], expires_at,
                        int(activate), self._unique_label(email, client_id, exclude_id=account_id), account_id,
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO chatgpt_accounts(id, label, email, subject, issuer, client_id, scopes, plan_usage_enabled, "
                    "is_active, signed_in, welcome_shown, saved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,1,0,?,?)",
                    (
                        account_id, self._unique_label(email, client_id), email, subject, issuer, client_id,
                        dumps(tokens.scopes), int(plan_enabled), int(activate), record["saved_at"], expires_at,
                    ),
                )

    # ------------------------------------------------------------------ account actions

    def activate(self, account_id: str) -> ChatGPTStatus:
        row = self._row(account_id)
        if not row["signed_in"]:
            raise _err("auth", "Sign in to this ChatGPT account before making it active.")
        with self.db.tx() as c:
            c.execute("UPDATE chatgpt_accounts SET is_active=0")
            c.execute("UPDATE chatgpt_accounts SET is_active=1 WHERE id=?", (account_id,))
        return self.status()

    def mark_welcome_shown(self, account_id: str) -> None:
        self._row(account_id)
        with self.db.tx() as c:
            c.execute("UPDATE chatgpt_accounts SET welcome_shown=1 WHERE id=?", (account_id,))

    def sign_out(self, account_id: str) -> ChatGPTStatus:
        """Revoke the renewable session, then clear tokens. The account/client mapping stays."""
        row = self._row(account_id)
        record = self._record(account_id)
        note: str | None = None
        refresh_token = record.get("refresh_token")
        if refresh_token:
            note = self._revoke(row["client_id"], refresh_token)
        for key in ("access_token", "refresh_token", "id_token"):
            record.pop(key, None)
        self._save_record(account_id, record)
        with self.db.tx() as c:
            c.execute(
                "UPDATE chatgpt_accounts SET signed_in=0, is_active=0, expires_at=NULL, last_error=? WHERE id=?",
                (note, account_id),
            )
        return self.status()

    def _revoke(self, client_id: str, refresh_token: str) -> str | None:
        """Best-effort revocation with bounded retries. Returns a note when it was not confirmed."""
        unconfirmed = (
            "Signed out locally, but remote revocation was not confirmed. "
            "You can disconnect StarIndex in ChatGPT Settings."
        )
        for delay in (0.0, *self._revoke_backoff):
            if delay:
                time.sleep(delay)
            try:
                self.oauth.revoke(client_id=client_id, refresh_token=refresh_token)
                return None
            except OAuthError as exc:
                if exc.status is not None and exc.status < 500:
                    return unconfirmed   # not retryable; the user can disconnect in ChatGPT Settings
            except httpx.HTTPError:
                pass
        return unconfirmed

    def delete(self, account_id: str) -> ChatGPTStatus:
        row = self._row(account_id)
        record = self._record(account_id)
        if record.get("refresh_token"):
            self._revoke(row["client_id"], record["refresh_token"])
        self.secrets.delete(chatgpt_account_key(account_id))
        with self.db.tx() as c:
            c.execute("DELETE FROM chatgpt_accounts WHERE id=?", (account_id,))
        return self.status()

    # ------------------------------------------------------------------ tokens

    @contextmanager
    def _refresh_guard(self, account_id: str) -> Iterator[None]:
        with self._locks_guard:
            lock = self._refresh_locks.setdefault(account_id, threading.Lock())
        with lock, _FileLock(self.data_dir / "locks" / f"chatgpt-{account_id}.lock"):
            yield

    @staticmethod
    def _expires_at(record: dict[str, Any]) -> float:
        saved = record.get("saved_at_epoch")
        if not isinstance(saved, int | float):
            return 0.0
        return float(saved) + float(record.get("expires_in") or 0)

    def _needs_refresh(self, record: dict[str, Any], now: float) -> bool:
        expires_at = self._expires_at(record)
        if now >= expires_at - 5:
            return True                                   # expired (or about to): must refresh
        if now < expires_at - REFRESH_MARGIN_S:
            return False
        earliest = record.get("earliest_refresh_at")
        return not (isinstance(earliest, int | float) and now < float(earliest))

    def access_token(self, account_id: str | None = None) -> str:
        """Access token for inference, refreshed near expiry. Raises ProviderError otherwise."""
        target = self.ready_account_id(account_id)
        record = self._record(target)
        if not record.get("access_token"):
            self._mark_signed_out(target, "Stored credentials are missing. Sign in again.")
            raise _err("auth", "The stored ChatGPT credentials are missing. Sign in again in Settings.")
        if not self._needs_refresh(record, time.time()):
            return record["access_token"]
        return self._refresh(target)

    def _refresh(self, account_id: str) -> str:
        with self._refresh_guard(account_id):
            # Another thread or process may have refreshed while we waited for the lock.
            record = self._record(account_id)
            if record.get("access_token") and not self._needs_refresh(record, time.time()):
                return record["access_token"]
            row = self._row(account_id)
            refresh_token = record.get("refresh_token")
            if not refresh_token:
                self._mark_signed_out(account_id, "No refresh token is stored. Sign in again.")
                raise _err("auth", "The ChatGPT session cannot be renewed. Sign in again in Settings.")
            try:
                tokens = self.oauth.refresh(client_id=row["client_id"], refresh_token=refresh_token)
            except OAuthError as exc:
                if exc.code in TERMINAL_REFRESH_CODES:
                    # The refresh token is unusable: clear tokens, keep the registration for re-auth.
                    for key in ("access_token", "refresh_token"):
                        record.pop(key, None)
                    self._save_record(account_id, record)
                    self._mark_signed_out(account_id, f"Session ended ({exc.code}). Sign in again.")
                    raise _err(
                        "auth", "Your ChatGPT session has ended. Sign in again in Settings.", code=exc.code, status=exc.status
                    ) from exc
                if exc.code == "invalid_client":
                    self._set_error(account_id, "OpenAI rejected this installation's client registration.")
                    raise _err(
                        "auth",
                        "OpenAI rejected this installation's client registration (invalid_client). "
                        "Remove the account in Settings and register again.",
                        code=exc.code, status=exc.status,
                    ) from exc
                # Temporary or unknown failure: keep credentials.
                raise _err(
                    "unavailable", f"Could not renew the ChatGPT session right now: {exc.message}",
                    code=exc.code, status=exc.status, retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise _err(
                    "network", "Could not reach OpenAI to renew the ChatGPT session.", retryable=True
                ) from exc
            # Replace access token, expiry, granted scopes and the rotating refresh token together.
            if not tokens.scopes:
                tokens.scopes = list(record.get("scopes") or [])
            self._store_tokens(
                account_id,
                client_id=row["client_id"],
                subject=row["subject"],
                issuer=row["issuer"],
                email=row["email"],
                tokens=TokenSet(
                    access_token=tokens.access_token,
                    refresh_token=tokens.refresh_token,
                    id_token=None,   # keep the ID token validated at sign-in for id_token_hint
                    token_type=tokens.token_type,
                    expires_in=tokens.expires_in,
                    scopes=tokens.scopes,
                    earliest_refresh_at=tokens.earliest_refresh_at,
                    received_at=tokens.received_at,
                ),
                activate=False,
            )
            if PLAN_USAGE_SCOPE not in tokens.scopes:
                raise _err("consent_required", "ChatGPT plan usage is no longer granted for this account.")
            return tokens.access_token

    def _mark_signed_out(self, account_id: str, reason: str) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE chatgpt_accounts SET signed_in=0, expires_at=NULL, last_error=? WHERE id=?", (reason, account_id)
            )

    def _set_error(self, account_id: str, reason: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE chatgpt_accounts SET last_error=? WHERE id=?", (reason, account_id))

    def note_error(self, message: str, account_id: str | None = None) -> None:
        """Record the last inference error for the account so Settings can show it."""
        target = account_id or self.active_account_id()
        if target:
            self._set_error(target, message[:500])

    def close(self) -> None:
        with self._attempt_lock:
            for attempt_id in list(self._servers):
                self._close_server(attempt_id)
        self.oauth.close()
