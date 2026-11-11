import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Terminal, TriangleAlert, User } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { keys } from "@/api/queries";
import type { GitHubAuthStatus } from "@/api/types";
import { Button, Field, Input, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { toast } from "@/state/stores";

type Method = "gh_cli" | "token" | "public";

/**
 * Connect GitHub with read-only access. Three supported paths:
 * the GitHub CLI's existing login, a pasted token (stored in the OS keyring, never
 * shown again), or public stars by username with no credentials at all.
 */
export function GitHubConnect({ onConnected, compact }: { onConnected?: (status: GitHubAuthStatus) => void; compact?: boolean }) {
  const qc = useQueryClient();
  const auth = useQuery({ queryKey: ["github-auth"], queryFn: api.githubAuth });
  const [method, setMethod] = useState<Method>("gh_cli");
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const a = auth.data;

  useEffect(() => {
    if (!a) return;
    if (a.method !== "none") setMethod(a.method as Method);
    else setMethod(a.gh_cli_authenticated ? "gh_cli" : "token");
    if (a.username) setUsername(a.username);
  }, [a?.method, a?.gh_cli_authenticated, a?.username]); // eslint-disable-line react-hooks/exhaustive-deps

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["github-auth"] });
    qc.invalidateQueries({ queryKey: keys.status });
    qc.invalidateQueries({ queryKey: keys.sync });
  };
  const save = useMutation({
    mutationFn: () =>
      api.setGithubAuth({
        method,
        token: method === "token" && token ? token : null,
        username: method === "public" ? username.trim() : null,
      }),
    onSuccess: (status) => {
      setToken("");
      qc.setQueryData(["github-auth"], status);
      invalidate();
      if (status.ok) {
        toast.success(`Connected to GitHub${status.login ? ` as ${status.login}` : ""}.`);
        onConnected?.(status);
      }
    },
  });
  const forget = useMutation({
    mutationFn: api.deleteGithubToken,
    onSuccess: (status) => {
      qc.setQueryData(["github-auth"], status);
      invalidate();
      toast.success("Stored token removed.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (auth.isLoading) return <Skeleton className="h-40 w-full" />;

  const options: { value: Method; icon: React.ReactNode; title: string; detail: string; badge?: string }[] = [
    {
      value: "gh_cli",
      icon: <Terminal className="size-4" />,
      title: "GitHub CLI",
      detail: a?.gh_cli_available
        ? a.gh_cli_authenticated
          ? "Uses the login from `gh auth login`. No token is copied or stored by StarIndex."
          : "The gh command is installed but not signed in. Run `gh auth login` in a terminal first."
        : "The gh command was not found. Install GitHub CLI, or choose another option.",
      badge: a?.gh_cli_authenticated ? "Detected" : a?.gh_cli_available ? "Not signed in" : "Not installed",
    },
    {
      value: "token",
      icon: <KeyRound className="size-4" />,
      title: "Personal access token",
      detail: "A fine-grained or classic token with read-only access. Includes private stars. Stored in your operating system's keyring.",
      badge: a?.token_stored ? "Token stored" : undefined,
    },
    {
      value: "public",
      icon: <User className="size-4" />,
      title: "Public stars by username",
      detail: "No credentials. Reads the public starred list of one account; lower rate limit (60 requests per hour) and no private repositories.",
    },
  ];

  const disabled =
    (method === "gh_cli" && !a?.gh_cli_authenticated) ||
    (method === "token" && !token && !a?.token_stored) ||
    (method === "public" && !username.trim());

  return (
    <div className="flex flex-col gap-4">
      <div role="radiogroup" aria-label="GitHub access method" className="flex flex-col">
        {options.map((o) => {
          const active = method === o.value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setMethod(o.value)}
              className={cn(
                "grid cursor-pointer grid-cols-[auto_auto_1fr_auto] items-start gap-x-3 border-b border-line px-1 text-left transition-colors first:border-t",
                compact ? "py-2.5" : "py-3.5",
                active ? "bg-surface" : "hover:bg-surface",
              )}
            >
              <span className={cn("mt-1 flex size-[15px] items-center justify-center rounded-full border", active ? "border-accent-solid" : "border-fg-4")}>
                {active && <span className="size-[7px] rounded-full bg-accent-solid" />}
              </span>
              <span className={cn("mt-0.5", active ? "text-fg" : "text-fg-3")}>{o.icon}</span>
              <span className="min-w-0">
                <span className={cn("block text-base font-medium", active ? "text-fg" : "text-fg-2")}>{o.title}</span>
                <span className="mt-0.5 block text-sm leading-relaxed text-fg-3">{o.detail}</span>
              </span>
              {o.badge && <span className="label mt-1 shrink-0">{o.badge}</span>}
            </button>
          );
        })}
      </div>

      {method === "token" && (
        <Field
          label="Token"
          htmlFor="gh-token"
          hint={
            a?.token_stored
              ? "A token is already stored. Paste a new one to replace it; the stored value is never displayed."
              : "Create one at github.com → Settings → Developer settings. Read-only is enough: for classic tokens no scopes are needed for public stars; add `repo` only to index private repositories."
          }
        >
          <div className="flex gap-2">
            <Input
              id="gh-token"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={a?.token_stored ? "••••••••••••  (stored)" : "github_pat_… or ghp_…"}
              className="mono"
            />
            {a?.token_stored && (
              <Button variant="ghost" loading={forget.isPending} onClick={() => forget.mutate()}>
                Remove stored token
              </Button>
            )}
          </div>
        </Field>
      )}
      {method === "public" && (
        <Field label="GitHub username" htmlFor="gh-username" hint="Only this account's public stars are read.">
          <Input id="gh-username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="otey247" autoComplete="off" spellCheck={false} className="max-w-[280px]" />
        </Field>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" loading={save.isPending} disabled={disabled} onClick={() => save.mutate()}>
          {a?.configured && a.method === method && !token ? "Test connection" : "Connect"}
        </Button>
        {a?.configured && a.ok && (
          <span className="inline-flex items-center gap-1.5 text-sm text-fg-2">
            <Check className="size-4" aria-hidden />
            Connected{a.login ? ` as ${a.login}` : a.username ? ` to ${a.username}'s public stars` : ""}
            {a.rate_limit?.remaining !== null && a.rate_limit?.remaining !== undefined && (
              <span className="mono text-xs text-fg-3">
                · {formatNumber(a.rate_limit.remaining)}/{formatNumber(a.rate_limit.limit)} requests left
              </span>
            )}
          </span>
        )}
      </div>
      {(save.isError || (a?.configured && !a.ok && a.detail) || (save.data && !save.data.ok)) && (
        <p className="flex items-start gap-2 text-sm text-fg-2" role="alert">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
          {save.isError ? errorMessage(save.error) : (save.data?.detail ?? a?.detail)}
        </p>
      )}
      <p className="text-xs leading-relaxed text-fg-4">
        StarIndex only reads from GitHub. It never stars, unstars, writes or runs anything in a repository.
      </p>
    </div>
  );
}
