import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FolderOpen, ShieldAlert } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import type { Evidence, EvidenceClass, EvidenceDetail } from "@/api/types";
import { Drawer, ErrorState, Skeleton } from "@/components/ui";
import { formatDateTime, lineRange, shortSha } from "@/lib/format";
import { useEvidenceViewer } from "@/state/stores";
import { EvidenceClassLabel, kindIcon } from "./EvidenceBits";

/** Line-numbered excerpt; the cited range is set in full-strength text, context is dimmed. */
export function Excerpt({
  before,
  text,
  after,
  startLine,
  contextStart,
}: {
  before?: string;
  text: string;
  after?: string;
  startLine?: number | null;
  contextStart?: number | null;
}) {
  const beforeLines = before ? before.replace(/\n$/, "").split("\n") : [];
  const mainLines = text.replace(/\n$/, "").split("\n");
  const afterLines = after ? after.replace(/\n$/, "").split("\n") : [];
  const numbered = typeof startLine === "number" && startLine > 0;
  const first = numbered ? (contextStart ?? startLine - beforeLines.length) : 1;
  const ref = useRef<HTMLDivElement>(null);
  // Bring the cited lines into view when there is a lot of leading context.
  useEffect(() => {
    const first = ref.current?.querySelector("tr[data-cited]");
    if (first && beforeLines.length > 6) first.scrollIntoView({ block: "center" });
  }, [text, beforeLines.length]);
  const rows = [
    ...beforeLines.map((l) => ({ l, cited: false })),
    ...mainLines.map((l) => ({ l, cited: true })),
    ...afterLines.map((l) => ({ l, cited: false })),
  ];
  return (
    <div ref={ref} className="overflow-x-auto rounded-md border border-line bg-[#0a0b0c] py-2" data-testid="excerpt">
      <table className="mono w-full border-collapse text-xs leading-[1.6]">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} data-cited={row.cited || undefined} className={row.cited ? "bg-white/[0.035]" : undefined}>
              {numbered && (
                <td className="w-px select-none whitespace-nowrap border-r border-line px-2.5 text-right align-top text-fg-4 tnum">{first + i}</td>
              )}
              <td className={`whitespace-pre-wrap break-words px-3 ${row.cited ? "text-fg" : "text-fg-4"}`}>{row.l || " "}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Body({ ev, detail }: { ev: Evidence; detail?: EvidenceDetail }) {
  const close = useEvidenceViewer((s) => s.close);
  const e = detail ?? ev;
  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <Link to={`/repo/${e.repo_id}`} onClick={close} className="text-lg font-semibold text-fg hover:underline">
          {e.repo_full_name}
        </Link>
        <div className="mt-1 flex items-center gap-1.5 text-fg-3">
          {kindIcon(e.kind)}
          <span className="mono min-w-0 break-all text-sm text-fg-2">{e.path ?? e.title}</span>
        </div>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5 text-sm">
        <dt className="text-fg-3">Evidence</dt>
        <dd>
          <EvidenceClassLabel value={e.evidence_class as EvidenceClass} />
        </dd>
        {e.start_line ? (
          <>
            <dt className="text-fg-3">Lines</dt>
            <dd className="mono text-xs text-fg-2">{lineRange(e.start_line, e.end_line)}</dd>
          </>
        ) : null}
        {e.commit_sha ? (
          <>
            <dt className="text-fg-3">Commit</dt>
            <dd className="mono text-xs text-fg-2" title={e.commit_sha}>
              {shortSha(e.commit_sha)}
            </dd>
          </>
        ) : null}
        <dt className="text-fg-3">Last indexed</dt>
        <dd className="text-fg-2">{formatDateTime(e.indexed_at)}</dd>
      </dl>

      {e.injection_flag && (
        <p className="flex items-start gap-2 text-sm leading-relaxed text-fg-2">
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          This passage contains text that reads like instructions to an AI model. StarIndex treats repository content strictly
          as data; it was not followed.
        </p>
      )}

      <Excerpt
        before={detail?.context_before}
        text={e.text}
        after={detail?.context_after}
        startLine={e.start_line}
        contextStart={detail?.context_start_line}
      />

      <div className="flex flex-wrap gap-2">
        {e.html_url && (
          <a
            href={e.html_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay"
          >
            <ExternalLink className="size-3.5" aria-hidden />
            Open on GitHub
          </a>
        )}
        {e.document_id ? (
          <Link
            to={`/repo/${e.repo_id}?tab=files&doc=${e.document_id}${e.start_line ? `&line=${e.start_line}` : ""}`}
            onClick={close}
            className="inline-flex h-8 items-center gap-1.5 rounded-sm px-3 text-sm font-medium text-fg-2 hover:bg-hover hover:text-fg"
          >
            <FolderOpen className="size-3.5" aria-hidden />
            Open in indexed files
          </Link>
        ) : null}
      </div>

      {e.evidence_class === "documented" && (
        <p className="text-xs leading-relaxed text-fg-4">
          Documentation describes what a project says it does. Treat it as the project's claim rather than verified behaviour.
        </p>
      )}
    </div>
  );
}

/** Global citation viewer. Opened from citation chips, match snippets and evidence lists. */
export function EvidenceViewer() {
  const target = useEvidenceViewer((s) => s.target);
  const close = useEvidenceViewer((s) => s.close);
  const chunkId = target?.type === "chunk" ? target.chunkId : target?.type === "evidence" ? target.evidence.chunk_id : null;
  const detail = useQuery({
    queryKey: ["evidence", chunkId],
    queryFn: () => api.evidenceChunk(chunkId as number),
    enabled: typeof chunkId === "number",
    staleTime: 60_000,
  });

  const title =
    target?.type === "evidence" ? `Evidence ${target.evidence.ref}` : target?.type === "chunk" ? (target.label ?? "Matched passage") : "Evidence";

  return (
    <Drawer open={target !== null} onClose={close} title={title} width={620}>
      {target?.type === "evidence" ? (
        <Body ev={target.evidence} detail={detail.data ? { ...detail.data, ref: target.evidence.ref } : undefined} />
      ) : detail.isLoading ? (
        <div className="flex flex-col gap-3 p-4">
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : detail.isError ? (
        <div className="p-4">
          <ErrorState title="Could not load this passage" message={errorMessage(detail.error)} onRetry={() => detail.refetch()} />
        </div>
      ) : detail.data ? (
        <Body ev={detail.data} detail={detail.data} />
      ) : null}
    </Drawer>
  );
}
