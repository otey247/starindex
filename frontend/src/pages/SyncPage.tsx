import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Play, RefreshCw, RotateCcw, Square, TriangleAlert, X } from "lucide-react";
import { GithubMark as Github } from "@/components/ui/GithubMark";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { isSyncActive, keys, useSyncStatus } from "@/api/queries";
import type { Job, JobType, SyncRun, SyncStatus } from "@/api/types";
import { Button, ErrorState, IconButton, Menu, ProgressBar, SectionHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDateTime, formatDuration, formatNumber, INDEX_LEVEL_LABEL, JOB_LABEL, pct, timeAgo } from "@/lib/format";
import { IndexBar, ScheduleEditor } from "@/components/sync/SyncBits";
import { toast } from "@/state/stores";

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="label">{label}</dt>
      <dd className="mt-0.5 truncate text-xl font-semibold text-fg tnum">{value}</dd>
      {sub && <dd className="truncate text-xs text-fg-3">{sub}</dd>}
    </div>
  );
}

function runDuration(run: SyncRun): string {
  if (!run.finished_at) return "running";
  return formatDuration((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000);
}

function runSummary(run: SyncRun): string {
  if (run.not_modified) return "No changes on GitHub";
  const parts = [`${formatNumber(run.repos_seen)} seen`];
  if (run.added) parts.push(`${run.added} added`);
  if (run.updated) parts.push(`${run.updated} updated`);
  if (run.renamed) parts.push(`${run.renamed} renamed`);
  if (run.unstarred) parts.push(`${run.unstarred} unstarred`);
  if (run.restarred) parts.push(`${run.restarred} re-starred`);
  return parts.join(" · ");
}

function ConnectionSection({ status }: { status: SyncStatus }) {
  const gh = status.github;
  const rl = status.rate_limit ?? gh.rate_limit;
  const METHOD: Record<string, string> = { gh_cli: "GitHub CLI", token: "Personal access token", public: "Public stars (no token)", none: "Not connected" };
  return (
    <section aria-label="GitHub connection">
      <SectionHeader title="GitHub">
        <Link to="/settings/github" className="text-xs text-fg-3 hover:text-fg">
          Change access
        </Link>
      </SectionHeader>
      <div className="mt-2 flex items-start gap-3">
        <Github className="mt-0.5 size-5 shrink-0 text-fg-2" aria-hidden />
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-md font-semibold text-fg">
            {gh.configured ? (gh.login ?? gh.username ?? "Connected") : "Not connected"}
            {gh.configured && (gh.ok ? <Check className="size-3.5 text-fg-2" aria-label="Working" /> : <TriangleAlert className="size-3.5 text-warn" aria-label="Problem" />)}
          </p>
          <p className="text-sm text-fg-3">
            {METHOD[gh.method]}
            {gh.scopes.length ? ` · scopes: ${gh.scopes.join(", ")}` : gh.configured ? " · read-only" : ""}
          </p>
          {gh.detail && <p className="mt-0.5 text-sm text-fg-3">{gh.detail}</p>}
          {rl && rl.limit !== null && (
            <p className="mono mt-1 text-xs text-fg-3">
              API budget {formatNumber(rl.remaining)} / {formatNumber(rl.limit)}
              {rl.reset_at ? ` · resets ${timeAgo(rl.reset_at)}` : ""}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function JobRow({ job, expanded, onToggle }: { job: Job; expanded: boolean; onToggle: () => void }) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: keys.sync });
  };
  const retry = useMutation({ mutationFn: () => api.retryJob(job.id), onSuccess: invalidate, onError: (e) => toast.error(errorMessage(e)) });
  const cancel = useMutation({ mutationFn: () => api.cancelJob(job.id), onSuccess: invalidate, onError: (e) => toast.error(errorMessage(e)) });
  const progress = job.progress_total > 0 ? pct(job.progress_current, job.progress_total) : null;
  const when = job.finished_at ?? job.started_at ?? job.created_at;
  return (
    <Fragment>
      <tr className={cn("border-b border-line align-top", expanded && "bg-surface")}>
        <td className="py-2 pl-1 pr-2">
          <button type="button" onClick={onToggle} aria-expanded={expanded} aria-label="Toggle job details" className="inline-flex size-5 cursor-pointer items-center justify-center text-fg-4 hover:text-fg">
            <ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
          </button>
        </td>
        <td className="py-2 pr-4">
          <p className="text-sm font-medium text-fg">{JOB_LABEL[job.type] ?? job.type}</p>
          {job.repo_full_name && <p className="mono truncate text-xs text-fg-3">{job.repo_full_name}</p>}
        </td>
        <td className="whitespace-nowrap py-2 pr-4 text-sm">
          <span className={cn("inline-flex items-center gap-1.5", job.status === "failed" ? "font-medium text-fg" : job.status === "running" ? "text-fg" : "text-fg-3")}>
            {job.status === "running" && <span className="size-1.5 rounded-full bg-accent animate-pulse-dot" aria-hidden />}
            {job.status === "failed" && <TriangleAlert className="size-3.5 text-danger" aria-hidden />}
            {job.status}
          </span>
        </td>
        <td className="min-w-[160px] py-2 pr-4">
          {job.status === "running" ? (
            <div>
              <ProgressBar value={progress} label={`${JOB_LABEL[job.type] ?? job.type} progress`} className="mt-2" />
              <p className="mt-1 truncate text-xs text-fg-3">
                {job.progress_total > 0 ? `${formatNumber(job.progress_current)} / ${formatNumber(job.progress_total)} · ` : ""}
                {job.progress_message}
              </p>
            </div>
          ) : job.error ? (
            <p className="line-clamp-1 text-xs text-fg-3" title={job.error}>
              {job.error}
            </p>
          ) : (
            <p className="truncate text-xs text-fg-4">{job.progress_message || "—"}</p>
          )}
        </td>
        <td className="whitespace-nowrap py-2 pr-4 text-xs text-fg-3 tnum">
          {job.attempts}/{job.max_attempts}
        </td>
        <td className="whitespace-nowrap py-2 pr-4 text-xs text-fg-3" title={formatDateTime(when)}>
          {timeAgo(when)}
        </td>
        <td className="py-1.5 text-right">
          {(job.status === "failed" || job.status === "cancelled") && (
            <IconButton label="Retry this job" size="sm" onClick={() => retry.mutate()} disabled={retry.isPending}>
              <RotateCcw className="size-3.5" />
            </IconButton>
          )}
          {(job.status === "queued" || job.status === "running") && (
            <IconButton label="Cancel this job" size="sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              <X className="size-3.5" />
            </IconButton>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-line bg-surface">
          <td />
          <td colSpan={6} className="pb-3 pr-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-fg-4">Job id</dt>
              <dd className="mono text-fg-3">{job.id}</dd>
              <dt className="text-fg-4">Created</dt>
              <dd className="text-fg-3">{formatDateTime(job.created_at)}</dd>
              {job.run_after && (
                <>
                  <dt className="text-fg-4">Retries after</dt>
                  <dd className="text-fg-3">{formatDateTime(job.run_after)}</dd>
                </>
              )}
              {Object.keys(job.payload ?? {}).length > 0 && (
                <>
                  <dt className="text-fg-4">Payload</dt>
                  <dd className="mono break-all text-fg-3">{JSON.stringify(job.payload)}</dd>
                </>
              )}
              {job.error && (
                <>
                  <dt className="text-fg-4">Error</dt>
                  <dd className="whitespace-pre-wrap break-words text-fg-2">{job.error}</dd>
                </>
              )}
              {job.result && (
                <>
                  <dt className="text-fg-4">Result</dt>
                  <dd className="mono break-all text-fg-3">{JSON.stringify(job.result)}</dd>
                </>
              )}
            </dl>
            {job.status === "failed" && (
              <p className="mt-2 text-xs text-fg-3">
                Recovery: retry the job; if it keeps failing because of rate limits or network access, it resumes safely from its checkpoint once the cause clears.
              </p>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function JobsSection({ active }: { active: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"" | "running" | "queued" | "failed" | "succeeded">("");
  const [type, setType] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const limit = 25;
  const params = { status: status || undefined, type: type || undefined, limit, offset: page * limit };
  const jobs = useQuery({ queryKey: keys.jobs(params), queryFn: () => api.jobs(params), refetchInterval: active ? 2500 : 20_000 });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: keys.sync });
  };
  const retryAll = useMutation({ mutationFn: api.retryFailedJobs, onSuccess: () => { invalidate(); toast.success("Failed jobs re-queued."); }, onError: (e) => toast.error(errorMessage(e)) });
  const clear = useMutation({ mutationFn: api.clearFinishedJobs, onSuccess: () => { invalidate(); toast.success("Finished jobs cleared."); }, onError: (e) => toast.error(errorMessage(e)) });
  const enqueue = useMutation({
    mutationFn: (t: JobType) => api.enqueueJob(t),
    onSuccess: (job) => { invalidate(); toast.success(`${JOB_LABEL[job.type] ?? job.type} queued.`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const maintenance: { type: JobType; hint: string }[] = [
    { type: "embed_pending", hint: "embed passages that are not in the vector index yet" },
    { type: "classify_repos", hint: "re-run automatic categorisation" },
    { type: "refresh_metadata", hint: "re-fetch repository metadata" },
    { type: "rebuild_search_index", hint: "rebuild the lexical (FTS) index" },
    { type: "rebuild_vector_index", hint: "rebuild Qdrant from stored embeddings" },
    { type: "cleanup", hint: "remove orphaned data and old indexes" },
    { type: "backup", hint: "write a backup now" },
  ];
  const totalPages = jobs.data ? Math.max(1, Math.ceil(jobs.data.total / limit)) : 1;
  return (
    <section aria-label="Jobs">
      <SectionHeader title="Jobs" count={jobs.data?.total ?? null}>
        <Menu
          align="end"
          width={330}
          header={<span className="label">Run a maintenance job</span>}
          items={maintenance.map((m) => ({ key: m.type, label: JOB_LABEL[m.type] ?? m.type, hint: undefined, onSelect: () => enqueue.mutate(m.type) }))}
          footer={<p className="py-1 text-2xs leading-relaxed text-fg-4">Jobs persist across restarts and resume from their checkpoints.</p>}
          trigger={(p) => (
            <Button size="sm" {...p}>
              Run job
            </Button>
          )}
        />
        <Button size="sm" icon={<RotateCcw className="size-3.5" />} loading={retryAll.isPending} onClick={() => retryAll.mutate()}>
          Retry all failed
        </Button>
        <Button size="sm" variant="ghost" loading={clear.isPending} onClick={() => clear.mutate()}>
          Clear finished
        </Button>
      </SectionHeader>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Segmented
          size="sm"
          label="Job status filter"
          value={status}
          onChange={(v) => { setStatus(v); setPage(0); }}
          options={[
            { value: "", label: "All" },
            { value: "running", label: "Running" },
            { value: "queued", label: "Queued" },
            { value: "failed", label: "Failed" },
            { value: "succeeded", label: "Done" },
          ]}
        />
        <Select value={type} onChange={(e) => { setType(e.target.value); setPage(0); }} aria-label="Job type filter">
          <option value="">All types</option>
          {Object.entries(JOB_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </div>
      {jobs.isLoading ? (
        <Skeleton className="mt-3 h-32 w-full" />
      ) : jobs.isError ? (
        <ErrorState className="mt-3" message={errorMessage(jobs.error)} onRetry={() => jobs.refetch()} />
      ) : !jobs.data?.items.length ? (
        <p className="mt-4 text-sm text-fg-3">{status || type ? "No jobs match this filter." : "No jobs yet. Start a sync to import your starred repositories."}</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="w-7" />
                <th className="label py-1.5 pr-4 font-medium">Job</th>
                <th className="label py-1.5 pr-4 font-medium">Status</th>
                <th className="label py-1.5 pr-4 font-medium">Progress</th>
                <th className="label py-1.5 pr-4 font-medium">Tries</th>
                <th className="label py-1.5 pr-4 font-medium">When</th>
                <th className="w-9" />
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((job) => (
                <JobRow key={job.id} job={job} expanded={expanded === job.id} onToggle={() => setExpanded((e) => (e === job.id ? null : job.id))} />
              ))}
            </tbody>
          </table>
          {totalPages > 1 && (
            <div className="flex items-center justify-end gap-2 py-3">
              <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span className="text-xs text-fg-3 tnum">
                {page + 1} / {totalPages}
              </span>
              <Button size="sm" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function SyncPage() {
  const qc = useQueryClient();
  const sync = useSyncStatus();
  const runs = useQuery({ queryKey: ["sync", "runs"], queryFn: () => api.syncRuns(10), refetchInterval: 15_000 });
  const start = useMutation({
    mutationFn: (kind: "full" | "incremental") => api.startSync(kind),
    onSuccess: (_job, kind) => {
      qc.invalidateQueries({ queryKey: ["sync"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      toast.success(kind === "full" ? "Full sync started." : "Checking for new stars.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancel = useMutation({
    mutationFn: api.cancelSync,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sync"] });
      toast.info("Sync cancelled. Progress so far is kept and the next run resumes.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (sync.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[1180px] px-4 pt-8 md:px-8">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="mt-6 h-48 w-full" />
      </div>
    );
  }
  if (sync.isError || !sync.data) {
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 pt-10 md:px-8">
        <ErrorState title="Could not load sync status" message={errorMessage(sync.error)} onRetry={() => sync.refetch()} />
      </div>
    );
  }
  const s = sync.data;
  const run = s.current_run;
  const active = isSyncActive(s);
  const idx = s.index;
  const remaining = idx.pending + idx.queued + idx.indexing;
  const embedPct = idx.chunks ? pct(idx.chunks_embedded, idx.chunks) : 0;

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 pb-20 pt-6 md:px-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Sync</h1>
          <p className="mt-0.5 text-sm text-fg-3">GitHub synchronisation and content indexing. Discovery of stars and indexing of content run as separate, resumable jobs.</p>
        </div>
        <div className="flex gap-2">
          {run ? (
            <Button icon={<Square className="size-3" />} loading={cancel.isPending} onClick={() => cancel.mutate()}>
              Cancel sync
            </Button>
          ) : (
            <>
              <Button icon={<RefreshCw className="size-3.5" />} disabled={!s.github.configured} loading={start.isPending && start.variables === "full"} onClick={() => start.mutate("full")} title="Walk the complete starred list; detects unstarred and renamed repositories">
                Full sync
              </Button>
              <Button variant="primary" icon={<Play className="size-3.5" />} disabled={!s.github.configured} loading={start.isPending && start.variables === "incremental"} onClick={() => start.mutate("incremental")}>
                Sync new stars
              </Button>
            </>
          )}
        </div>
      </header>

      {!s.github.configured && (
        <div className="mt-5 flex flex-wrap items-center gap-3 rounded-md border border-line-strong bg-surface p-4">
          <Github className="size-5 shrink-0 text-fg-2" aria-hidden />
          <p className="min-w-0 flex-1 text-sm text-fg-2">
            <span className="font-semibold text-fg">Connect GitHub to import your stars.</span> StarIndex only needs read access.
          </p>
          <Link to="/settings/github" className="inline-flex h-8 items-center rounded-sm bg-accent-solid px-3 text-sm font-medium text-white hover:bg-accent-solid-hover">
            Connect GitHub
          </Link>
        </div>
      )}
      {!s.worker.alive && (
        <p className="mt-4 flex items-start gap-2 text-sm text-fg-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          The background worker is not running, so queued jobs will not start. Restart StarIndex, or run <span className="mono text-xs">starindex worker</span> if you use a separate worker process.
        </p>
      )}

      <div className="mt-7 grid gap-x-12 gap-y-8 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-9">
          <section aria-label="Star synchronisation">
            <SectionHeader title={run ? `${run.kind === "full" ? "Full" : "Incremental"} sync in progress` : "Star synchronisation"} />
            {run ? (
              <div className="mt-2 animate-rise">
                <ProgressBar value={run.total_expected ? pct(run.repos_seen, run.total_expected) : null} label="Sync progress" className="!h-1.5" />
                <dl className="mt-4 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
                  <Stat label="Repositories" value={formatNumber(run.repos_seen)} sub={run.total_expected ? `of ${formatNumber(run.total_expected)} starred` : "counting…"} />
                  <Stat label="Pages fetched" value={formatNumber(run.pages_fetched)} sub="100 per page" />
                  <Stat label="New" value={formatNumber(run.added)} sub={`${formatNumber(run.updated)} updated`} />
                  <Stat label="Started" value={timeAgo(run.started_at)} sub={run.renamed ? `${run.renamed} renamed` : undefined} />
                </dl>
              </div>
            ) : (
              <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
                <Stat label="Starred" value={formatNumber(idx.total)} sub="repositories in the library" />
                <Stat label="Last sync" value={timeAgo(s.last_incremental?.finished_at ?? s.last_full?.finished_at)} sub={s.last_full ? `full ${timeAgo(s.last_full.finished_at)}` : "no full sync yet"} />
                <Stat label="Next scheduled" value={s.next_scheduled_sync ? timeAgo(s.next_scheduled_sync) : "Off"} sub={s.next_scheduled_sync ? formatDateTime(s.next_scheduled_sync) : "enable a schedule →"} />
              </dl>
            )}
            {(s.last_full?.status === "failed" || s.last_incremental?.status === "failed" || s.last_full?.status === "interrupted") && !run && (
              <div className="mt-4 flex items-start gap-3 rounded-md border border-line-strong bg-surface p-3.5" role="alert">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-fg">The last sync did not finish</p>
                  <p className="mt-0.5 break-words text-sm text-fg-3">
                    {(s.last_incremental?.status === "failed" ? s.last_incremental.error : s.last_full?.error) ?? "It was interrupted before completing."} Nothing was lost; the next run resumes from its checkpoint.
                  </p>
                </div>
                <Button size="sm" onClick={() => start.mutate(s.last_full?.status !== "succeeded" ? "full" : "incremental")}>
                  Resume
                </Button>
              </div>
            )}
          </section>

          <section aria-label="Content indexing">
            <SectionHeader title="Content indexing">
              {remaining > 0 && s.queue.eta_seconds !== null && s.queue.eta_seconds !== undefined && <span className="text-xs text-fg-3">about {formatDuration(s.queue.eta_seconds)} left</span>}
            </SectionHeader>
            <div className="mt-2.5">
              <IndexBar index={idx} />
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
              <Stat label="Files" value={formatNumber(idx.documents)} sub="indexed documents" />
              <Stat label="Passages" value={formatNumber(idx.chunks)} sub="searchable chunks" />
              <Stat label="Embedded" value={`${embedPct.toFixed(embedPct === 100 || embedPct === 0 ? 0 : 1)}%`} sub={`${formatNumber(idx.chunks_embedded)} vectors`} />
              <Stat label="Throughput" value={s.queue.throughput_per_min ? `${s.queue.throughput_per_min.toFixed(1)}/min` : "idle"} sub={`${formatNumber(s.queue.succeeded_last_hour)} jobs in the last hour`} />
            </dl>
            {Object.keys(idx.by_level).length > 0 && (
              <p className="mt-4 text-xs text-fg-3">
                By level:{" "}
                {Object.entries(idx.by_level)
                  .map(([level, n]) => `${INDEX_LEVEL_LABEL[level] ?? level} ${formatNumber(n)}`)
                  .join(" · ")}
                . Upgrade selected repositories to deep indexing from the Library or a collection.
              </p>
            )}
            {idx.failed > 0 && (
              <p className="mt-2 flex items-center gap-2 text-sm text-fg-2">
                <TriangleAlert className="size-3.5 text-danger" aria-hidden />
                {formatNumber(idx.failed)} repositor{idx.failed === 1 ? "y" : "ies"} failed to index.
                <Link to="/library?index=failed" className="link">
                  Show them
                </Link>
              </p>
            )}
          </section>

          <JobsSection active={active} />
        </div>

        <aside className="flex min-w-0 flex-col gap-8">
          <ConnectionSection status={s} />
          <section aria-label="Queue">
            <SectionHeader title="Queue" />
            <dl className="mt-2 grid grid-cols-3 gap-x-4">
              <Stat label="Running" value={formatNumber(s.queue.running)} />
              <Stat label="Queued" value={formatNumber(s.queue.queued)} />
              <Stat label="Failed" value={formatNumber(s.queue.failed)} />
            </dl>
            {Object.keys(s.queue.by_type).length > 0 && (
              <ul className="mt-3">
                {Object.entries(s.queue.by_type).map(([type, counts]) => (
                  <li key={type} className="flex items-baseline justify-between gap-3 border-b border-line py-1 text-xs">
                    <span className="truncate text-fg-2">{JOB_LABEL[type] ?? type}</span>
                    <span className="shrink-0 text-fg-3 tnum">
                      {Object.entries(counts)
                        .filter(([, n]) => n > 0)
                        .map(([state, n]) => `${n} ${state}`)
                        .join(" · ") || "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-fg-4">
              Worker: {s.worker.alive ? `${s.worker.mode} · seen ${timeAgo(s.worker.last_seen)}` : "not running"}
            </p>
          </section>
          <section aria-label="Schedules">
            <SectionHeader title="Schedules" />
            <ScheduleEditor />
            <p className="mt-2 text-xs leading-relaxed text-fg-4">Schedules run while StarIndex is open. To sync at sign-in, install the Windows startup task from the docs.</p>
          </section>
          <section aria-label="Recent runs">
            <SectionHeader title="Recent runs" />
            {runs.data && runs.data.length > 0 ? (
              <ul className="mt-1">
                {runs.data.map((r) => (
                  <li key={r.id} className="border-b border-line py-2">
                    <p className="flex items-baseline justify-between gap-3 text-sm">
                      <span className={cn("font-medium", r.status === "failed" ? "text-fg" : "text-fg-2")}>
                        {r.kind === "full" ? "Full" : "Incremental"}
                        {r.status !== "succeeded" && <span className="ml-1.5 font-normal text-fg-3">· {r.status}</span>}
                      </span>
                      <span className="shrink-0 text-xs text-fg-4" title={formatDateTime(r.started_at)}>
                        {timeAgo(r.started_at)} · {runDuration(r)}
                      </span>
                    </p>
                    <p className="mt-0.5 text-xs text-fg-3">{r.error ?? runSummary(r)}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 text-sm text-fg-4">No sync has run yet.</p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
