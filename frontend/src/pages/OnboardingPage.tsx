import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, Play, Search as SearchIcon, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { isSyncActive, keys, useSyncStatus, useSystemStatus } from "@/api/queries";
import { GitHubConnect } from "@/components/GitHubConnect";
import { Wordmark } from "@/components/shell/AppShell";
import { Button, ProgressBar } from "@/components/ui";
import { Toaster } from "@/components/ui/Toaster";
import { cn } from "@/lib/cn";
import { formatDuration, formatNumber, pct } from "@/lib/format";
import { IndexBar } from "@/components/sync/SyncBits";

type Step = 1 | 2 | 3;

function StepHeader({ n, title, state }: { n: number; title: string; state: "done" | "current" | "todo" }) {
  return (
    <div className="flex items-center gap-3">
      <span
        className={cn(
          "flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs",
          state === "done" ? "border-fg-3 bg-fg text-bg" : state === "current" ? "border-accent text-accent" : "border-line-strong text-fg-4",
        )}
      >
        {state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : n}
      </span>
      <h2 className={cn("text-xl font-semibold", state === "todo" ? "text-fg-4" : "text-fg")}>{title}</h2>
    </div>
  );
}

/** First-run flow: connect GitHub, run the first full sync, land in Search. */
export default function OnboardingPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const status = useSystemStatus();
  const sync = useSyncStatus();
  const [step, setStep] = useState<Step>(1);
  const [started, setStarted] = useState(false);

  const gh = sync.data?.github ?? status.data?.github;
  const s = sync.data;
  const run = s?.current_run;
  const active = isSyncActive(s);
  const total = s?.index.total ?? 0;

  // Pick up where the user left off (e.g. reopening the app mid-sync).
  useEffect(() => {
    if (!s) return;
    if (run || total > 0) {
      setStarted(true);
      setStep((prev) => (prev < 2 ? 2 : prev));
    } else if (gh?.configured && gh.ok) {
      setStep((prev) => (prev < 2 ? 2 : prev));
    }
  }, [s, run, total, gh?.configured, gh?.ok]);

  const start = useMutation({
    mutationFn: () => api.startSync("full"),
    onSuccess: () => {
      setStarted(true);
      qc.invalidateQueries({ queryKey: keys.sync });
    },
  });

  const finish = async (to: string) => {
    sessionStorage.setItem("starindex.skipOnboarding", "1");
    try {
      await api.patchSettings({ onboarding_completed: true });
    } catch {
      // Non-fatal: the flag only controls whether this screen is shown again.
    }
    qc.invalidateQueries({ queryKey: keys.status });
    navigate(to, { replace: true });
  };

  const discoveryDone = started && !run && total > 0;
  const idx = s?.index;
  const indexedPct = idx && idx.total ? pct(idx.indexed + idx.skipped, idx.total) : 0;
  const syncFailed = started && !run && total === 0 && (s?.last_full?.status === "failed" || s?.last_full?.status === "interrupted");

  return (
    <div className="flex min-h-dvh flex-col bg-bg">
      <header className="flex h-14 items-center justify-between border-b border-line px-5 md:px-8">
        <Wordmark />
        <button type="button" onClick={() => finish("/search")} className="cursor-pointer text-sm text-fg-3 hover:text-fg">
          Skip setup
        </button>
      </header>

      <main className="mx-auto grid w-full max-w-[1180px] flex-1 gap-x-16 gap-y-10 px-5 py-10 md:px-8 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] lg:py-14">
        <div className="min-w-0">
          <p className="label animate-rise">First run</p>
          <h1 className="mt-2 max-w-[18ch] text-4xl font-semibold leading-[1.05] tracking-[-0.03em] text-fg animate-rise [animation-delay:40ms]">
            Turn your stars into a searchable index.
          </h1>
          <p className="mt-4 max-w-[58ch] text-md leading-relaxed text-fg-3 animate-rise [animation-delay:80ms]">
            StarIndex imports every repository you have starred, indexes its README and documentation, and makes all of it searchable on this machine. No model is required for search.
          </p>

          <ol className="mt-10 flex flex-col gap-9">
            <li className="animate-rise [animation-delay:120ms]">
              <StepHeader n={1} title="Connect GitHub" state={step > 1 ? "done" : "current"} />
              <div className="ml-9 mt-3">
                {step === 1 ? (
                  <GitHubConnect onConnected={() => setStep(2)} />
                ) : (
                  <p className="text-sm text-fg-3">
                    {gh?.login ? `Connected as ${gh.login}.` : gh?.username ? `Reading ${gh.username}'s public stars.` : "Connected."}{" "}
                    <button type="button" className="link cursor-pointer" onClick={() => setStep(1)} disabled={Boolean(run)}>
                      Change
                    </button>
                  </p>
                )}
              </div>
            </li>

            <li className="animate-rise [animation-delay:160ms]">
              <StepHeader n={2} title="Import your starred repositories" state={step < 2 ? "todo" : discoveryDone ? "done" : "current"} />
              {step >= 2 && (
                <div className="ml-9 mt-3 flex flex-col gap-4">
                  {!started ? (
                    <>
                      <p className="max-w-[60ch] text-sm leading-relaxed text-fg-3">
                        The first sync walks your complete starred list, however long it is, then indexes README and documentation in the background. You can close StarIndex at any point; it resumes where it stopped.
                      </p>
                      <div className="flex items-center gap-3">
                        <Button variant="primary" size="lg" icon={<Play className="size-4" />} loading={start.isPending} onClick={() => start.mutate()}>
                          Start the first sync
                        </Button>
                      </div>
                      {start.isError && (
                        <p className="flex items-start gap-2 text-sm text-fg-2" role="alert">
                          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" /> {errorMessage(start.error)}
                        </p>
                      )}
                    </>
                  ) : (
                    <div className="flex flex-col gap-5" aria-live="polite">
                      <div>
                        <div className="flex items-baseline justify-between gap-4">
                          <p className="text-base font-medium text-fg">
                            {run ? "Discovering starred repositories" : syncFailed ? "The sync stopped" : "All starred repositories imported"}
                          </p>
                          <p className="mono text-sm text-fg-2 tnum">
                            {formatNumber(run ? run.repos_seen : total)}
                            {run?.total_expected ? ` / ${formatNumber(run.total_expected)}` : ""}
                          </p>
                        </div>
                        <ProgressBar
                          className="mt-2 !h-1.5"
                          label="Star discovery"
                          value={run ? (run.total_expected ? pct(run.repos_seen, run.total_expected) : null) : syncFailed ? 0 : 100}
                        />
                        <p className="mt-1.5 text-xs text-fg-3">
                          {run
                            ? `Page ${run.pages_fetched}${run.total_expected ? ` of ${Math.ceil(run.total_expected / 100)}` : ""} · ${formatNumber(run.added)} new`
                            : syncFailed
                              ? (s?.last_full?.error ?? "It was interrupted before finishing.")
                              : "Metadata is stored. Search by name, description and topic works now."}
                        </p>
                        {syncFailed && (
                          <Button className="mt-2" onClick={() => start.mutate()} loading={start.isPending}>
                            Resume sync
                          </Button>
                        )}
                      </div>

                      {idx && total > 0 && (
                        <div>
                          <div className="flex items-baseline justify-between gap-4">
                            <p className="text-base font-medium text-fg">Indexing documentation</p>
                            <p className="mono text-sm text-fg-2 tnum">{indexedPct.toFixed(0)}%</p>
                          </div>
                          <div className="mt-2">
                            <IndexBar index={idx} />
                          </div>
                          <p className="mt-2 text-xs text-fg-3">
                            {formatNumber(idx.documents)} files · {formatNumber(idx.chunks)} passages · {formatNumber(idx.chunks_embedded)} embedded
                            {s.queue.throughput_per_min > 0 ? ` · ${s.queue.throughput_per_min.toFixed(1)} repos/min` : ""}
                            {s.queue.eta_seconds ? ` · about ${formatDuration(s.queue.eta_seconds)} left` : ""}
                            {!active && indexedPct >= 100 ? " · done" : ""}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </li>

            <li className="animate-rise [animation-delay:200ms]">
              <StepHeader n={3} title="Start searching" state={total > 0 ? "current" : "todo"} />
              {total > 0 && (
                <div className="ml-9 mt-3 flex flex-col gap-3">
                  <p className="max-w-[60ch] text-sm leading-relaxed text-fg-3">
                    You do not need to wait for indexing. Results get richer as README and documentation passages are added; semantic search switches on as embeddings complete.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="primary" size="lg" icon={<SearchIcon className="size-4" />} onClick={() => finish("/search")}>
                      Open Search
                    </Button>
                    <Button size="lg" variant="ghost" onClick={() => finish("/settings/models")}>
                      Set up a model for Ask AI
                      <ArrowRight className="size-4" />
                    </Button>
                  </div>
                </div>
              )}
            </li>
          </ol>
        </div>

        <aside className="flex flex-col gap-7 lg:pt-16">
          {[
            ["Local by default", "Repository text, embeddings, notes and credentials stay on this computer. Cloud models are off until you turn them on."],
            ["Search needs no model", "Keyword, semantic and hybrid search run on local indexes. A local or cloud model is only used for Ask AI and Research."],
            ["Nothing is executed", "Repository content is read as untrusted text. StarIndex never runs code, scripts or installers from a repository."],
            ["Yours stays yours", "Notes, tags and collections are never overwritten by sync, even if you unstar a repository later."],
          ].map(([title, body]) => (
            <div key={title}>
              <p className="text-base font-semibold text-fg">{title}</p>
              <p className="mt-1 text-sm leading-relaxed text-fg-3">{body}</p>
            </div>
          ))}
        </aside>
      </main>
      <Toaster />
    </div>
  );
}
