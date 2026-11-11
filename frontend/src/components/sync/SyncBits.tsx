import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { keys } from "@/api/queries";
import type { IndexProgress, Schedule } from "@/api/types";
import { ErrorState, Input, Skeleton, Switch } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatNumber, JOB_LABEL, timeAgo } from "@/lib/format";
import { toast } from "@/state/stores";

/** Monochrome segmented bar: the accent marks finished work, neutrals mark the rest. */
export function IndexBar({ index }: { index: IndexProgress }) {
  const segments = [
    { key: "indexed", label: "Indexed", value: index.indexed, color: "var(--color-accent)" },
    { key: "indexing", label: "Indexing", value: index.indexing, color: "#b6bcc5" },
    { key: "queued", label: "Queued", value: index.queued, color: "#7b8491" },
    { key: "pending", label: "Pending", value: index.pending, color: "#4a515a" },
    { key: "skipped", label: "Skipped", value: index.skipped, color: "#30353b" },
    { key: "failed", label: "Failed", value: index.failed, color: "var(--color-danger)" },
  ];
  const total = Math.max(1, index.total);
  return (
    <div>
      <div className="flex h-2 w-full gap-px overflow-hidden rounded-full bg-line" role="img" aria-label={`Indexed ${index.indexed} of ${index.total} repositories`}>
        {segments.map((s) => (s.value > 0 ? <span key={s.key} style={{ width: `${(s.value / total) * 100}%`, background: s.color }} className="transition-[width] duration-500" /> : null))}
      </div>
      <ul className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5 text-xs">
            <span className="size-2 rounded-[2px]" style={{ background: s.color }} aria-hidden />
            <span className="text-fg-3">{s.label}</span>
            <span className={cn("tnum", s.key === "failed" && s.value > 0 ? "font-medium text-fg" : "text-fg-2")}>{formatNumber(s.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SCHEDULE_LABEL: Record<string, string> = {
  sync_incremental: "Check for new stars",
  sync_full: "Full reconciliation",
  backup: "Back up the database",
  cleanup: "Clean up old data",
  refresh_metadata: "Refresh repository metadata",
};

export function ScheduleEditor() {
  const qc = useQueryClient();
  const schedules = useQuery({ queryKey: keys.schedules, queryFn: api.schedules });
  const patch = useMutation({
    mutationFn: ({ name, body }: { name: string; body: { interval_minutes?: number; enabled?: boolean } }) => api.patchSchedule(name, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.schedules });
      qc.invalidateQueries({ queryKey: keys.sync });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (schedules.isLoading) return <Skeleton className="mt-2 h-24 w-full" />;
  if (schedules.isError) return <ErrorState className="mt-2" message={errorMessage(schedules.error)} onRetry={() => schedules.refetch()} />;
  const Row = ({ s }: { s: Schedule }) => {
    const hours = s.interval_minutes / 60;
    const [draft, setDraft] = useState(String(hours >= 1 ? Math.round(hours * 10) / 10 : s.interval_minutes));
    const unit = hours >= 1 ? "h" : "min";
    const commit = () => {
      const n = Number(draft);
      if (!Number.isFinite(n) || n <= 0) return setDraft(String(hours));
      const minutes = Math.max(5, Math.round(unit === "h" ? n * 60 : n));
      if (minutes !== s.interval_minutes) patch.mutate({ name: s.name, body: { interval_minutes: minutes } });
    };
    return (
      <li className="grid grid-cols-[1fr_auto_auto] items-center gap-x-4 border-b border-line py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-fg">{SCHEDULE_LABEL[s.job_type] ?? JOB_LABEL[s.job_type] ?? s.name}</p>
          <p className="truncate text-xs text-fg-3" title={s.job_type === "sync_full" ? "Walks the whole starred list; detects unstars and renames" : undefined}>
            {s.enabled ? (s.next_run_at ? `Next ${timeAgo(s.next_run_at)}` : "Scheduled") : "Paused"}
            {s.last_run_at ? ` · last ran ${timeAgo(s.last_run_at)}` : " · never ran"}
          </p>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-fg-3">
          every
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} inputMode="decimal" aria-label={`Interval for ${s.name} in ${unit}`} className="!h-7 !w-16 text-right text-xs" />
          {unit}
        </label>
        <Switch checked={s.enabled} onChange={(enabled) => patch.mutate({ name: s.name, body: { enabled } })} label={`Enable ${s.name}`} />
      </li>
    );
  };
  return (
    <ul className="mt-1">
      {(schedules.data ?? []).map((s) => (
        <Row key={s.name} s={s} />
      ))}
    </ul>
  );
}

