# ChatGPT plan (Sign in with ChatGPT)

StarIndex can run Ask, Research and Compare on your ChatGPT plan instead of a paid API key. It uses
OpenAI's documented flow for open-source, locally hosted apps and nothing else: no ChatGPT web endpoints,
no browser cookies, no other application's client id.

Official documentation this implementation follows:

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [UI/UX guidelines](https://developers.openai.com/siwc/ui-ux-guidelines)

## Status

**Implemented and covered by automated tests against mocked OpenAI endpoints. Not yet exercised against
OpenAI's live service**: the build environment could not open a browser for interactive sign-in. The first
real sign-in on your machine is the live verification; the steps are at the end of this page.

## Distribution condition

OpenAI documents this flow for **open-source and locally hosted apps**. StarIndex qualifies as shipped: MIT
licensed, running on your own machine, bound to loopback. A paid or remotely hosted derivative is a
different case that OpenAI handles through an interest form, and it must not assume the same authorization.

A ChatGPT subscription is not general API access. Requests draw on your plan's usage limits (for Plus, the
shared five-hour limit) and on any per-app limit you set in ChatGPT settings.

## What happens when you sign in

1. **Host identifier.** Before the first sign-in StarIndex creates this installation's `ext_agent_host_id`
   once and keeps it: an RFC 9278 JWK-thumbprint URI (`urn:ietf:params:oauth:jwk-thumbprint:sha-256:…`)
   derived from a locally generated P-256 key. It is opaque and is not a credential.
2. **Authorization request.** StarIndex starts a listener on `http://127.0.0.1:<port>/auth/callback`
   (port 1455 when free, otherwise another free port; never `localhost`) and gives the app the URL to open
   in your browser: `https://auth.openai.com/api/accounts/authorize` with `response_type=code`, the scopes
   `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`,
   `resource=https://api.openai.com/v1`, and a fresh `state`, `nonce` and PKCE S256 challenge.
   - First time: `client_id=dynamic_agent_client` and `agent_name_hint=StarIndex`.
   - Later sign-ins to a saved account: that account's issued client id, plus `id_token_hint` and
     `login_hint`; no `agent_name_hint`.
3. **Callback.** `state` must match. `error=access_denied` stops the attempt without exchanging anything.
   A new registration must return an issued `client_id`; a re-authorization that returns a *different*
   client id is rejected. Only the first valid callback is exchanged.
4. **Code exchange.** Form-encoded `authorization_code` grant to
   `https://auth.openai.com/api/accounts/oauth/token` with the issued client id, the code, the PKCE
   verifier, the same redirect URI and the same `resource`. No client secret exists in this flow.
5. **Identity.** The ID token's signature is verified against OpenAI's JWKS (from the OpenID discovery
   document), along with issuer, audience (the issued client id), expiry and nonce. Unsigned and
   HMAC-signed tokens are refused. When re-authorizing, the verified subject must equal the saved
   account's, otherwise its credentials are left untouched.
6. **Permission.** Plan usage is enabled only if the **token response's** `scope` contains
   `chatgpt.tokens.use.direct`. If it does not, you stay signed in, plan usage is marked off, and StarIndex
   refuses to send inference with that account. "Enable ChatGPT plan usage" in Settings repeats the
   authorization with the full scope set and `prompt=consent`.
7. **Storage.** Access, refresh and ID tokens go to the OS keyring (Windows Credential Manager) or, where
   no keyring exists, an owner-only file. SQLite holds only the label, email, subject, issued client id,
   granted scopes and expiry. Tokens are never returned by the HTTP API, logged, or placed in URLs; an
   authorization URL carrying `id_token_hint` is redacted before logging.

Each registration (issued client id + verified identity) is separate, even if two share an email address.
You can keep several, switch the active one, sign out of one, or remove one.

## Inference

- **Models:** `GET https://api.openai.com/v1/models` with the access token; StarIndex shows entries of the
  `models` array whose `visibility` is `list`, in the server's order, and sends the chosen `slug`.
- **Requests:** `POST https://api.openai.com/v1/responses` with `store: false`, `stream: true`, `input` as
  an array of messages and the system prompt in `instructions`. The body contains only
  `model`, `input`, `store`, `stream` and (when there is a system prompt) `instructions`.
- **Success** means `response.completed` arrived. `response.failed`, `response.incomplete`, an `error`
  event and a stream that simply ends are each reported as failures, including when text had already
  started streaming.
- The access token is only ever sent to `api.openai.com`; the endpoint is not configurable.

### Preview limitations that shape StarIndex's behaviour

- No `temperature`, `max_output_tokens`, `metadata`, `truncation`, `previous_response_id` or the other
  fields the preview lists as unsupported: StarIndex never sends them, so answer length is steered by the
  prompt only.
- No tools are sent. Research's tool selection uses prompt-instructed JSON on this provider.
- No embeddings: semantic search always uses the local embedding model.
- Each request carries its own context; nothing is stored server-side.

## Errors and what StarIndex does

| Situation | StarIndex behaviour |
|---|---|
| Consent declined (`access_denied`) | Attempt ends as "denied"; nothing stored; other providers unaffected |
| Signed in without `chatgpt.tokens.use.direct` | Sign-in kept, plan usage off, inference refused with an explanation |
| `subscription_sharing_user_not_eligible` (403) | "Not available for this account/workspace"; no retry, no OAuth loop |
| `subscription_sharing_usage_limit_exceeded` (429, or mid-stream) | Request stops; **Manage usage** links to <https://chatgpt.com/settings/usage>; no reset time is guessed |
| `subscription_sharing_usage_unavailable`, `subscription_sharing_user_unavailable` (503) | Reported as temporary and retryable; credentials kept |
| `subscription_sharing_unsupported_capability` (400) | Reported with the offending `param`; not retried |
| `subscription_sharing_route_not_supported` (403) | Reported as unsupported |
| `subscription_sharing_invalid_user` (401) | Reported as an authentication problem with the request id |
| `chatpass_v2_scope_not_authorized`, `chatpass_v2_invalid_authorization_context` (403) | Reported as missing permission; points to re-enabling plan usage |
| Pre-stream `{"detail": …}` with 401 / 403 / 503 | Mapped by status (identity or permission / policy or region / routing unavailable); the detail text is shown as diagnostics, never parsed as a code |
| Refresh: `invalid_grant`, `invalid_refresh_token`, `token_expired`, `refresh_token_expired`, `refresh_token_invalidated`, `refresh_token_reused` | Tokens cleared, registration kept, "sign in again" |
| Refresh: `invalid_client` | Reported as a client-registration problem; tokens kept |
| Network failure or 5xx during refresh | Credentials kept; retry later |

After any of these, StarIndex **does not** switch to another provider. The error comes back with the
providers you may choose explicitly.

Token renewal: access tokens last an hour. StarIndex refreshes within five minutes of expiry (respecting
`earliest_refresh_at`), omits `scope` so the grant is retained, stores the rotated refresh token together
with the new access token, and serializes refreshes with an in-process lock plus a lock file so the API
and a separate worker cannot both spend the same refresh token.

Sign-out posts the refresh token to the discovery document's `revocation_endpoint`, retries briefly on
network errors or 5xx, then clears the tokens locally. If revocation could not be confirmed, the account
says so and points to disconnecting the app in ChatGPT settings.

## Privacy switches

ChatGPT plan is a cloud provider. It cannot be used while **Allow cloud models** is off, and each request
needs confirmation unless you disabled that. Offline mode blocks sign-in as well. The API reports the
active provider with every answer and exposes the ChatGPT usage page (`usage_url`) so the interface can
show "Using ChatGPT plan" with a **Manage usage** link, as OpenAI's UI guidelines ask.

## Verifying on your machine

1. Start StarIndex, open the model provider settings, and enable **Allow cloud models** under Privacy.
2. Choose **Continue with ChatGPT**. Your browser opens `auth.openai.com`; sign in, name the agent and
   approve the permissions.
3. The browser returns to `http://127.0.0.1:1455/auth/callback` and shows "You're signed in". Back in
   StarIndex the account appears with **plan usage enabled**.
4. Open the model picker for ChatGPT plan: it lists the models your account can use.
5. Use **Test** with one of those models, then ask a question in Ask AI with ChatGPT plan selected.
6. Optional checks: decline consent once (the account should show plan usage off and offer to enable it);
   sign out and sign in again (no new registration, same client); disconnect StarIndex in ChatGPT settings
   and send a request (StarIndex should ask you to sign in again).

If step 2 or 3 fails, the attempt's message in Settings states the reason (for example an eligibility
restriction on the account or workspace). Local providers keep working either way.

## Not implemented

- `force_reconsent=true`: the docs say to use it only after OpenAI confirms deployment for an integration;
  StarIndex uses the documented `prompt=consent` instead.
- Codex app-server and self-hosted VM credential transfer: not needed for a desktop installation.
- WebSocket continuation, hosted tools, image/file inputs: not used.
