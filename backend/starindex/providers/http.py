"""HTTP plumbing shared by every provider adapter.

One place for: client construction (with transport injection for tests), base-URL
validation, local-vs-remote host classification, SSE / NDJSON parsing, and the mapping of
HTTP statuses and transport failures onto ``ProviderError`` kinds.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import ChatProvider, ErrorKind, ProviderConfig, ProviderError

CONNECT_TIMEOUT_S = 10.0
DEFAULT_READ_TIMEOUT_S = 180.0
LIST_TIMEOUT_S = 20.0
MAX_ERROR_BODY = 64_000

_CONTEXT_PATTERNS = re.compile(
    r"context[ _]length|context window|maximum context|max(imum)? (input )?tokens|too many tokens|"
    r"prompt is too long|input is too long|reduce the length|exceeds? the (model'?s? )?(maximum|limit)|"
    r"token limit|context_length_exceeded|string too long|request too large",
    re.IGNORECASE,
)
_QUOTA_PATTERNS = re.compile(
    r"insufficient_quota|exceeded your current quota|billing|credit balance|out of credits|payment",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------- URLs and hosts


def validate_base_url(url: str | None, *, provider_kind: str | None = None) -> str:
    """Return a normalised base URL (no trailing slash) or raise ``ProviderError('bad_request')``.

    Only http/https are accepted, and credentials must never be embedded in the URL (they
    would end up in the database, logs and the browser).
    """
    if not url or not url.strip():
        raise ProviderError("bad_request", "A base URL is required.", provider_kind=provider_kind)
    cleaned = url.strip()
    try:
        parts = urlsplit(cleaned)
    except ValueError as exc:
        raise ProviderError("bad_request", f"Invalid base URL: {exc}", provider_kind=provider_kind) from exc
    if parts.scheme not in ("http", "https"):
        raise ProviderError(
            "bad_request", "The base URL must start with http:// or https://.", provider_kind=provider_kind
        )
    if not parts.hostname:
        raise ProviderError("bad_request", "The base URL has no host.", provider_kind=provider_kind)
    if parts.username or parts.password:
        raise ProviderError(
            "bad_request",
            "Do not put credentials in the base URL; store the API key separately.",
            provider_kind=provider_kind,
        )
    if parts.fragment:
        raise ProviderError("bad_request", "The base URL must not contain a fragment.", provider_kind=provider_kind)
    return cleaned.rstrip("/")


def is_private_host(host: str | None) -> bool:
    """True for loopback and private-network hosts (traffic that stays on the user's network)."""
    if not host:
        return False
    h = host.strip().strip("[]").lower()
    if h in ("localhost", "host.docker.internal") or h.endswith(".localhost") or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # A bare single-label name (e.g. "gpu-box") resolves on the local network only.
        return "." not in h
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        return is_private_host(urlsplit(url).hostname)
    except ValueError:
        return False


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


# ---------------------------------------------------------------------------- client


def make_client(
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S, read=timeout_s or DEFAULT_READ_TIMEOUT_S, write=30.0, pool=CONNECT_TIMEOUT_S
    )
    return httpx.Client(
        headers=headers or {},
        timeout=timeout,
        transport=transport,
        follow_redirects=False,   # never forward credentials to a redirect target
    )


def request_timeout(read_s: float | None) -> httpx.Timeout:
    return httpx.Timeout(connect=CONNECT_TIMEOUT_S, read=read_s or DEFAULT_READ_TIMEOUT_S, write=30.0, pool=CONNECT_TIMEOUT_S)


# ---------------------------------------------------------------------------- stream parsing


@dataclass(frozen=True)
class SSEMessage:
    event: str | None
    data: str


def iter_sse(lines: Iterator[str]) -> Iterator[SSEMessage]:
    """Parse a server-sent-event stream from decoded lines (WHATWG framing)."""
    event: str | None = None
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data or event is not None:
                yield SSEMessage(event, "\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
    if data or event is not None:
        yield SSEMessage(event, "\n".join(data))


def iter_ndjson(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Parse newline-delimited JSON objects; malformed lines raise ``ValueError``."""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            yield value


# ---------------------------------------------------------------------------- error mapping


def extract_error(body: Any) -> tuple[str, str | None, str | None]:
    """Pull (message, code, param) out of the error shapes providers actually send."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = err.get("message") or err.get("detail") or json.dumps(err)[:500]
            code = err.get("code") or err.get("type") or err.get("status")
            param = err.get("param")
            return str(message), (str(code) if code is not None else None), (str(param) if param else None)
        if isinstance(err, str):
            desc = body.get("error_description") or body.get("message")
            return (f"{err}: {desc}" if desc else err), err, None
        for key in ("detail", "message"):
            if isinstance(body.get(key), str):
                return body[key], None, None
        if isinstance(body.get("detail"), list | dict):
            return json.dumps(body["detail"])[:500], None, None
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return extract_error(body[0])
    if isinstance(body, str) and body.strip():
        return body.strip()[:500], None, None
    return "", None, None


def kind_for_status(status: int, message: str = "", code: str | None = None) -> tuple[ErrorKind, bool]:
    """Default status → (kind, retryable). Adapters refine with provider-specific codes."""
    text = f"{message} {code or ''}"
    if status in (401,):
        return "auth", False
    if status == 403:
        return "auth", False
    if status == 402:
        return "usage_limit", False
    if status == 404:
        return "unsupported", False
    if status == 408:
        return "timeout", True
    if status == 413:
        return "context_length", False
    if status == 429:
        if _QUOTA_PATTERNS.search(text):
            return "usage_limit", False
        return "rate_limit", True
    if status in (400, 422):
        if _CONTEXT_PATTERNS.search(text):
            return "context_length", False
        return "bad_request", False
    if status >= 500:
        return "unavailable", True
    return "bad_request", False


def parse_retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def read_error_body(response: httpx.Response) -> Any:
    """Read a (possibly streaming) error response body without raising."""
    try:
        raw = response.read()[:MAX_ERROR_BODY]
    except httpx.HTTPError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def error_from_response(
    response: httpx.Response,
    *,
    provider_id: str | None,
    provider_kind: str | None,
    request_id_headers: tuple[str, ...] = ("x-request-id", "request-id"),
    refine: Callable[[int, str, str | None, Any], tuple[ErrorKind, bool] | None] | None = None,
) -> ProviderError:
    body = read_error_body(response)
    message, code, param = extract_error(body)
    kind, retryable = kind_for_status(response.status_code, message, code)
    if refine is not None:
        refined = refine(response.status_code, message, code, body)
        if refined is not None:
            kind, retryable = refined
    request_id = next((response.headers[h] for h in request_id_headers if h in response.headers), None)
    if not message:
        message = f"HTTP {response.status_code} from the model provider."
    return ProviderError(
        kind,
        _friendly(kind, message, response.status_code),
        provider_id=provider_id,
        provider_kind=provider_kind,
        status=response.status_code,
        code=code,
        param=param,
        request_id=request_id,
        retryable=retryable,
        retry_after_s=parse_retry_after(response.headers),
    )


def _friendly(kind: str, message: str, status: int) -> str:
    prefix = {
        "auth": "Authentication failed",
        "rate_limit": "Rate limited",
        "usage_limit": "Usage or quota limit reached",
        "context_length": "The request is too large for this model",
        "unavailable": "The provider is unavailable",
        "timeout": "The provider timed out",
        "unsupported": "Not supported or not found",
        "bad_request": "The provider rejected the request",
    }.get(kind)
    return f"{prefix} (HTTP {status}): {message}" if prefix else message


def map_transport_error(
    exc: Exception, *, provider_id: str | None, provider_kind: str | None, base_url: str | None = None,
    mid_stream: bool = False,
) -> ProviderError:
    where = f" at {base_url}" if base_url else ""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            "timeout", f"The request to the model provider{where} timed out.",
            provider_id=provider_id, provider_kind=provider_kind, retryable=True,
        )
    if mid_stream and isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError | httpx.StreamError):
        return ProviderError(
            "stream_incomplete", "The connection dropped before the response finished.",
            provider_id=provider_id, provider_kind=provider_kind, retryable=True,
        )
    if isinstance(exc, httpx.ConnectError):
        hint = " Is the server running?" if is_local_url(base_url) else ""
        return ProviderError(
            "network", f"Could not connect to the model provider{where}.{hint}",
            provider_id=provider_id, provider_kind=provider_kind, retryable=True,
        )
    return ProviderError(
        "network", f"Network error talking to the model provider{where}: {type(exc).__name__}",
        provider_id=provider_id, provider_kind=provider_kind, retryable=True,
    )


# ---------------------------------------------------------------------------- adapter base


class HttpChatProvider(ChatProvider):
    """Common machinery for adapters that speak HTTP with an optional API key."""

    request_id_headers: tuple[str, ...] = ("x-request-id", "request-id")

    def __init__(
        self,
        config: ProviderConfig,
        secret: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(config)
        self._secret = secret or None
        self._timeout_s = float(config.options.get("timeout_s") or DEFAULT_READ_TIMEOUT_S)
        self._client = make_client(timeout_s=self._timeout_s, transport=transport)

    # subclasses override ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {}

    def _refine_error(self, status: int, message: str, code: str | None, body: Any) -> tuple[ErrorKind, bool] | None:
        return None

    @property
    def base_url(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    # helpers ---------------------------------------------------------------------------

    def _error(self, kind: ErrorKind, message: str, **kw: Any) -> ProviderError:
        return ProviderError(kind, message, provider_id=self.id, provider_kind=self.kind, **kw)

    def _require_secret(self) -> str:
        if not self._secret:
            raise self._error("not_configured", f"No API key is stored for {self.config.name}. Add one in Settings.")
        return self._secret

    def _response_error(self, response: httpx.Response) -> ProviderError:
        return error_from_response(
            response,
            provider_id=self.id,
            provider_kind=self.kind,
            request_id_headers=self.request_id_headers,
            refine=self._refine_error,
        )

    def _transport_error(self, exc: Exception, *, mid_stream: bool = False) -> ProviderError:
        return map_transport_error(
            exc, provider_id=self.id, provider_kind=self.kind, base_url=self.base_url or None, mid_stream=mid_stream
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout_s: float = LIST_TIMEOUT_S,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method, url, json=json_body, params=params,
                headers={**self._headers(), **(headers or {})}, timeout=request_timeout(timeout_s),
            )
        except httpx.HTTPError as exc:
            raise self._transport_error(exc) from exc
        if response.status_code >= 400:
            raise self._response_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise self._error("invalid_response", "The provider returned a response that is not JSON.") from exc

    @contextmanager
    def _open_stream(
        self,
        url: str,
        *,
        json_body: Any,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[httpx.Response]:
        """POST and yield the open streaming response after the status check.

        Transport failures while the caller iterates are converted to ``ProviderError`` too.
        """
        opened = False
        try:
            with self._client.stream(
                "POST", url, json=json_body, params=params,
                headers={**self._headers(), **(headers or {})},
                timeout=request_timeout(timeout_s or self._timeout_s),
            ) as response:
                if response.status_code >= 400:
                    raise self._response_error(response)
                opened = True
                yield response
        except httpx.HTTPError as exc:
            raise self._transport_error(exc, mid_stream=opened) from exc

    def close(self) -> None:
        self._client.close()
