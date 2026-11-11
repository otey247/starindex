import { Link } from "react-router-dom";
import { Braces, FileCode2, FileText, GitCommitHorizontal, Info, ListTree, Package, ShieldAlert } from "lucide-react";
import type { Evidence, EvidenceClass } from "@/api/types";
import { Tooltip } from "@/components/ui";
import { cn } from "@/lib/cn";
import { lineRange, shortSha, timeAgo } from "@/lib/format";
import { cleanSnippet } from "@/lib/highlight";
import { useEvidenceViewer } from "@/state/stores";

const CLASS_TEXT: Record<EvidenceClass, { label: string; help: string }> = {
  observed: { label: "Observed", help: "Read directly from repository metadata or indexed source files." },
  documented: { label: "Documented", help: "Stated by the project's own documentation. A claim, not proof of behaviour." },
  inferred: { label: "Inferred", help: "A conclusion drawn from several sources." },
  unverified: { label: "Unverified", help: "No indexed evidence supports this." },
};

/** Typographic evidence-class label: mono caps with a glyph, no colour coding. */
export function EvidenceClassLabel({ value, className }: { value: EvidenceClass; className?: string }) {
  const meta = CLASS_TEXT[value] ?? CLASS_TEXT.unverified;
  const glyph = value === "observed" ? "●" : value === "documented" ? "◐" : value === "inferred" ? "○" : "◌";
  return (
    <Tooltip content={meta.help} side="top">
      <span className={cn("label inline-flex cursor-help items-center gap-1 !text-fg-3", className)}>
        <span aria-hidden className="text-[9px] leading-none text-fg-2">
          {glyph}
        </span>
        {meta.label}
      </span>
    </Tooltip>
  );
}

export function EvidenceLegend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {(Object.keys(CLASS_TEXT) as EvidenceClass[]).map((c) => (
        <EvidenceClassLabel key={c} value={c} />
      ))}
    </div>
  );
}

export function kindIcon(kind: string) {
  switch (kind) {
    case "code":
    case "example":
      return <FileCode2 className="size-3.5" aria-hidden />;
    case "manifest":
    case "dependency":
      return <Package className="size-3.5" aria-hidden />;
    case "config":
    case "api":
      return <Braces className="size-3.5" aria-hidden />;
    case "metadata":
      return <Info className="size-3.5" aria-hidden />;
    case "activity":
      return <GitCommitHorizontal className="size-3.5" aria-hidden />;
    default:
      return <FileText className="size-3.5" aria-hidden />;
  }
}

/** `path  L10–24  @abc1234` */
export function SourceRef({
  path,
  start,
  end,
  sha,
  className,
}: {
  path?: string | null;
  start?: number | null;
  end?: number | null;
  sha?: string | null;
  className?: string;
}) {
  return (
    <span className={cn("mono inline-flex min-w-0 max-w-full items-baseline gap-1.5 text-xs", className)}>
      <span className="truncate">{path ?? "repository metadata"}</span>
      {start ? <span className="shrink-0 text-fg-4">{lineRange(start, end)}</span> : null}
      {sha ? <span className="shrink-0 text-fg-4">@{shortSha(sha)}</span> : null}
    </span>
  );
}

function EvidenceCard({ ev, active }: { ev: Evidence; active?: boolean }) {
  const open = useEvidenceViewer((s) => s.open);
  return (
    <button
      type="button"
      id={`evidence-${ev.ref}`}
      onClick={() => open({ type: "evidence", evidence: ev })}
      className={cn(
        "group block w-full cursor-pointer rounded-md px-2.5 py-2 text-left transition-colors hover:bg-hover",
        active && "bg-hover",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="mono shrink-0 rounded-[4px] border border-line-strong bg-raised px-1 text-[10.5px] font-medium leading-[16px] text-accent">
          {ev.ref}
        </span>
        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-fg-3">
          {kindIcon(ev.kind)}
          <SourceRef path={ev.path ?? ev.title} start={ev.start_line} end={ev.end_line} className="text-fg-2 group-hover:text-fg" />
        </span>
        {ev.injection_flag && (
          <Tooltip content="This passage contains text that looks like instructions to an AI. It was treated strictly as data." side="left">
            <ShieldAlert className="size-3.5 shrink-0 text-warn" aria-label="Possible prompt injection" />
          </Tooltip>
        )}
      </div>
      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-fg-3">{cleanSnippet(ev.text.slice(0, 400))}</p>
      <div className="mt-1 flex items-center gap-3">
        <EvidenceClassLabel value={ev.evidence_class as EvidenceClass} />
        {ev.indexed_at && <span className="label !normal-case !tracking-normal">indexed {timeAgo(ev.indexed_at)}</span>}
      </div>
    </button>
  );
}

/** Evidence grouped by repository. */
export function EvidenceList({
  evidence,
  activeRef,
  emptyText = "No evidence was selected.",
}: {
  evidence: Evidence[];
  activeRef?: string | null;
  emptyText?: string;
}) {
  if (evidence.length === 0) {
    return <p className="px-1 py-3 text-sm text-fg-3">{emptyText}</p>;
  }
  const groups = new Map<number, { name: string; items: Evidence[] }>();
  for (const ev of evidence) {
    const g = groups.get(ev.repo_id) ?? { name: ev.repo_full_name, items: [] };
    g.items.push(ev);
    groups.set(ev.repo_id, g);
  }
  return (
    <div className="flex flex-col gap-4">
      {[...groups.entries()].map(([repoId, group]) => (
        <section key={repoId}>
          <header className="mb-1 flex items-center gap-2 px-2.5">
            <ListTree className="size-3.5 text-fg-4" aria-hidden />
            <Link to={`/repo/${repoId}`} className="min-w-0 truncate text-sm font-semibold text-fg hover:underline">
              {group.name}
            </Link>
            <span className="text-xs text-fg-4 tnum">{group.items.length}</span>
          </header>
          <div className="flex flex-col">
            {group.items.map((ev) => (
              <EvidenceCard key={ev.ref} ev={ev} active={activeRef === ev.ref} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
