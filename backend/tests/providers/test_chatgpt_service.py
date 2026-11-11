"""Account lifecycle: sign-in, permission checks, refresh, sign-out. Fully mocked."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from starindex.providers.base import ProviderError
from starindex.providers.chatgpt_plan import oauth
from starindex.providers.chatgpt_plan.service import ChatGPTPlanService
from starindex.secrets import chatgpt_account_key

from .chatgpt_fakes import ALL_SCOPES, ISSUED_CLIENT, NO_PLAN_SCOPES, FakeOpenAI, query


@pytest.fixture
def fake() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def service(ctx, fake):
    svc = ChatGPTPlanService(ctx.db, ctx.secrets, ctx.settings_store, ctx.config.paths.data_dir, transport=fake.transport, callback_port=0)
    svc._revoke_backoff = (0.0, 0.0)
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": True}})
    yield svc
    svc.close()


def sign_in(service, fake, *, scope=ALL_SCOPES, account_id=None, sub="user-sub-1", email="user@example.com",
            client_id=ISSUED_CLIENT, enable_plan_usage=False, expires_in=3600):
    """Drive a full attempt through the service's own callback handler."""
    view = service.start_login(account_id=account_id, enable_plan_usage=enable_plan_usage)
    attempt = service._attempts[view.attempt_id]
    fake.queue_token(fake.token_body(nonce=attempt.nonce, scope=scope, aud=client_id, sub=sub, email=email, expires_in=expires_in))
    params = {"code": "auth-code", "state": attempt.state}
    if account_id is None:
        params["client_id"] = client_id
    ok, message = service._on_callback(attempt, params)
    return view, attempt, ok, message


def record(ctx, account_id):
    return json.loads(ctx.secrets.get(chatgpt_account_key(account_id)))


def test_host_id_is_created_once_and_persisted(service, ctx):
    assert not service.host_id_set()
    first = service.host_id()
    assert oauth.is_valid_host_id(first) and service.host_id() == first and service.host_id_set()
    again = ChatGPTPlanService(ctx.db, ctx.secrets, ctx.settings_store, ctx.config.paths.data_dir)
    assert again.host_id() == first
    again.close()


def test_start_login_builds_a_first_time_registration_url(service):
    view = service.start_login()
    q = query(view.auth_url)
    assert view.status == "pending" and q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "StarIndex"
    assert q["ext_agent_host_id"] == service.host_id()
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
    assert service.login_status(view.attempt_id).status == "pending"


def test_full_sign_in_through_the_loopback_listener(service, fake, ctx):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    fake.queue_token(fake.token_body(nonce=attempt.nonce))
    with httpx.Client(trust_env=False) as http:
        page = http.get(attempt.redirect_uri, params={"code": "auth-code", "state": attempt.state, "client_id": ISSUED_CLIENT, "scope": ALL_SCOPES})
    assert page.status_code == 200 and "access-0" not in page.text
    done = service.login_status(view.attempt_id)
    assert done.status == "completed" and done.plan_usage_enabled is True and done.auth_url is None
    # the exchange used the issued client id, the verifier and the exact redirect URI
    form = fake.form(fake.sent(oauth.TOKEN_URL)[0])
    assert form["client_id"] == ISSUED_CLIENT and form["code_verifier"] == attempt.code_verifier
    assert form["redirect_uri"] == attempt.redirect_uri and form["code"] == "auth-code"

    status = service.status()
    assert status.available and status.active_account_id == done.account_id
    account = status.accounts[0]
    assert account.email == "user@example.com" and account.plan_usage_enabled and account.signed_in and account.is_active
    assert account.client_id_suffix == ISSUED_CLIENT[-6:] and account.welcome_pending
    stored = record(ctx, done.account_id)
    assert stored["client_id"] == ISSUED_CLIENT and stored["client_id"] != "dynamic_agent_client"
    assert stored["subject"] == "user-sub-1" and stored["issuer"] == oauth.ISSUER
    assert stored["refresh_token"] == "refresh-0" and stored["id_token"]
    assert "chatgpt.tokens.use.direct" in stored["scopes"] and stored["ext_agent_host_id"] == service.host_id()
    # tokens are in the secret store only, never in SQLite
    row = dict(ctx.db.one("SELECT * FROM chatgpt_accounts"))
    assert "access-0" not in json.dumps(row) and "refresh-0" not in json.dumps(row)
    assert service.access_token() == "access-0"
    service.mark_welcome_shown(done.account_id)
    assert not service.status().accounts[0].welcome_pending


def test_stray_request_with_wrong_state_does_not_cancel_the_attempt(service, fake):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    ok, _ = service._on_callback(attempt, {"code": "x", "state": "forged", "client_id": ISSUED_CLIENT})
    assert not ok and service.login_status(view.attempt_id).status == "pending"
    assert fake.sent(oauth.TOKEN_URL) == []


def test_access_denied_stops_without_exchange(service, fake):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    ok, _ = service._on_callback(attempt, {"error": "access_denied", "state": attempt.state})
    status = service.login_status(view.attempt_id)
    assert not ok and status.status == "denied" and "plan usage stays off" in status.message
    assert fake.sent(oauth.TOKEN_URL) == [] and service.status().accounts == []


def test_new_registration_without_issued_client_id_is_incomplete(service, fake):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    ok, _ = service._on_callback(attempt, {"code": "c", "state": attempt.state})
    assert not ok and service.login_status(view.attempt_id).status == "failed"
    assert fake.sent(oauth.TOKEN_URL) == []


def test_invalid_grant_fails_and_retains_issued_client_id_for_the_next_attempt(service, fake):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    fake.queue_token({"error": "invalid_grant"}, status=400)
    ok, _ = service._on_callback(attempt, {"code": "c", "state": attempt.state, "client_id": ISSUED_CLIENT})
    assert not ok and service.login_status(view.attempt_id).status == "failed"
    retry = query(service.start_login().auth_url)
    assert retry["client_id"] == ISSUED_CLIENT and "agent_name_hint" not in retry


@pytest.mark.parametrize("bad", ["signature", "issuer", "audience", "nonce", "expired"])
def test_invalid_id_token_never_stores_credentials(service, fake, bad):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    kw = {
        "signature": {"key": fake.other_key}, "issuer": {"iss": "https://evil.example"}, "audience": {"aud": "oaiapp_other"},
        "nonce": {}, "expired": {"exp_delta": -3600},
    }[bad]
    nonce = "wrong-nonce" if bad == "nonce" else attempt.nonce
    fake.queue_token(fake.token_body(nonce=nonce, **kw))
    ok, _ = service._on_callback(attempt, {"code": "c", "state": attempt.state, "client_id": ISSUED_CLIENT})
    status = service.login_status(view.attempt_id)
    assert not ok and status.status == "failed" and "Identity verification failed" in status.message
    assert service.status().accounts == []


def test_missing_plan_scope_keeps_sign_in_but_disables_plan_usage_and_refuses_inference(service, fake):
    view, _, ok, message = sign_in(service, fake, scope=NO_PLAN_SCOPES)
    assert ok and "not granted" in message
    done = service.login_status(view.attempt_id)
    assert done.status == "completed" and done.plan_usage_enabled is False
    status = service.status()
    account = status.accounts[0]
    assert account.signed_in and not account.plan_usage_enabled and not status.available
    assert "permission to use your ChatGPT plan was not granted" in status.reason
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind == "consent_required"


def test_granted_scopes_come_from_the_token_response_not_the_callback(service, fake):
    view = service.start_login()
    attempt = service._attempts[view.attempt_id]
    fake.queue_token(fake.token_body(nonce=attempt.nonce, scope=NO_PLAN_SCOPES))
    service._on_callback(attempt, {"code": "c", "state": attempt.state, "client_id": ISSUED_CLIENT, "scope": ALL_SCOPES})
    assert service.login_status(view.attempt_id).plan_usage_enabled is False


def test_enabling_plan_usage_later_reauthorises_with_consent_prompt(service, fake):
    first, *_ = sign_in(service, fake, scope=NO_PLAN_SCOPES)
    account_id = service.login_status(first.attempt_id).account_id
    view, attempt, ok, _ = sign_in(service, fake, account_id=account_id, enable_plan_usage=True)
    q = query(view.auth_url)
    assert q["client_id"] == ISSUED_CLIENT and q["prompt"] == "consent" and "agent_name_hint" not in q
    assert q["id_token_hint"] and q["login_hint"] == "user@example.com"
    assert "chatgpt.tokens.use.direct" in q["scope"].split()
    assert ok and service.status().available and len(service.status().accounts) == 1


def test_ordinary_reauthorisation_does_not_force_consent(service, fake):
    first, *_ = sign_in(service, fake)
    account_id = service.login_status(first.attempt_id).account_id
    view = service.start_login(account_id=account_id)
    assert "prompt" not in query(view.auth_url)


def test_reauth_as_a_different_identity_does_not_replace_credentials(service, fake, ctx):
    first, *_ = sign_in(service, fake)
    account_id = service.login_status(first.attempt_id).account_id
    before = record(ctx, account_id)
    fake.refresh_counter = 5
    view, _, ok, _ = sign_in(service, fake, account_id=account_id, sub="someone-else", email="other@example.com")
    assert not ok and "different ChatGPT account" in service.login_status(view.attempt_id).message
    assert record(ctx, account_id) == before


def test_reauth_callback_with_a_different_client_id_is_rejected(service, fake):
    first, *_ = sign_in(service, fake)
    account_id = service.login_status(first.attempt_id).account_id
    view = service.start_login(account_id=account_id)
    attempt = service._attempts[view.attempt_id]
    ok, _ = service._on_callback(attempt, {"code": "c", "state": attempt.state, "client_id": "oaiapp_other"})
    assert not ok and service.login_status(view.attempt_id).status == "failed"
    assert len(fake.sent(oauth.TOKEN_URL)) == 1  # only the original sign-in exchanged a code


def test_multiple_registrations_are_kept_separate_even_with_the_same_email(service, fake, ctx):
    a, *_ = sign_in(service, fake, client_id="oaiapp_workspace_a", sub="sub-a")
    fake.refresh_counter = 1
    b, *_ = sign_in(service, fake, client_id="oaiapp_workspace_b", sub="sub-b")
    id_a, id_b = service.login_status(a.attempt_id).account_id, service.login_status(b.attempt_id).account_id
    accounts = {acc.id: acc for acc in service.status().accounts}
    assert id_a != id_b and len(accounts) == 2
    assert accounts[id_a].label != accounts[id_b].label
    assert accounts[id_b].is_active and not accounts[id_a].is_active
    assert record(ctx, id_a)["client_id"] == "oaiapp_workspace_a" and record(ctx, id_b)["client_id"] == "oaiapp_workspace_b"
    assert record(ctx, id_a)["access_token"] != record(ctx, id_b)["access_token"]
    service.activate(id_a)
    assert service.status().active_account_id == id_a and service.access_token() == record(ctx, id_a)["access_token"]


def test_attempts_expire(service, monkeypatch):
    view = service.start_login()
    service._attempts[view.attempt_id].created_at -= 10_000
    expired = service.login_status(view.attempt_id)
    assert expired.status == "expired" and expired.auth_url is None
    assert service.login_status("does-not-exist").status == "expired"


def test_offline_mode_blocks_sign_in(service, ctx):
    ctx.settings_store.patch({"privacy": {"offline_mode": True}})
    with pytest.raises(ProviderError) as info:
        service.start_login()
    assert info.value.kind == "cloud_disabled"


# -- refresh -------------------------------------------------------------------------------------


def test_refresh_near_expiry_rotates_all_credentials_together(service, fake, ctx):
    view, *_ = sign_in(service, fake, expires_in=60)   # inside the refresh margin immediately
    account_id = service.login_status(view.attempt_id).account_id
    id_token = record(ctx, account_id)["id_token"]
    assert service.access_token() == "access-1"
    form = fake.form(fake.sent(oauth.TOKEN_URL)[-1])
    assert form == {"grant_type": "refresh_token", "client_id": ISSUED_CLIENT, "refresh_token": "refresh-0", "resource": "https://api.openai.com/v1"}
    stored = record(ctx, account_id)
    assert stored["access_token"] == "access-1" and stored["refresh_token"] == "refresh-1"
    assert stored["id_token"] == id_token  # retained for id_token_hint
    # a fresh token is not refreshed again
    assert service.access_token() == "access-1" and len(fake.sent(oauth.TOKEN_URL)) == 2


def test_fresh_token_is_used_without_contacting_the_token_endpoint(service, fake):
    sign_in(service, fake)
    assert service.access_token() == "access-0" and len(fake.sent(oauth.TOKEN_URL)) == 1


def test_earliest_refresh_at_is_respected_until_the_token_actually_expires(service, fake, ctx):
    view, *_ = sign_in(service, fake, expires_in=200)
    account_id = service.login_status(view.attempt_id).account_id
    stored = record(ctx, account_id)
    stored["earliest_refresh_at"] = time.time() + 150
    ctx.secrets.set(chatgpt_account_key(account_id), json.dumps(stored))
    assert service.access_token() == "access-0"          # too early to refresh, token still valid
    stored["saved_at_epoch"] -= 400                       # now actually expired
    ctx.secrets.set(chatgpt_account_key(account_id), json.dumps(stored))
    assert service.access_token() == "access-1"


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"],
)
def test_terminal_refresh_errors_clear_tokens_and_require_sign_in(service, fake, ctx, code):
    view, *_ = sign_in(service, fake, expires_in=60)
    account_id = service.login_status(view.attempt_id).account_id
    body = {"error": code} if code == "invalid_grant" else {"error": {"code": code, "message": "nope"}}
    fake.queue_token(body, status=400)
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind == "auth" and info.value.code == code
    stored = record(ctx, account_id)
    assert "access_token" not in stored and "refresh_token" not in stored
    assert stored["client_id"] == ISSUED_CLIENT and stored["id_token"]   # mapping and hint survive for re-auth
    account = service.status().accounts[0]
    assert not account.signed_in and code in (account.last_error or "")
    # the next sign-in reuses the saved client id rather than registering again
    assert query(service.start_login(account_id=account_id).auth_url)["client_id"] == ISSUED_CLIENT


def test_invalid_client_keeps_tokens_and_reports_configuration_problem(service, fake, ctx):
    view, *_ = sign_in(service, fake, expires_in=60)
    account_id = service.login_status(view.attempt_id).account_id
    fake.queue_token({"error": "invalid_client"}, status=401)
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind == "auth" and info.value.code == "invalid_client"
    assert record(ctx, account_id)["refresh_token"] == "refresh-0"
    assert service.status().accounts[0].signed_in


def test_temporary_refresh_failures_keep_credentials(service, fake, ctx):
    view, *_ = sign_in(service, fake, expires_in=60)
    account_id = service.login_status(view.attempt_id).account_id
    fake.queue_token(httpx.Response(503, text="upstream down"))
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind == "unavailable" and info.value.retryable
    assert record(ctx, account_id)["refresh_token"] == "refresh-0" and service.status().accounts[0].signed_in
    assert service.access_token() == "access-1"  # recovers on the next attempt


def test_concurrent_refreshes_are_serialised_into_one_request(service, fake):
    sign_in(service, fake, expires_in=60)
    fake.token_delay_s = 0.2
    results: list[str] = []
    errors: list[Exception] = []

    def worker():
        try:
            results.append(service.access_token())
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors and results == ["access-1"] * 6
    refreshes = [r for r in fake.sent(oauth.TOKEN_URL) if fake.form(r).get("grant_type") == "refresh_token"]
    assert len(refreshes) == 1   # the rotating refresh token was used exactly once


def test_refresh_that_drops_the_plan_scope_disables_plan_usage(service, fake):
    sign_in(service, fake, expires_in=60)
    fake.refresh_counter = 1
    fake.queue_token(fake.token_body(scope=NO_PLAN_SCOPES))
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind == "consent_required" and not service.status().accounts[0].plan_usage_enabled


# -- sign-out ------------------------------------------------------------------------------------


def test_sign_out_revokes_then_clears_tokens_but_keeps_the_registration(service, fake, ctx):
    view, *_ = sign_in(service, fake)
    account_id = service.login_status(view.attempt_id).account_id
    status = service.sign_out(account_id)
    revoke = [r for r in fake.requests if "revoke" in str(r.url)][0]
    assert fake.form(revoke) == {"token": "refresh-0", "token_type_hint": "refresh_token", "client_id": ISSUED_CLIENT}
    stored = record(ctx, account_id)
    assert not {"access_token", "refresh_token", "id_token"} & set(stored)
    assert stored["client_id"] == ISSUED_CLIENT
    account = status.accounts[0]
    assert not account.signed_in and not account.is_active and account.last_error is None and not status.available
    with pytest.raises(ProviderError) as info:
        service.access_token()
    assert info.value.kind in ("not_configured", "auth")
    # signing in again reuses the client id; no id_token_hint after sign-out
    q = query(service.start_login(account_id=account_id).auth_url)
    assert q["client_id"] == ISSUED_CLIENT and "id_token_hint" not in q


def test_sign_out_with_unconfirmed_revocation_still_clears_locally_and_says_so(service, fake, ctx):
    view, *_ = sign_in(service, fake)
    account_id = service.login_status(view.attempt_id).account_id
    fake.revoke_responses = [httpx.Response(503), httpx.ConnectError("down"), httpx.Response(500)]
    status = service.sign_out(account_id)
    assert len([r for r in fake.requests if "revoke" in str(r.url)]) == 3   # bounded retries
    assert "refresh_token" not in record(ctx, account_id)
    assert "remote revocation was not confirmed" in status.accounts[0].last_error


def test_delete_removes_account_and_secret(service, fake, ctx):
    view, *_ = sign_in(service, fake)
    account_id = service.login_status(view.attempt_id).account_id
    status = service.delete(account_id)
    assert status.accounts == [] and ctx.secrets.get(chatgpt_account_key(account_id)) is None
    assert any("revoke" in str(r.url) for r in fake.requests)


def test_status_explains_privacy_block(service, fake, ctx):
    sign_in(service, fake)
    ctx.settings_store.patch({"privacy": {"allow_cloud_models": False}})
    status = service.status()
    assert not status.available and status.blocked_by_privacy and "Cloud model traffic is turned off" in status.reason


def test_status_when_nothing_is_registered(service):
    status = service.status()
    assert not status.available and status.accounts == [] and "Not signed in" in status.reason
    assert status.usage_url == "https://chatgpt.com/settings/usage"


def test_new_attempt_supersedes_a_pending_one_and_codes_are_single_use(service, fake):
    first = service.start_login()
    second = service.start_login()
    assert service.login_status(first.attempt_id).status == "expired"
    assert service.login_status(second.attempt_id).status == "pending"
    attempt = service._attempts[second.attempt_id]
    fake.queue_token(fake.token_body(nonce=attempt.nonce))
    params = {"code": "c", "state": attempt.state, "client_id": ISSUED_CLIENT}
    assert service._on_callback(attempt, params)[0] is True
    assert service._on_callback(attempt, params)[0] is False          # replayed callback is ignored
    assert len(fake.sent(oauth.TOKEN_URL)) == 1
    stale = service._attempts[first.attempt_id]
    assert service._on_callback(stale, {"code": "c", "state": stale.state, "client_id": ISSUED_CLIENT})[0] is False
