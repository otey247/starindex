import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookmarkCheck, BookmarkPlus, Check, CircleDashed, Copy, Download, FolderPlus, ListChecks, Search as SearchIcon, ShieldAlert, Trash2, TriangleAlert, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { useCollections } from "@/api/queries";
import type { Evidence, Report, TraceStep, UnsupportedCitation } from "@/api/types";
import { EvidenceLegend, EvidenceList } from "@/components/evidence/EvidenceBits";
import { Markdown } from "@/components/Markdown";
import { Button, Menu, Tabs, type MenuItem } from "@/components/ui";
import { cn } from "@/lib/cn";
import { extractCitationRefs } from "@/lib/citations";
import { formatMs } from "@/lib/format";
import { toast, useEvidenceViewer } from "@/state/stores";

/** Answer markdown whose [E#] citations open the evidence viewer. */
export function CitedAnswer({
  text,
  evidence,
  streaming,
  compact,
  onCite,
}: {
  text: string;
  evidence: Evidence[];
  streaming?: boolean;
  compact?: boolean;
  onCite?: (ref: string) => void;
}) {
  const open = useEvidenceViewer((s) => s.open);
  const byRef = useMemo(() => new Map(evidence.map((e) => [e.ref, e])), [evidence]);
  const known = useMemo(() => new Set(byRef.keys()), [byRef]);
  return (
    <Markdown
      compact={compact}
      streaming={streaming}
      knownRefs={known}
      onCite={(ref) => {
        const ev = byRef.get(ref);
        if (ev) open({ type: "evidence", evidence: ev });
        onCite?.(ref);
      }}
    >
      {text}
    </Markdown>
  );
}

const STEP_ICON: Record<TraceStep["kind"], React.ReactNode> = {
  plan: <ListChecks className="size-3.5" />,
  tool: <Wrench className="size-3.5" />,
  note: <CircleDashed className="size-3.5" />,
  synthesis: <Check className="size-3.5" />,
  limit: <TriangleAlert className="size-3.5 text-warn" />,
  warning: <ShieldAlert className="size-3.5 text-warn" />,
};

function formatArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args ?? {})) {
    if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    const text = typeof v === "string" ? `"${v}"` : JSON.stringify(v);
    parts.push(`${k}=${text.length > 90 ? `${text.slice(0, 88)}…` : text}`);
  }
  return parts.join("  ");
}

function describeRepos(ids: number[], names: Record<number, string>): string {
  const known = ids.map((id) => names[id]).filter(Boolean) as string[];
  const shown = known.slice(0, 4);
  const rest = ids.length - shown.length;
  if (shown.length === 0) return `${ids.length} repositor${ids.length === 1 ? "y" : "ies"} examined`;
  return `${shown.join(", ")}${rest > 0 ? ` +${rest} more` : ""}`;
}

/**
 * Research Trace: the searches performed, tools used, repositories examined and evidence
 * selected. This is an activity log — it never contains the model's private reasoning.
 */
export function TraceTimeline({ steps, live, repoNames = {} }: { steps: TraceStep[]; live?: boolean; repoNames?: Record<number, string> }) {
  if (steps.length === 0) {
    return <p className="px-1 py-3 text-sm text-fg-3">{live ? "Waiting for the first step…" : "No trace was recorded."}</p>;
  }
  return (
    <ol className="relative flex flex-col" data-testid="research-trace">
      {steps.map((step, i) => {
        const last = i === steps.length - 1;
        return (
          <li key={step.index} className="relative grid grid-cols-[20px_1fr] gap-x-2.5 pb-3.5 animate-rise">
            {!last && <span className="absolute left-[9.5px] top-5 h-[calc(100%-18px)] w-px bg-line" aria-hidden />}
            <span className={cn("mt-0.5 flex size-5 items-center justify-center rounded-full border border-line-strong bg-raised text-fg-3", !step.ok && "text-danger")}>
              {live && last && step.kind === "tool" ? <span className="size-1.5 rounded-full bg-accent animate-pulse-dot" /> : STEP_ICON[step.kind]}
            </span>
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 text-sm font-medium text-fg">
                  {step.title}
                  {!step.ok && <span className="ml-2 text-xs font-normal text-danger">failed</span>}
                </p>
                {step.duration_ms !== null && step.duration_ms !== undefined && (
                  <span className="mono shrink-0 text-2xs text-fg-4">{formatMs(step.duration_ms)}</span>
                )}
              </div>
              {step.tool && (
                <p className="mono mt-0.5 break-words text-xs text-fg-3">
                  <span className="text-accent">{step.tool}</span>
                  {Object.keys(step.arguments ?? {}).length > 0 && <span className="text-fg-3">({formatArgs(step.arguments)})</span>}
                </p>
              )}
              {step.summary && <p className="mt-0.5 text-xs leading-relaxed text-fg-3">{step.summary}</p>}
              {(step.repo_ids.length > 0 || step.evidence_refs.length > 0) && (
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-4">
                  {step.repo_ids.length > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <SearchIcon className="size-2.5" />
                      {describeRepos(step.repo_ids, repoNames)}
                    </span>
                  )}
                  {step.evidence_refs.length > 0 && <span className="mono">→ {step.evidence_refs.join(" ")}</span>}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function WarningsBlock({ warnings, unsupported }: { warnings: string[]; unsupported: UnsupportedCitation[] }) {
  if (warnings.length === 0 && unsupported.length === 0) return null;
  return (
    <section className="mt-6 border-t border-line pt-4" aria-label="Verification notes">
      <h3 className="label">Verification notes</h3>
      {warnings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-fg-2">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
              {w}
            </li>
          ))}
        </ul>
      )}
      {unsupported.length > 0 && (
        <>
          <p className="mt-3 text-sm text-fg-3">
            These statements were not backed by indexed evidence and should be treated as unverified:
          </p>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {unsupported.map((u, i) => (
              <li key={i} className="text-sm">
                <span className="text-fg-2">“{u.text}”</span>
                <span className="block text-xs text-fg-4">{u.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Right-hand panel: evidence grouped by repository, and the research trace. */
export function EvidenceAside({
  evidence,
  steps,
  live,
  showTrace,
  repoNames,
  text,
  className,
}: {
  evidence: Evidence[];
  steps?: TraceStep[];
  live?: boolean;
  showTrace?: boolean;
  repoNames?: Record<number, string>;
  text?: string;
  className?: string;
}) {
  const [tab, setTab] = useState<"evidence" | "trace">("evidence");
  const cited = useMemo(() => new Set(text ? extractCitationRefs(text) : []), [text]);
  const [onlyCited, setOnlyCited] = useState(false);
  const shown = onlyCited && cited.size ? evidence.filter((e) => cited.has(e.ref)) : evidence;
  return (
    <aside className={cn("min-w-0", className)} aria-label="Evidence">
      {showTrace ? (
        <Tabs
          label="Evidence and trace"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "evidence", label: "Evidence", count: evidence.length },
            { value: "trace", label: "Research trace", count: steps?.length ?? 0 },
          ]}
        />
      ) : (
        <div className="flex h-9 items-center justify-between border-b border-line">
          <h2 className="label">
            Evidence <span className="text-fg-4">{evidence.length}</span>
          </h2>
        </div>
      )}
      {tab === "evidence" || !showTrace ? (
        <div className="pt-3">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-2.5">
            <EvidenceLegend />
            {cited.size > 0 && cited.size < evidence.length && (
              <button type="button" onClick={() => setOnlyCited((v) => !v)} className="cursor-pointer text-xs text-fg-3 hover:text-fg" aria-pressed={onlyCited}>
                {onlyCited ? `Show all ${evidence.length}` : `Only cited (${cited.size})`}
              </button>
            )}
          </div>
          <EvidenceList evidence={shown} emptyText={live ? "Retrieving evidence…" : "No evidence was retrieved for this answer."} />
        </div>
      ) : (
        <div className="pt-4">
          <p className="mb-3 text-xs leading-relaxed text-fg-4">
            What the research run did: searches, tools, repositories examined and evidence selected. The model's private reasoning is not recorded.
          </p>
          <TraceTimeline steps={steps ?? []} live={live} repoNames={repoNames} />
        </div>
      )}
    </aside>
  );
}

/** Save / export / file / delete actions for a finished report. */
export function ReportActions({ report, onChanged, onDeleted }: { report: Report; onChanged?: (r: Report) => void; onDeleted?: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const collections = useCollections();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["reports"] });
    qc.invalidateQueries({ queryKey: ["report", report.id] });
    qc.invalidateQueries({ queryKey: ["collections"] });
  };
  const patch = useMutation({
    mutationFn: (body: { saved?: boolean; collection_ids?: number[] }) => api.patchReport(report.id, body),
    onSuccess: (r, vars) => {
      invalidate();
      onChanged?.(r);
      if (vars.saved !== undefined) toast.success(vars.saved ? "Saved to your reports." : "Removed from saved reports.", vars.saved ? { label: "Open reports", onClick: () => navigate("/collections?tab=reports") } : undefined);
      else toast.success("Collections updated.");
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteReport(report.id),
    onSuccess: () => {
      invalidate();
      toast.success("Report deleted.");
      onDeleted?.();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const member = report.collections.map((c) => c.id);
  const items: MenuItem[] = (collections.data ?? [])
    .filter((c) => c.kind === "manual")
    .map((c) => ({
      key: String(c.id),
      label: c.name,
      checked: member.includes(c.id),
      onSelect: () => patch.mutate({ saved: true, collection_ids: member.includes(c.id) ? member.filter((x) => x !== c.id) : [...member, c.id] }),
    }));
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Button
        size="sm"
        variant={report.saved ? "secondary" : "primary"}
        loading={patch.isPending}
        icon={report.saved ? <BookmarkCheck className="size-3.5" /> : <BookmarkPlus className="size-3.5" />}
        onClick={() => patch.mutate({ saved: !report.saved })}
      >
        {report.saved ? "Saved" : "Save report"}
      </Button>
      <Menu
        width={240}
        header={<span className="label">File in collection</span>}
        items={items.length ? items : [{ key: "none", label: "No manual collections yet", disabled: true, onSelect: () => undefined }]}
        trigger={(p) => (
          <Button size="sm" icon={<FolderPlus className="size-3.5" />} {...p}>
            Collection
          </Button>
        )}
      />
      <a
        href={api.reportExportUrl(report.id)}
        download
        className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-2.5 text-xs font-medium text-fg hover:bg-overlay"
      >
        <Download className="size-3.5" aria-hidden /> Markdown
      </a>
      <Button
        size="sm"
        variant="ghost"
        icon={<Copy className="size-3.5" />}
        onClick={() => {
          navigator.clipboard?.writeText(report.answer_md).then(
            () => toast.success("Copied as Markdown."),
            () => toast.error("Clipboard is not available."),
          );
        }}
      >
        Copy
      </Button>
      {onDeleted && (
        <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} loading={remove.isPending} onClick={() => remove.mutate()}>
          Delete
        </Button>
      )}
    </div>
  );
}
