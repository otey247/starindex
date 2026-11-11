"""A fake of the OpenAI endpoints used by Sign in with ChatGPT, for MockTransport."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from starindex.providers.chatgpt_plan import oauth

ALL_SCOPES = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
NO_PLAN_SCOPES = "email offline_access openid profile resource.invoke"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
REVOKE_URL = "https://auth.openai.com/api/accounts/oauth/revoke"
ISSUED_CLIENT = "oaiapp_issued_123456"


class FakeOpenAI:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.requests: list[httpx.Request] = []
        self.token_responses: list[httpx.Response] = []     # queued responses for the token endpoint
        self.revoke_responses: list[httpx.Response | Exception] = []
        self.models_response: httpx.Response | None = None
        self.responses_response: httpx.Response | None = None
        self.refresh_counter = 0
        self.token_delay_s = 0.0

    # -- helpers ----------------------------------------------------------------------------

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def id_token(
        self, *, nonce: str, aud: Any = ISSUED_CLIENT, iss: str = oauth.ISSUER, sub: str = "user-sub-1",
        email: str = "user@example.com", exp_delta: int = 600, key=None, kid: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": sub, "email": email, "nonce": nonce, "iat": now, "exp": now + exp_delta}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})

    def token_body(self, *, nonce: str | None = None, scope: str = ALL_SCOPES, expires_in: int = 3600, **id_kw: Any) -> dict[str, Any]:
        body = {
            "access_token": f"access-{self.refresh_counter}",
            "refresh_token": f"refresh-{self.refresh_counter}",
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": scope,
        }
        if nonce is not None:
            body["id_token"] = self.id_token(nonce=nonce, **id_kw)
        return body

    def queue_token(self, body_or_response: Any, status: int = 200) -> None:
        response = body_or_response if isinstance(body_or_response, httpx.Response) else httpx.Response(status, json=body_or_response)
        self.token_responses.append(response)

    def sent(self, url_prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(url_prefix)]

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    # -- transport handler --------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url == oauth.DISCOVERY_URL:
            return httpx.Response(200, json={
                "issuer": oauth.ISSUER, "authorization_endpoint": oauth.AUTHORIZE_URL, "token_endpoint": oauth.TOKEN_URL,
                "jwks_uri": JWKS_URL, "revocation_endpoint": REVOKE_URL,
            })
        if url == JWKS_URL:
            return httpx.Response(200, json=self.jwks())
        if url == oauth.TOKEN_URL:
            if self.token_delay_s:
                time.sleep(self.token_delay_s)
            if self.token_responses:
                return self.token_responses.pop(0)
            if self.form(request).get("grant_type") == "refresh_token":
                self.refresh_counter += 1
                return httpx.Response(200, json=self.token_body())
            return httpx.Response(400, json={"error": "invalid_request"})
        if url == REVOKE_URL:
            if self.revoke_responses:
                item = self.revoke_responses.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return httpx.Response(200)
        if url == "https://api.openai.com/v1/models":
            return self.models_response or httpx.Response(200, json={"models": []})
        if url == "https://api.openai.com/v1/responses":
            return self.responses_response or httpx.Response(500, json={"error": {"message": "no response queued"}})
        return httpx.Response(404, json={"error": "unexpected url in test", "url": url})


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def responses_sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
