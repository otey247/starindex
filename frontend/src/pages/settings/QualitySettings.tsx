import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Download, Play, ThumbsDown, ThumbsUp, SearchX, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { ApiError, errorMessage } from "@/api/errors";
import { keys, useSettings } from "@/api/queries";
import type { AppSettings, EvalRun, FeedbackQuery } from "@/api/types";
import { Button, Dialog, ErrorState, ProgressBar, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatSettingValue, settingLabel, settingsDiff, type SettingChange } from "@/lib/feedback";
import { formatMs, formatNumber, pct, timeAgo } from "@/lib/format";
import { toast } from "@/state/stores";
import { Group, PageTitle } from "./GeneralSettings";

const fmt = (n: number) => n.toFixed(3);

/** One row per retrieval configuration; the current and best ones are named in words. */
export function EvalTable({ run }: { run: EvalRun }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm" data-testid="eval-table">
        <thead>
          <tr className="border-b border-line text-left">
            <th className="label py-1.5 pr-4 font-medium">Configuration</th>
            <th className="label py-1.5 pr-4 text-right font-medium">nDCG@10</th>
            <th className="label py-1.5 pr-4 text-right font-medium">Recall@10</th>
            <th className="label py-1.5 pr-4 text-right font-medium">MRR</th>
            <th className="label py-1.5 pr-4 text-right font-medium" title="Average number of results you marked not relevant among the first ten">
              Rejected in top 10
            </th>
            <th className="label py-1.5 text-right font-medium">Median latency</th>
          </tr>
        </thead>
        <tbody>
          {run.results.map((r) => {
            const current = r.config === run.current_config;
            const best = r.config === run.best_config;
            return (
              <tr key={r.config} data-config={r.config} data-available={r.available} className={cn("border-b border-line", !r.available && "text-fg-4")}>
                <td className="py-2 pr-4">
                  <span className={cn(r.available ? (best ? "font-semibold text-fg" : "text-fg-2") : "text-fg-4")}>{r.label}</span>
                  {current && <span className="label ml-2 !text-fg-2">current</span>}
                  {best && <span className="label ml-2 !text-accent">best</span>}
                  {!r.available && <span className="ml-2 text-xs">not available (no semantic index or reranker)</span>}
                </td>
                {r.available ? (
                  <>
                    <td className={cn("py-2 pr-4 text-right tnum", best ? "font-semibold text-fg" : "text-fg-2")}>{fmt(r.ndcg_at_10)}</td>
                    <td className="py-2 pr-4 text-right text-fg-2 tnum">{fmt(r.recall_at_10)}</td>
                    <td className="py-2 pr-4 text-right text-fg-2 tnum">{fmt(r.mrr)}</td>
                    <td className="py-2 pr-4 text-right text-fg-2 tnum">{r.rejected_in_top_10.toFixed(2)}</td>
                    <td className="py-2 text-right text-fg-3 tnum">{formatMs(r.latency_ms_median)}</td>
                  </>
                ) : (
                  <td colSpan={5} className="py-2 text-right">
                    —
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Confirmation that spells out every setting that will change, from → to. */
export function ApplyDialog({
  open,
  onClose,
  onConfirm,
  changes,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  changes: SettingChange[];
  pending?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      title="Apply the suggested search settings?"
      description="Only these settings change. You can set them back at any time under Settings → Search."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={pending} disabled={changes.length === 0} onClick={onConfirm}>
            Apply {changes.length} change{changes.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      {changes.length === 0 ? (
        <p className="text-sm text-fg-3">Your settings already match the suggestion.</p>
      ) : (
        <ul className="flex flex-col" data-testid="apply-changes">
          {changes.map((c) => (
            <li key={c.path} className="grid grid-cols-[1fr_auto] items-baseline gap-x-4 border-b border-line py-2 text-sm last:border-b-0">
              <span className="text-fg-2" title={c.path}>
                {settingLabel(c.path)}
              </span>
              <span className="mono inline-flex items-center gap-1.5 text-xs">
                <span className="text-fg-3">{formatSettingValue(c.from)}</span>
                <ArrowRight className="size-3 text-fg-4" aria-hidden />
                <span className="font-medium text-fg">{formatSettingValue(c.to)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

const VERDICT_ICON = {
  relevant: <ThumbsUp className="size-3 fill-accent text-accent" aria-label="Relevant" />,
  not_relevant: <ThumbsDown className="size-3 fill-fg-2 text-fg-2" aria-label="Not relevant" />,
  missing: <SearchX className="size-3 text-fg-2" aria-label="Missing from results" />,
} as const;

function JudgedQueries({ queries, onRemove }: { queries: FeedbackQuery[]; onRemove: (id: number) => void }) {
  if (queries.length === 0) {
    return (
      <p className="mt-2 max-w-[70ch] text-sm leading-relaxed text-fg-3">
        No judgments yet. Run a search and use the thumbs on a result (or press <span className="mono text-fg-2">+</span> / <span className="mono text-fg-2">-</span> on a focused result) to say whether it belongs.
      </p>
    );
  }
  return (
    <ul className="mt-1" data-testid="judged-queries">
      {queries.map((q) => (
        <li key={q.query} className="border-b border-line py-2.5">
          <div className="flex items-baseline justify-between gap-4">
            <Link to={`/search?q=${encodeURIComponent(q.query)}`} className="min-w-0 truncate text-base font-medium text-fg hover:underline" title="Run this search">
              {q.query}
            </Link>
            <span className="shrink-0 text-xs text-fg-4">{timeAgo(q.updated_at)}</span>
          </div>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {q.judgments.map((j) => (
              <li key={j.feedback_id} className="inline-flex h-[24px] items-center gap-1.5 rounded-[4px] border border-line bg-raised pl-1.5 pr-1 text-xs text-fg-2">
                {VERDICT_ICON[j.verdict]}
                <Link to={`/repo/${j.repo_id}`} className="mono max-w-[240px] truncate hover:underline">
                  {j.full_name}
                </Link>
                <button type="button" aria-label={`Remove judgment for ${j.full_name}`} onClick={() => onRemove(j.feedback_id)} className="cursor-pointer text-fg-4 hover:text-fg">
                  <X className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

export default function QualitySettings() {
  const qc = useQueryClient();
  const settings = useSettings();
  const status = useQuery({
    queryKey: ["eval", "status"],
    queryFn: api.evalStatus,
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });
  const queries = useQuery({ queryKey: ["feedback-queries"], queryFn: api.feedbackQueries });
  const [confirming, setConfirming] = useState(false);
  const [started, setStarted] = useState(false);

  const run = useMutation({
    mutationFn: api.runEval,
    onSuccess: () => {
      setStarted(true);
      qc.setQueryData(["eval", "status"], (prev: typeof status.data) => (prev ? { ...prev, running: true } : prev));
      qc.invalidateQueries({ queryKey: ["eval", "status"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const apply = useMutation({
    mutationFn: api.applyEval,
    onSuccess: (next: AppSettings) => {
      qc.setQueryData(keys.settings, next);
      qc.invalidateQueries({ queryKey: ["search"] });
      setConfirming(false);
      toast.success("Search settings updated from your evaluation.");
    },
    onError: (err) => {
      setConfirming(false);
      toast.error(err instanceof ApiError && err.kind === "nothing_to_apply" ? "The latest evaluation does not suggest a change." : errorMessage(err));
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSearchFeedback(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback-queries"] });
      qc.invalidateQueries({ queryKey: ["eval"] });
      qc.invalidateQueries({ queryKey: ["search-feedback"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const s = status.data;
  const latest = s?.latest ?? null;
  const running = Boolean(s?.running) || run.isPending;
  const changes = latest?.suggested_settings ? settingsDiff(settings.data, latest.suggested_settings) : [];
  const enough = s ? s.queries >= s.min_queries_for_recommendation : false;

  return (
    <div>
      <PageTitle title="Search quality">
        Marking results as relevant, not relevant or missing builds a private test set from your own searches. Evaluating it shows which retrieval settings rank your library best; nothing changes unless you apply a suggestion.
      </PageTitle>

      {status.isLoading ? (
        <Skeleton className="mt-6 h-40 w-full" />
      ) : status.isError || !s ? (
        <ErrorState className="mt-6" message={errorMessage(status.error)} onRetry={() => status.refetch()} />
      ) : (
        <>
          <Group title="Your judgments">
            <div className="mt-2 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
              <dl className="flex gap-10">
                <div>
                  <dt className="label">Judged queries</dt>
                  <dd className="mt-0.5 text-xl font-semibold text-fg tnum" data-testid="eval-queries">
                    {formatNumber(s.queries)}
                  </dd>
                </div>
                <div>
                  <dt className="label">Judgments</dt>
                  <dd className="mt-0.5 text-xl font-semibold text-fg tnum" data-testid="eval-judgments">
                    {formatNumber(s.judgments)}
                  </dd>
                </div>
              </dl>
              <div className="flex items-center gap-2">
                <a href={api.feedbackExportUrl()} download className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay">
                  <Download className="size-3.5" aria-hidden /> Export judgments
                </a>
                <Button variant="primary" icon={<Play className="size-3.5" />} loading={running} disabled={s.queries === 0} onClick={() => run.mutate()}>
                  {running ? "Evaluating…" : "Run evaluation"}
                </Button>
              </div>
            </div>
            <div className="mt-3 max-w-[520px]">
              <ProgressBar value={pct(s.queries, s.min_queries_for_recommendation)} label="Progress toward a recommendation" />
              <p className="mt-1.5 text-xs text-fg-3">
                {enough
                  ? `${s.queries} judged queries — enough for a recommendation.`
                  : `${s.queries} of ${s.min_queries_for_recommendation} judged queries needed before StarIndex recommends a change. You can evaluate with fewer; the numbers are just less reliable.`}
              </p>
            </div>
            {running && (
              <p className="mt-3 text-sm text-fg-2" role="status">
                Running every retrieval configuration against your judged queries…
              </p>
            )}
          </Group>

          <Group title="Latest evaluation">
            {!latest ? (
              <p className="mt-2 text-sm text-fg-3">{s.queries === 0 ? "Judge a few search results first, then run an evaluation." : started && running ? "The first evaluation is running." : "No evaluation has been run yet."}</p>
            ) : (
              <div className="mt-2">
                <p className="text-xs text-fg-3">
                  {timeAgo(latest.created_at)} · {latest.queries} quer{latest.queries === 1 ? "y" : "ies"} · {latest.judgments} judgment{latest.judgments === 1 ? "" : "s"}
                </p>
                <div className="mt-2">
                  <EvalTable run={latest} />
                </div>
                {latest.recommendation && (
                  <p className="mt-3 max-w-[76ch] text-sm leading-relaxed text-fg-2" data-testid="eval-recommendation">
                    {latest.recommendation}
                  </p>
                )}
                {latest.suggested_settings && (
                  <Button className="mt-3" variant="primary" onClick={() => setConfirming(true)}>
                    Apply suggested settings
                  </Button>
                )}
              </div>
            )}
          </Group>

          <Group title="Judged queries" description="Click a query to run it again. Removing a judgment removes it from the evaluation set.">
            {queries.isLoading ? <Skeleton className="mt-3 h-24 w-full" /> : <JudgedQueries queries={queries.data ?? []} onRemove={(id) => remove.mutate(id)} />}
          </Group>
        </>
      )}
      <ApplyDialog open={confirming} onClose={() => setConfirming(false)} onConfirm={() => apply.mutate()} changes={changes} pending={apply.isPending} />
    </div>
  );
}
