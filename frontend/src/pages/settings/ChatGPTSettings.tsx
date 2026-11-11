import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, ShieldOff, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { invalidateModels, keys, useChatGPTStatus } from "@/api/queries";
import type { ChatGPTAccount, ChatGPTLoginAttempt } from "@/api/types";
import { CHATGPT_USAGE_URL } from "@/components/ai/ModelControls";
import { Button, ErrorState, ProgressBar, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDateTime, timeAgo } from "@/lib/format";
import { ContinueWithChatGPT } from "@/components/ai/ChatGPTBits";
import { toast } from "@/state/stores";
import { Group, PageTitle } from "./GeneralSettings";

function LoginFlow({ attempt, onDone, onCancel }: { attempt: ChatGPTLoginAttempt; onDone: (a: ChatGPTLoginAttempt) => void; onCancel: () => void }) {
  const poll = useQuery({
    queryKey: ["chatgpt-login", attempt.attempt_id],
    queryFn: () => api.chatgptLoginStatus(attempt.attempt_id),
    refetchInterval: (q) => (q.state.data && q.state.data.status !== "pending" ? false : 1500),
  });
  const current = poll.data ?? attempt;
  useEffect(() => {
    if (current.status !== "pending") onDone(current);
  }, [current.status]); // eslint-disable-line react-hooks/exhaustive-deps
  if (current.status !== "pending") return null;
  return (
    <div className="mt-4 rounded-md border border-line-strong bg-surface p-4" role="status">
      <p className="text-base font-semibold text-fg">Finish signing in in your browser</p>
      <p className="mt-1 max-w-[64ch] text-sm leading-relaxed text-fg-3">
        A ChatGPT sign-in page was opened. Review the permissions there and approve. StarIndex is listening on this computer for the result; nothing is sent anywhere else.
      </p>
      <ProgressBar className="mt-3 max-w-[280px]" label="Waiting for sign-in" />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {current.auth_url && (
          <a href={current.auth_url} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay">
            Open the sign-in page again <ExternalLink className="size-3.5" />
          </a>
        )}
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function AccountRow({ account, onReauth, busy }: { account: ChatGPTAccount; onReauth: (enablePlan: boolean) => void; busy: boolean }) {
  const qc = useQueryClient();
  const done = (data: unknown) => {
    qc.setQueryData(keys.chatgpt, data);
    invalidateModels(qc);
  };
  const activate = useMutation({ mutationFn: () => api.chatgptActivate(account.id), onSuccess: done, onError: (e) => toast.error(errorMessage(e)) });
  const signOut = useMutation({
    mutationFn: () => api.chatgptSignOut(account.id),
    onSuccess: (d) => {
      done(d);
      toast.success("Signed out. The registration is kept so you can sign in again without re-registering.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api.chatgptDelete(account.id),
    onSuccess: (d) => {
      done(d);
      toast.success("Account removed from StarIndex.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <li className="border-b border-line py-3.5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-md font-semibold text-fg">
            {account.label}
            {account.is_active && <span className="label inline-flex items-center gap-1 !text-fg-2"><Check className="size-3" /> Active</span>}
          </p>
          <p className="mt-0.5 text-sm text-fg-3">
            {account.email ?? "No email shared"}
            <span className="mono ml-2 text-xs text-fg-4">client …{account.client_id_suffix}</span>
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-3">
            <span className={account.signed_in ? "text-fg-2" : ""}>{account.signed_in ? "Signed in" : "Signed out"}</span>
            <span className={account.plan_usage_enabled ? "text-fg-2" : ""}>{account.plan_usage_enabled ? "Plan usage enabled" : "Plan usage not granted"}</span>
            <span>saved {timeAgo(account.saved_at)}</span>
            {account.expires_at && account.signed_in && <span title={formatDateTime(account.expires_at)}>token renews {timeAgo(account.expires_at)}</span>}
          </p>
          {account.last_error && (
            <p className="mt-1.5 flex items-start gap-1.5 text-sm text-fg-2">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-danger" aria-hidden />
              {account.last_error}
            </p>
          )}
          {account.signed_in && !account.plan_usage_enabled && (
            <p className="mt-1.5 max-w-[64ch] text-sm leading-relaxed text-fg-3">
              You are signed in, but StarIndex was not given permission to use your ChatGPT plan, so it cannot run inference with this account. Enable it to grant that permission, or use a local model or your own API key instead.
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {account.signed_in && !account.plan_usage_enabled && (
            <Button size="sm" variant="primary" disabled={busy} onClick={() => onReauth(true)}>
              Enable ChatGPT plan usage
            </Button>
          )}
          {!account.signed_in && (
            <Button size="sm" variant="primary" disabled={busy} onClick={() => onReauth(false)}>
              Sign in again
            </Button>
          )}
          {account.signed_in && !account.is_active && account.plan_usage_enabled && (
            <Button size="sm" loading={activate.isPending} onClick={() => activate.mutate()}>
              Make active
            </Button>
          )}
          {account.signed_in && (
            <Button size="sm" variant="ghost" loading={signOut.isPending} onClick={() => signOut.mutate()}>
              Sign out
            </Button>
          )}
          <Button size="sm" variant="ghost" loading={remove.isPending} onClick={() => remove.mutate()}>
            Remove
          </Button>
        </div>
      </div>
    </li>
  );
}

export default function ChatGPTSettings() {
  const qc = useQueryClient();
  const status = useChatGPTStatus();
  const [attempt, setAttempt] = useState<ChatGPTLoginAttempt | null>(null);
  const [outcome, setOutcome] = useState<ChatGPTLoginAttempt | null>(null);
  const login = useMutation({
    mutationFn: (body: { account_id?: string | null; enable_plan_usage?: boolean }) => api.chatgptLogin(body),
    onSuccess: (a) => {
      setOutcome(null);
      if (a.status === "pending") {
        setAttempt(a);
        if (a.auth_url) window.open(a.auth_url, "_blank", "noopener,noreferrer");
      } else {
        setOutcome(a);
      }
    },
  });
  const s = status.data;
  const hasAccounts = (s?.accounts.length ?? 0) > 0;
  const active = s?.accounts.find((a) => a.is_active);

  return (
    <div>
      <PageTitle title="ChatGPT plan">
        Use the model access included in your ChatGPT subscription through OpenAI's official Sign in with ChatGPT flow for open-source, locally hosted apps. This is not general API access, and it never reads your ChatGPT conversations.
      </PageTitle>

      {status.isLoading ? (
        <Skeleton className="mt-6 h-48 w-full" />
      ) : status.isError || !s ? (
        <ErrorState className="mt-6" message={errorMessage(status.error)} onRetry={() => status.refetch()} />
      ) : (
        <>
          <section className="mt-6 rounded-lg border border-line-strong bg-surface p-5" aria-label="Use your ChatGPT plan">
            <h3 className="text-xl font-semibold text-fg">Use your ChatGPT plan</h3>
            <p className="mt-1.5 max-w-[66ch] text-base leading-relaxed text-fg-3">
              Complete eligible AI requests in this app with usage included in your ChatGPT plan or credits balance. StarIndex is free and has no plan of its own; usage is billed only against your ChatGPT plan.
            </p>

            {s.blocked_by_privacy ? (
              <div className="mt-4 flex items-start gap-2.5 text-sm text-fg-2">
                <ShieldOff className="mt-0.5 size-4 shrink-0 text-fg-3" aria-hidden />
                <p className="max-w-[64ch] leading-relaxed">
                  Cloud models are turned off, and a ChatGPT plan is a cloud model: retrieved repository passages would be sent to OpenAI. Allow cloud models in{" "}
                  <Link to="/settings/privacy" className="link">
                    Privacy
                  </Link>{" "}
                  to use it.
                </p>
              </div>
            ) : !s.available && s.reason ? (
              <div className="mt-4 flex items-start gap-2.5 text-sm text-fg-2" data-testid="chatgpt-unavailable">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                <div className="max-w-[66ch] leading-relaxed">
                  <p className="font-medium text-fg">ChatGPT plan usage isn't available right now</p>
                  <p className="mt-0.5 text-fg-3">{s.reason}</p>
                  <p className="mt-1 text-fg-3">Local providers and API keys keep working; nothing else is affected.</p>
                </div>
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <ContinueWithChatGPT
                loading={login.isPending}
                disabled={Boolean(attempt) || s.blocked_by_privacy}
                onClick={() => login.mutate({ account_id: null })}
                label={hasAccounts ? "Add another ChatGPT account" : "Continue with ChatGPT"}
              />
              <a href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer" className="link inline-flex items-center gap-1 text-sm">
                Manage usage <ExternalLink className="size-3.5" />
              </a>
            </div>
            {login.isError && (
              <p className="mt-3 flex items-start gap-2 text-sm text-fg-2" role="alert">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" /> {errorMessage(login.error)}
              </p>
            )}

            {attempt && (
              <LoginFlow
                attempt={attempt}
                onCancel={() => setAttempt(null)}
                onDone={(a) => {
                  setAttempt(null);
                  setOutcome(a);
                  qc.invalidateQueries({ queryKey: keys.chatgpt });
                  invalidateModels(qc);
                }}
              />
            )}

            {outcome && (
              <div className={cn("mt-4 flex items-start gap-2.5 text-sm")} role="status" data-testid="chatgpt-outcome">
                {outcome.status === "completed" && outcome.plan_usage_enabled !== false ? (
                  <Check className="mt-0.5 size-4 shrink-0 text-fg-2" />
                ) : (
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" />
                )}
                <div className="max-w-[66ch] leading-relaxed">
                  <p className="font-medium text-fg">
                    {outcome.status === "completed"
                      ? outcome.plan_usage_enabled === false
                        ? "Signed in, but plan usage was not granted"
                        : "Signed in with ChatGPT"
                      : outcome.status === "denied"
                        ? "Sign-in was declined"
                        : outcome.status === "expired"
                          ? "The sign-in attempt timed out"
                          : "Sign-in failed"}
                  </p>
                  <p className="mt-0.5 text-fg-3">
                    {outcome.message ||
                      (outcome.status === "denied"
                        ? "Nothing was changed. You can try again whenever you like, or keep using a local model or your own API key."
                        : outcome.status === "completed" && outcome.plan_usage_enabled === false
                          ? "The account is saved, but StarIndex cannot run inference with it until you enable ChatGPT plan usage below."
                          : "")}
                  </p>
                </div>
              </div>
            )}
          </section>

          {hasAccounts && (
            <Group title="Accounts" description="Each registration keeps its own credentials. Switching accounts never mixes one account's client with another's tokens.">
              <ul className="mt-1 border-t border-line">
                {s.accounts.map((a) => (
                  <AccountRow key={a.id} account={a} busy={Boolean(attempt) || login.isPending} onReauth={(enable) => login.mutate({ account_id: a.id, enable_plan_usage: enable })} />
                ))}
              </ul>
            </Group>
          )}

          {active?.plan_usage_enabled && (
            <Group title="Using it">
              <p className="mt-2 max-w-[70ch] text-sm leading-relaxed text-fg-3">
                Select <span className="font-medium text-fg-2">ChatGPT plan</span> as the default model or for individual tasks in{" "}
                <Link to="/settings/models" className="link">
                  Model providers → Routing
                </Link>
                . Only models available to your account are listed. When a usage limit is reached, StarIndex stops and tells you; it does not move the request to a paid API.
              </p>
            </Group>
          )}

          <Group title="How this works">
            <dl className="mt-2 grid max-w-[820px] grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
              <dt className="text-fg-3">Sign-in</dt>
              <dd className="text-fg-2">OAuth with PKCE in your system browser, returning to a listener on 127.0.0.1. No password or cookie ever passes through StarIndex.</dd>
              <dt className="text-fg-3">Permission</dt>
              <dd className="text-fg-2">Identity scopes plus explicit consent to use your plan. Without that consent StarIndex makes no inference requests.</dd>
              <dt className="text-fg-3">Requests</dt>
              <dd className="text-fg-2">Streamed through OpenAI's public Responses API with response storage turned off.</dd>
              <dt className="text-fg-3">Credentials</dt>
              <dd className="text-fg-2">Kept in protected local storage on this computer. Disconnect any time here or in ChatGPT settings.</dd>
              <dt className="text-fg-3">Limits</dt>
              <dd className="text-fg-2">Your plan's usage limits are shared with every app where you use it. This is not unlimited API access.</dd>
              <dt className="text-fg-3">This device</dt>
              <dd className="text-fg-2">{s.host_id_set ? "Has a stable, anonymous host identifier for this installation." : "A host identifier is created before the first sign-in."}</dd>
            </dl>
          </Group>
        </>
      )}
    </div>
  );
}
