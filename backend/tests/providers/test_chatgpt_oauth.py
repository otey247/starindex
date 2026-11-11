"""OAuth mechanics of Sign in with ChatGPT, checked against the documented parameter tables."""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from starindex.providers.chatgpt_plan import oauth
from starindex.providers.chatgpt_plan.oauth import CallbackRejected, OAuthClient, OAuthError

from .chatgpt_fakes import ALL_SCOPES, ISSUED_CLIENT, FakeOpenAI, query

REDIRECT = "http://127.0.0.1:1455/auth/callback"
HOST_ID = "urn:uuid:11111111-2222-4333-8444-555555555555"


def attempt(client_id=oauth.DYNAMIC_CLIENT_ID, new=True, account_id=None):
    return oauth.new_attempt(redirect_uri=REDIRECT, client_id=client_id, is_new_registration=new, account_id=account_id, enable_plan_usage=False)


def test_host_id_is_a_jwk_thumbprint_uri_and_stable_format():
    host_id, pem = oauth.generate_host_id()
    assert host_id.startswith("urn:ietf:params:oauth:jwk-thumbprint:sha-256:")
    assert len(host_id.rsplit(":", 1)[1]) == 43  # unpadded base64url SHA-256
    assert pem and "PRIVATE KEY" in pem
    assert oauth.is_valid_host_id(host_id) and oauth.is_valid_host_id(HOST_ID) and oauth.is_valid_host_id("did:key:z6Mk")
    assert not oauth.is_valid_host_id("user@example.com") and not oauth.is_valid_host_id(None)
    assert oauth.generate_host_id()[0] != host_id


def test_pkce_challenge_is_unpadded_base64url_sha256():
    verifier = oauth.new_pkce_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert oauth.pkce_challenge(verifier) == expected and "=" not in expected
    assert 43 <= len(verifier) <= 128


def test_first_time_authorize_url_uses_dynamic_registration():
    a = attempt()
    url = oauth.build_authorize_url(a, host_id=HOST_ID, id_token_hint="should-not-be-sent", login_hint="x@example.com")
    assert url.startswith("https://auth.openai.com/api/accounts/authorize?")
    q = query(url)
    assert q["client_id"] == "dynamic_agent_client"
    assert q["agent_name_hint"] == "StarIndex"
    assert q["ext_agent_host_id"] == HOST_ID
    assert q["response_type"] == "code"
    assert q["redirect_uri"] == REDIRECT and "localhost" not in q["redirect_uri"]
    assert q["scope"].split() == ["openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"]
    assert q["resource"] == "https://api.openai.com/v1"
    assert q["state"] == a.state and q["nonce"] == a.nonce
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] == oauth.pkce_challenge(a.code_verifier)
    assert "id_token_hint" not in q and "login_hint" not in q and "prompt" not in q
    assert "client_secret" not in q and a.code_verifier not in url


def test_returning_authorize_url_reuses_issued_client_and_hints():
    a = attempt(client_id=ISSUED_CLIENT, new=False, account_id="acc1")
    q = query(oauth.build_authorize_url(a, host_id=HOST_ID, id_token_hint="old.id.token", login_hint="user@example.com"))
    assert q["client_id"] == ISSUED_CLIENT
    assert "agent_name_hint" not in q
    assert q["ext_agent_host_id"] == HOST_ID
    assert q["id_token_hint"] == "old.id.token" and q["login_hint"] == "user@example.com"


def test_reconsent_uses_prompt_consent_only_when_asked():
    a = attempt(client_id=ISSUED_CLIENT, new=False, account_id="acc1")
    assert query(oauth.build_authorize_url(a, host_id=HOST_ID, force_consent=True))["prompt"] == "consent"
    assert "force_reconsent" not in oauth.build_authorize_url(a, host_id=HOST_ID, force_consent=True)


def test_each_attempt_has_fresh_state_nonce_and_verifier():
    a, b = attempt(), attempt()
    assert a.state != b.state and a.nonce != b.nonce and a.code_verifier != b.code_verifier
    assert a.state != a.nonce


def test_redact_url_hides_hints_and_state():
    a = attempt(client_id=ISSUED_CLIENT, new=False)
    redacted = oauth.redact_url(oauth.build_authorize_url(a, host_id=HOST_ID, id_token_hint="secret.jwt.value", login_hint="me@example.com"))
    assert "secret.jwt.value" not in redacted and "me%40example.com" not in redacted and a.state not in redacted
    assert "id_token_hint=<redacted>" in redacted and ISSUED_CLIENT in redacted


def test_callback_new_registration_returns_issued_client_id():
    a = attempt()
    result = oauth.validate_callback(a, {"code": "abc", "state": a.state, "client_id": ISSUED_CLIENT, "scope": ALL_SCOPES})
    assert (result.code, result.client_id) == ("abc", ISSUED_CLIENT)


def test_callback_state_mismatch_is_rejected():
    a = attempt()
    with pytest.raises(CallbackRejected) as info:
        oauth.validate_callback(a, {"code": "abc", "state": "wrong", "client_id": ISSUED_CLIENT})
    assert "state mismatch" in info.value.message
    with pytest.raises(CallbackRejected):
        oauth.validate_callback(a, {"code": "abc", "client_id": ISSUED_CLIENT})


def test_callback_access_denied_is_not_exchanged():
    a = attempt()
    with pytest.raises(CallbackRejected) as info:
        oauth.validate_callback(a, {"error": "access_denied", "state": a.state, "code": "should-be-ignored"})
    assert info.value.status == "denied"


def test_callback_other_oauth_error_fails():
    a = attempt()
    with pytest.raises(CallbackRejected) as info:
        oauth.validate_callback(a, {"error": "server_error", "error_description": "boom", "state": a.state})
    assert info.value.status == "failed" and "boom" in info.value.message


@pytest.mark.parametrize("returned", [None, "", "dynamic_agent_client"])
def test_callback_new_registration_without_issued_client_id_is_incomplete(returned):
    a = attempt()
    params = {"code": "abc", "state": a.state}
    if returned is not None:
        params["client_id"] = returned
    with pytest.raises(CallbackRejected) as info:
        oauth.validate_callback(a, params)
    assert "incomplete" in info.value.message


def test_callback_reauth_may_omit_client_id_but_rejects_a_different_one():
    a = attempt(client_id=ISSUED_CLIENT, new=False, account_id="acc1")
    assert oauth.validate_callback(a, {"code": "abc", "state": a.state}).client_id == ISSUED_CLIENT
    assert oauth.validate_callback(a, {"code": "abc", "state": a.state, "client_id": ISSUED_CLIENT}).client_id == ISSUED_CLIENT
    with pytest.raises(CallbackRejected) as info:
        oauth.validate_callback(a, {"code": "abc", "state": a.state, "client_id": "oaiapp_someone_else"})
    assert "different client" in info.value.message


def test_callback_without_code_fails():
    a = attempt()
    with pytest.raises(CallbackRejected):
        oauth.validate_callback(a, {"state": a.state, "client_id": ISSUED_CLIENT})


def test_code_exchange_form_body():
    fake = FakeOpenAI()
    fake.queue_token(fake.token_body(nonce="n"))
    client = OAuthClient(transport=fake.transport)
    tokens = client.exchange_code(client_id=ISSUED_CLIENT, code="the-code", code_verifier="the-verifier", redirect_uri=REDIRECT)
    sent = fake.sent(oauth.TOKEN_URL)[0]
    assert sent.method == "POST" and sent.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert fake.form(sent) == {
        "grant_type": "authorization_code", "client_id": ISSUED_CLIENT, "code": "the-code",
        "code_verifier": "the-verifier", "redirect_uri": REDIRECT, "resource": "https://api.openai.com/v1",
    }
    assert "client_secret" not in fake.form(sent) and "authorization" not in sent.headers
    assert tokens.access_token == "access-0" and tokens.refresh_token == "refresh-0"
    assert "chatgpt.tokens.use.direct" in tokens.scopes and tokens.expires_in == 3600


def test_code_exchange_refuses_the_registration_entry_point_as_client_id():
    fake = FakeOpenAI()
    with pytest.raises(OAuthError) as info:
        OAuthClient(transport=fake.transport).exchange_code(client_id="dynamic_agent_client", code="c", code_verifier="v", redirect_uri=REDIRECT)
    assert info.value.code == "invalid_client" and fake.requests == []


def test_code_exchange_invalid_grant_is_reported_by_code():
    fake = FakeOpenAI()
    fake.queue_token({"error": "invalid_grant", "error_description": "code expired"}, status=400)
    with pytest.raises(OAuthError) as info:
        OAuthClient(transport=fake.transport).exchange_code(client_id=ISSUED_CLIENT, code="c", code_verifier="v", redirect_uri=REDIRECT)
    assert info.value.code == "invalid_grant" and info.value.status == 400


def test_refresh_form_body_omits_scope():
    fake = FakeOpenAI()
    client = OAuthClient(transport=fake.transport)
    tokens = client.refresh(client_id=ISSUED_CLIENT, refresh_token="refresh-0")
    assert fake.form(fake.sent(oauth.TOKEN_URL)[0]) == {
        "grant_type": "refresh_token", "client_id": ISSUED_CLIENT, "refresh_token": "refresh-0", "resource": "https://api.openai.com/v1",
    }
    assert tokens.refresh_token == "refresh-1"


def test_revocation_request():
    fake = FakeOpenAI()
    OAuthClient(transport=fake.transport).revoke(client_id=ISSUED_CLIENT, refresh_token="refresh-0")
    sent = [r for r in fake.requests if "revoke" in str(r.url)][0]
    assert fake.form(sent) == {"token": "refresh-0", "token_type_hint": "refresh_token", "client_id": ISSUED_CLIENT}


# -- ID token ------------------------------------------------------------------------------------


@pytest.fixture
def fake_and_client():
    fake = FakeOpenAI()
    return fake, OAuthClient(transport=fake.transport)


def test_id_token_valid(fake_and_client):
    fake, client = fake_and_client
    claims = client.validate_id_token(fake.id_token(nonce="n1"), client_id=ISSUED_CLIENT, nonce="n1")
    assert claims["sub"] == "user-sub-1" and claims["email"] == "user@example.com"
    # the audience may be a list, as in OpenAI's documented example
    client.validate_id_token(fake.id_token(nonce="n1", aud=[ISSUED_CLIENT]), client_id=ISSUED_CLIENT, nonce="n1")


def test_id_token_bad_signature(fake_and_client):
    fake, client = fake_and_client
    forged = fake.id_token(nonce="n1", key=fake.other_key)
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(forged, client_id=ISSUED_CLIENT, nonce="n1")
    assert "signature" in info.value.message


def test_id_token_wrong_issuer(fake_and_client):
    fake, client = fake_and_client
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(fake.id_token(nonce="n1", iss="https://evil.example"), client_id=ISSUED_CLIENT, nonce="n1")
    assert "not issued by OpenAI" in info.value.message


def test_id_token_wrong_audience(fake_and_client):
    fake, client = fake_and_client
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(fake.id_token(nonce="n1", aud="oaiapp_other"), client_id=ISSUED_CLIENT, nonce="n1")
    assert "different client" in info.value.message


def test_id_token_wrong_nonce(fake_and_client):
    fake, client = fake_and_client
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(fake.id_token(nonce="other"), client_id=ISSUED_CLIENT, nonce="n1")
    assert "nonce" in info.value.message


def test_id_token_expired(fake_and_client):
    fake, client = fake_and_client
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(fake.id_token(nonce="n1", exp_delta=-3600), client_id=ISSUED_CLIENT, nonce="n1")
    assert "expired" in info.value.message


def test_id_token_unknown_key_and_unsigned_tokens_are_rejected(fake_and_client):
    import jwt

    fake, client = fake_and_client
    with pytest.raises(OAuthError):
        client.validate_id_token(fake.id_token(nonce="n1", kid="unknown-kid"), client_id=ISSUED_CLIENT, nonce="n1")
    unsigned = jwt.encode({"iss": oauth.ISSUER, "aud": ISSUED_CLIENT, "sub": "s", "nonce": "n1", "exp": 9999999999}, key="", algorithm="none")
    with pytest.raises(OAuthError) as info:
        client.validate_id_token(unsigned, client_id=ISSUED_CLIENT, nonce="n1")
    assert "algorithm" in info.value.message
    hs = jwt.encode({"iss": oauth.ISSUER, "aud": ISSUED_CLIENT, "sub": "s", "nonce": "n1", "exp": 9999999999}, key="x" * 40, algorithm="HS256")
    with pytest.raises(OAuthError):
        client.validate_id_token(hs, client_id=ISSUED_CLIENT, nonce="n1")


def test_discovery_with_wrong_issuer_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"issuer": "https://evil.example", "jwks_uri": "https://evil.example/jwks"})

    with pytest.raises(OAuthError):
        OAuthClient(transport=httpx.MockTransport(handler)).discovery()


def test_callback_server_binds_loopback_and_serves_only_the_callback_path():
    seen = {}

    def handler(params):
        seen.update(params)
        return True, "Signed in."

    server = oauth.CallbackServer(handler, preferred_port=0)
    server.start()
    try:
        assert server.redirect_uri.startswith("http://127.0.0.1:") and server.redirect_uri.endswith("/auth/callback")
        with httpx.Client(trust_env=False) as http:
            assert http.get(server.redirect_uri.replace("/auth/callback", "/callback")).status_code == 404
            ok = http.get(server.redirect_uri, params={"code": "c1", "state": "s1"})
        assert ok.status_code == 200 and "signed in" in ok.text.lower() and "c1" not in ok.text
        assert seen == {"code": "c1", "state": "s1"}
        assert ok.headers["cache-control"] == "no-store"
    finally:
        server.close()
