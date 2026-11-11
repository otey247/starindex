"""Sign in with ChatGPT - OAuth for open-source, locally hosted clients ("ChatGPT plan usage").

Implements only what OpenAI documents at
https://developers.openai.com/siwc/token-sharing-open-source (and its sign-in,
profiles-and-sessions, token-reference and errors-and-recovery pages):

* first-time registration through ``client_id=dynamic_agent_client`` with ``agent_name_hint``
  and a persistent, opaque ``ext_agent_host_id``;
* Authorization Code + PKCE (S256) with fresh ``state`` and ``nonce`` per attempt and an HTTP
  loopback redirect on ``127.0.0.1`` (never ``localhost``), path ``/auth/callback``;
* code exchange / refresh at the documented token endpoint as a public client (no secret);
* ID-token validation against OpenAI's published JWKS (signature, issuer, audience = issued
  client id, expiry, nonce);
* revocation of the renewable session on sign-out.

Nothing here touches ChatGPT ``backend-api`` routes, browser cookies or other clients' ids.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets as pysecrets
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx
import jwt

from ..base import ProviderError

log = logging.getLogger(__name__)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
DISCOVERY_URL = "https://auth.openai.com/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "StarIndex"
IDENTITY_SCOPES = ("openid", "profile", "email")
PLAN_SCOPES = ("offline_access", "resource.invoke", "chatgpt.tokens.use.direct")
PLAN_USAGE_SCOPE = "chatgpt.tokens.use.direct"
REQUESTED_SCOPE = " ".join((*IDENTITY_SCOPES, *PLAN_SCOPES))

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/auth/callback"
PREFERRED_CALLBACK_PORT = 1455
USAGE_URL = "https://chatgpt.com/settings/usage"

PROVIDER_ID = "chatgpt-plan"
PROVIDER_KIND = "chatgpt_plan"

# Refresh failures that mean the stored refresh token can never work again.
TERMINAL_REFRESH_CODES = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)
_ALLOWED_ID_TOKEN_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")
_CLOCK_SKEW_S = 60


def _err(kind: str, message: str, **kw: Any) -> ProviderError:
    return ProviderError(kind, message, provider_id=PROVIDER_ID, provider_kind=PROVIDER_KIND, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- host identifier


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_host_id() -> tuple[str, str | None]:
    """Create this host's ``ext_agent_host_id``.

    Preferred form is an RFC 9278 JWK-thumbprint URI derived from a freshly generated P-256
    public key; the private key (PEM) is returned so the caller can keep it in secret storage.
    If key generation is unavailable, a ``urn:uuid:`` identifier is used (also documented).
    The identifier is opaque and is not a credential.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        jwk = {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64url(numbers.x.to_bytes(32, "big")),
            "y": _b64url(numbers.y.to_bytes(32, "big")),
        }
        # RFC 7638: required members only, lexicographic order, no whitespace.
        canonical = json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode("utf-8")
        thumbprint = _b64url(hashlib.sha256(canonical).digest())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode("ascii")
        return f"urn:ietf:params:oauth:jwk-thumbprint:sha-256:{thumbprint}", pem
    except Exception:  # pragma: no cover - cryptography is a hard dependency via pyjwt[crypto]
        return f"urn:uuid:{uuid.uuid4()}", None


def is_valid_host_id(value: str | None) -> bool:
    if not value:
        return False
    return (
        value.startswith("urn:ietf:params:oauth:jwk-thumbprint:")
        or value.startswith("urn:uuid:")
        or value.startswith("did:key:")
    )


# ---------------------------------------------------------------------------- PKCE / attempt state


def new_pkce_verifier() -> str:
    return _b64url(pysecrets.token_bytes(48))


def pkce_challenge(verifier: str) -> str:
    """Base64url (unpadded) SHA-256 digest of the verifier."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


@dataclass
class AuthAttempt:
    """Everything that must survive from opening the browser until the callback completes."""

    attempt_id: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    client_id: str                    # ``dynamic_agent_client`` or a saved issued client id
    is_new_registration: bool
    account_id: str | None = None     # set when re-authorising a saved account
    enable_plan_usage: bool = False
    created_at: float = field(default_factory=time.time)
    status: str = "pending"           # pending | completed | denied | failed | expired
    message: str = ""
    result_account_id: str | None = None
    plan_usage_enabled: bool | None = None
    auth_url: str = ""
    processing: bool = False          # a callback is being exchanged; later callbacks are ignored


def new_attempt(
    *, redirect_uri: str, client_id: str, is_new_registration: bool, account_id: str | None, enable_plan_usage: bool
) -> AuthAttempt:
    return AuthAttempt(
        attempt_id=uuid.uuid4().hex,
        state=_b64url(pysecrets.token_bytes(32)),
        nonce=_b64url(pysecrets.token_bytes(32)),
        code_verifier=new_pkce_verifier(),
        redirect_uri=redirect_uri,
        client_id=client_id,
        is_new_registration=is_new_registration,
        account_id=account_id,
        enable_plan_usage=enable_plan_usage,
    )


def build_authorize_url(
    attempt: AuthAttempt,
    *,
    host_id: str,
    id_token_hint: str | None = None,
    login_hint: str | None = None,
    force_consent: bool = False,
) -> str:
    """Authorization request per the documented parameter table.

    ``agent_name_hint`` is sent only on the initial dynamic registration; the hints are sent
    only when re-authorising a saved account. ``prompt=consent`` is the documented way to ask
    for consent again when the user explicitly enables plan usage.
    """
    params: list[tuple[str, str]] = [("client_id", attempt.client_id)]
    if attempt.client_id == DYNAMIC_CLIENT_ID:
        params.append(("agent_name_hint", AGENT_NAME))
    params.append(("ext_agent_host_id", host_id))
    if attempt.client_id != DYNAMIC_CLIENT_ID:
        if id_token_hint:
            params.append(("id_token_hint", id_token_hint))
        if login_hint:
            params.append(("login_hint", login_hint))
    params.extend(
        [
            ("response_type", "code"),
            ("redirect_uri", attempt.redirect_uri),
            ("scope", REQUESTED_SCOPE),
            ("resource", RESOURCE),
            ("state", attempt.state),
            ("nonce", attempt.nonce),
            ("code_challenge_method", "S256"),
            ("code_challenge", pkce_challenge(attempt.code_verifier)),
        ]
    )
    if force_consent:
        params.append(("prompt", "consent"))
    return AUTHORIZE_URL + "?" + urlencode(params, quote_via=quote)


def redact_url(url: str) -> str:
    """Authorization URLs can carry ``id_token_hint``; never log them verbatim."""
    parts = urlsplit(url)
    safe = []
    for key, values in parse_qs(parts.query, keep_blank_values=True).items():
        for value in values:
            if key in ("id_token_hint", "login_hint", "state", "nonce", "code_challenge", "code"):
                safe.append(f"{key}=<redacted>")
            else:
                safe.append(f"{key}={quote(value, safe='')}")
    return f"{parts.scheme}://{parts.netloc}{parts.path}?{'&'.join(safe)}"


# ---------------------------------------------------------------------------- callback validation


@dataclass(frozen=True)
class CallbackResult:
    code: str
    client_id: str   # the issued client id to use for the exchange


class CallbackRejected(Exception):
    """The callback must not be exchanged. ``status`` is the attempt status to record."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_callback(attempt: AuthAttempt, params: dict[str, str]) -> CallbackResult:
    """Apply the documented callback rules. Raises ``CallbackRejected``; never exchanges."""
    state = params.get("state") or ""
    if not state or not pysecrets.compare_digest(state.encode(), attempt.state.encode()):
        raise CallbackRejected("failed", "The sign-in response did not match this sign-in attempt (state mismatch).")
    error = params.get("error")
    if error:
        if error == "access_denied":
            raise CallbackRejected(
                "denied",
                "Sign-in was cancelled or permission was declined. ChatGPT plan usage stays off; "
                "you can enable it later from Settings.",
            )
        description = params.get("error_description") or error
        raise CallbackRejected("failed", f"OpenAI returned an authorization error: {description}")
    returned_client = params.get("client_id") or ""
    if attempt.is_new_registration and attempt.client_id == DYNAMIC_CLIENT_ID:
        if not returned_client or returned_client == DYNAMIC_CLIENT_ID:
            raise CallbackRejected(
                "failed", "Registration is incomplete: OpenAI did not return an issued client ID."
            )
        client_id = returned_client
    else:
        if returned_client and returned_client != attempt.client_id:
            raise CallbackRejected(
                "failed", "The sign-in response named a different client than this account's registration."
            )
        client_id = attempt.client_id
    code = params.get("code") or ""
    if not code:
        raise CallbackRejected("failed", "The sign-in response did not include an authorization code.")
    return CallbackResult(code=code, client_id=client_id)


# ---------------------------------------------------------------------------- token endpoint


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    token_type: str
    expires_in: int
    scopes: list[str]
    earliest_refresh_at: float | None
    received_at: float


class OAuthError(Exception):
    """Machine-readable failure from the token or revocation endpoint."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _oauth_error_from(response: httpx.Response) -> OAuthError:
    code = "server_error" if response.status_code >= 500 else "invalid_request"
    message = f"HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, str):
            code = err
            message = str(body.get("error_description") or err)
        elif isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or code)
            message = str(err.get("message") or code)
        elif isinstance(body.get("detail"), str):
            message = body["detail"]
    return OAuthError(code, message, status=response.status_code)


def _parse_earliest_refresh(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value:
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _token_set(body: dict[str, Any]) -> TokenSet:
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise OAuthError("invalid_response", "The token response did not contain an access token.")
    scope = body.get("scope")
    scopes = sorted(scope.split()) if isinstance(scope, str) else []
    expires = body.get("expires_in")
    return TokenSet(
        access_token=access,
        refresh_token=body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else None,
        id_token=body.get("id_token") if isinstance(body.get("id_token"), str) else None,
        token_type=str(body.get("token_type") or "Bearer"),
        expires_in=int(expires) if isinstance(expires, int | float) else 3600,
        scopes=scopes,
        earliest_refresh_at=_parse_earliest_refresh(body.get("earliest_refresh_at")),
        received_at=time.time(),
    )


class OAuthClient:
    """HTTP side of the flow. ``transport`` is injectable so tests never touch the network."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(connect=10, read=30, write=30, pool=10),
            follow_redirects=False,
        )
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = 0.0
        self._lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # -- discovery / keys -------------------------------------------------------------------

    def discovery(self) -> dict[str, Any]:
        with self._lock:
            if self._discovery is None:
                response = self._http.get(DISCOVERY_URL)
                if response.status_code != 200:
                    raise OAuthError("discovery_failed", f"OpenID discovery failed (HTTP {response.status_code}).")
                doc = response.json()
                if doc.get("issuer") != ISSUER:
                    raise OAuthError("discovery_failed", "OpenID discovery returned an unexpected issuer.")
                self._discovery = doc
            return self._discovery

    def _endpoint(self, name: str) -> str:
        url = self.discovery().get(name)
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise OAuthError("discovery_failed", f"OpenID discovery has no usable {name}.")
        return url

    def jwks(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = self._jwks is not None and time.time() - self._jwks_fetched_at < 3600
        if fresh and not force:
            return self._jwks  # type: ignore[return-value]
        url = self._endpoint("jwks_uri")
        response = self._http.get(url)
        if response.status_code != 200:
            raise OAuthError("jwks_failed", f"Could not load OpenAI's signing keys (HTTP {response.status_code}).")
        with self._lock:
            self._jwks = response.json()
            self._jwks_fetched_at = time.time()
            return self._jwks

    # -- grants -----------------------------------------------------------------------------

    def exchange_code(self, *, client_id: str, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        """``authorization_code`` grant: form-encoded, issued client id, PKCE verifier, no secret."""
        if client_id == DYNAMIC_CLIENT_ID:
            raise OAuthError("invalid_client", "The registration entry point cannot be used for token exchange.")
        response = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "resource": RESOURCE,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise _oauth_error_from(response)
        return _token_set(response.json())

    def refresh(self, *, client_id: str, refresh_token: str) -> TokenSet:
        """``refresh_token`` grant. ``scope`` is omitted so the existing grant is retained."""
        response = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": RESOURCE,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise _oauth_error_from(response)
        return _token_set(response.json())

    def revoke(self, *, client_id: str, refresh_token: str) -> None:
        """End the renewable session. An empty 200 is success, also for an already-invalid token."""
        url = self._endpoint("revocation_endpoint")
        response = self._http.post(
            url,
            data={"token": refresh_token, "token_type_hint": "refresh_token", "client_id": client_id},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise _oauth_error_from(response)

    # -- ID token ---------------------------------------------------------------------------

    def validate_id_token(self, id_token: str, *, client_id: str, nonce: str) -> dict[str, Any]:
        """Verify signature (JWKS), issuer, audience, expiry and nonce. Returns the claims."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OAuthError("invalid_id_token", "The ID token is malformed.") from exc
        alg = header.get("alg")
        if alg not in _ALLOWED_ID_TOKEN_ALGS:
            raise OAuthError("invalid_id_token", "The ID token uses a signing algorithm that is not accepted.")
        key = self._signing_key(header.get("kid"), force=False) or self._signing_key(header.get("kid"), force=True)
        if key is None:
            raise OAuthError("invalid_id_token", "No published OpenAI signing key matches the ID token.")
        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=[alg],
                audience=client_id,
                issuer=ISSUER,
                leeway=_CLOCK_SKEW_S,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise OAuthError("invalid_id_token", "The ID token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise OAuthError("invalid_id_token", "The ID token was issued for a different client.") from exc
        except jwt.InvalidIssuerError as exc:
            raise OAuthError("invalid_id_token", "The ID token was not issued by OpenAI.") from exc
        except jwt.InvalidSignatureError as exc:
            raise OAuthError("invalid_id_token", "The ID token signature is not valid.") from exc
        except jwt.PyJWTError as exc:
            raise OAuthError("invalid_id_token", f"The ID token could not be validated ({type(exc).__name__}).") from exc
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not pysecrets.compare_digest(token_nonce.encode(), nonce.encode()):
            raise OAuthError("invalid_id_token", "The ID token nonce does not match this sign-in attempt.")
        if not claims.get("sub"):
            raise OAuthError("invalid_id_token", "The ID token has no subject.")
        return claims

    def _signing_key(self, kid: str | None, *, force: bool) -> Any:
        try:
            keyset = jwt.PyJWKSet.from_dict(self.jwks(force=force))
        except jwt.PyJWTError:
            return None
        candidates = [k for k in keyset.keys if kid is None or k.key_id == kid]
        if kid is None and len(candidates) != 1:
            return None
        return candidates[0].key if candidates else None


# ---------------------------------------------------------------------------- loopback listener

CallbackHandler = Callable[[dict[str, str]], tuple[bool, str]]


def _page(title: str, message: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>StarIndex</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;background:#16181d;color:#e8eaed;"
        "display:grid;place-items:center;height:100vh;margin:0}main{max-width:32rem;padding:2rem}"
        "h1{font-size:1.4rem;margin:0 0 .5rem}p{color:#aeb4bd;margin:0}</style></head><body><main>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></main></body></html>"
    ).encode()


class CallbackServer:
    """One-shot HTTP listener on 127.0.0.1 for the OAuth redirect.

    Bound to the loopback interface only. Only ``GET /auth/callback`` is served; everything
    else is a 404. The handler decides the outcome; the page shown never contains tokens.
    """

    def __init__(self, handler: CallbackHandler, *, preferred_port: int = PREFERRED_CALLBACK_PORT):
        self._handler = handler
        self._server = self._bind(preferred_port)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="chatgpt-oauth-callback", daemon=True)
        self._closed = False

    @property
    def redirect_uri(self) -> str:
        return f"http://{CALLBACK_HOST}:{self.port}{CALLBACK_PATH}"

    def _bind(self, preferred_port: int) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                parts = urlsplit(self.path)
                if parts.path != CALLBACK_PATH:
                    self._send(404, _page("Not found", "This address only completes StarIndex sign-in."))
                    return
                params = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
                try:
                    ok, message = owner._handler(params)
                except Exception:  # never leak internals into the browser page
                    log.exception("ChatGPT sign-in callback failed")
                    ok, message = False, "StarIndex could not complete the sign-in. Check the app for details."
                title = "You're signed in" if ok else "Sign-in was not completed"
                self._send(200 if ok else 400, _page(title, message + " You can close this tab and return to StarIndex."))

            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
                return  # request lines contain the authorization code; never log them

        class Server(ThreadingHTTPServer):
            # On Windows SO_REUSEADDR lets two listeners share a port; never allow that for OAuth.
            allow_reuse_address = sys.platform != "win32"
            daemon_threads = True

        for port in (preferred_port, 0):
            try:
                return Server((CALLBACK_HOST, port), Handler)
            except OSError:
                continue
        raise _err("unavailable", "Could not open a local port for the sign-in callback.")

    def start(self) -> None:
        self._thread.start()

    def close(self, *, wait: bool = False) -> None:
        """Stop listening. ``wait`` blocks (briefly) until the port is released."""
        if self._closed:
            return
        self._closed = True

        def _shutdown() -> None:
            try:
                if self._thread.is_alive():
                    self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

        # shutdown() blocks until serve_forever returns; never call it from a handler thread.
        closer = threading.Thread(target=_shutdown, name="chatgpt-oauth-callback-close", daemon=True)
        closer.start()
        if wait:
            closer.join(timeout=3)
