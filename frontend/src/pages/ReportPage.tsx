import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Cloud, HardDrive } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { ApiError, errorMessage } from "@/api/errors";
import type { Comparison } from "@/api/types";
import { CompareTable, SharedLists } from "@/components/ai/CompareTable";
import { UsageLine } from "@/components/ai/ModelControls";
import { CitedAnswer, EvidenceAside, ReportActions, WarningsBlock } from "@/components/ai/ReportView";
import { RepoTitle } from "@/components/repo/RepoBits";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const KIND_LABEL: Record<string, string> = { ask: "Answer", research: "Research report", compare: "Comparison", usage_ideas: "Usage ideas" };

function asComparison(data: Record<string, unknown>): Comparison | null {
  const c = (data?.comparison ?? data) as Partial<Comparison> | undefined;
  if (c && Array.isArray(c.repos) && Array.isArray(c.rows)) {
    return { shared_topics: [], shared_dependencies: [], license_notes: [], evidence: [], report_id: null, ...c } as Comparison;
  }
  return null;
}

export default function ReportPage() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["report", id],
    queryFn: () => api.report(id),
    enabled: Number.isFinite(id),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2500 : false),
  });
  const report = query.data;
  const repoNames = useMemo(() => Object.fromEntries((report?.repos ?? []).map((r) => [r.id, r.full_name])), [report]);

  if (query.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[1100px] px-4 pt-8 md:px-8">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="mt-6 h-72 w-full" />
      </div>
    );
  }
  if (query.isError || !report) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 pt-10 md:px-8">
        {notFound ? (
          <EmptyState title="Report not found" action={<Button onClick={() => navigate("/collections?tab=reports")}>All reports</Button>}>
            It may have been deleted.
          </EmptyState>
        ) : (
          <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />
        )}
      </div>
    );
  }

  const comparison = report.kind === "compare" ? asComparison(report.data) : null;
  const evidence = report.evidence.length ? report.evidence : (comparison?.evidence ?? []);

  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 pb-20 pt-5 md:px-8">
      <Link to="/collections?tab=reports" className="mb-3 inline-flex items-center gap-1 text-xs text-fg-3 hover:text-fg">
        <ArrowLeft className="size-3" /> Reports
      </Link>
      <header className="border-b border-line pb-4">
        <p className="label">{KIND_LABEL[report.kind] ?? report.kind}</p>
        <h1 className="mt-1 max-w-[30ch] text-3xl font-semibold leading-tight tracking-[-0.02em] text-fg sm:max-w-[48ch]">{report.title}</h1>
        {report.question && report.question !== report.title && <p className="mt-2 max-w-[90ch] text-base leading-relaxed text-fg-3">{report.question}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-3">
          <span>{formatDateTime(report.created_at)}</span>
          {report.provider_id && (
            <span className="inline-flex items-center gap-1.5 text-fg-2">
              {["openai", "anthropic", "gemini", "azure_openai", "chatgpt_plan"].includes(report.provider_kind ?? "") ? <Cloud className="size-3.5" /> : <HardDrive className="size-3.5" />}
              {report.provider_id}
            </span>
          )}
          {report.model && <span className="mono">{report.model}</span>}
          <UsageLine usage={report.usage} />
          {report.status !== "completed" && <span className="font-medium text-fg-2">{report.status}</span>}
        </div>
        <div className="mt-4">
          <ReportActions
            report={report}
            onChanged={(r) => qc.setQueryData(["report", id], r)}
            onDeleted={() => navigate("/collections?tab=reports")}
          />
        </div>
      </header>

      <div className="mt-6 grid gap-x-10 gap-y-8 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="min-w-0">
          {report.status === "failed" && <ErrorState className="mb-5" title="This run failed" message={report.error ?? "No error message was recorded."} />}
          {comparison && (
            <div className="mb-8">
              <CompareTable comparison={comparison} />
              <div className="mt-8">
                <SharedLists comparison={comparison} />
              </div>
            </div>
          )}
          {report.answer_md ? (
            <div className="max-w-[900px]">
              <CitedAnswer text={report.answer_md} evidence={evidence} />
            </div>
          ) : !comparison && report.status === "running" ? (
            <p className="text-sm text-fg-3">This run is still in progress…</p>
          ) : !comparison ? (
            <p className="text-sm text-fg-3">No answer text was produced.</p>
          ) : null}
          <div className="max-w-[900px]">
            <WarningsBlock warnings={report.warnings} unsupported={report.unsupported} />
          </div>
          {report.repos.length > 0 && (
            <section className="mt-8 max-w-[900px] border-t border-line pt-4">
              <h2 className="label">Repositories in this report</h2>
              <ul className="mt-2 grid gap-x-8 gap-y-1 sm:grid-cols-2">
                {report.repos.map((repo) => (
                  <li key={repo.id} className="min-w-0">
                    <RepoTitle repo={repo} size="sm" />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </section>
        <EvidenceAside
          evidence={evidence}
          steps={report.trace}
          showTrace={report.kind === "research" || report.trace.length > 0}
          repoNames={repoNames}
          text={report.answer_md}
          className="xl:border-l xl:border-line xl:pl-6"
        />
      </div>
    </div>
  );
}
