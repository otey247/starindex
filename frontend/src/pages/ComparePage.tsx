import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, BookmarkPlus, Scale, Sparkles, Square, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import type { RouteOverride } from "@/api/types";
import { CompareTable, narrativeOnly, SharedLists } from "@/components/ai/CompareTable";
import { ModelPicker, ProviderErrorPanel, UsageLine, useCloudGate } from "@/components/ai/ModelControls";
import { CitedAnswer, ReportActions } from "@/components/ai/ReportView";
import { useAiStream } from "@/components/ai/useAiStream";
import { RepoPicker } from "@/components/repo/RepoPicker";
import { Button, EmptyState, ErrorState, Input, ProgressBar, SectionHeader, Skeleton } from "@/components/ui";
import { MAX_COMPARE, toast, useCompareTray } from "@/state/stores";

export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tray = useCompareTray();
  const ids = useMemo(
    () =>
      [...new Set((params.get("ids") ?? "").split(",").map(Number).filter((n) => Number.isInteger(n) && n > 0))].slice(0, MAX_COMPARE),
    [params],
  );
  const [focus, setFocus] = useState("");
  const [route, setRoute] = useState<RouteOverride | null>(null);
  const gate = useCloudGate(route);
  const stream = useAiStream();
  const s = stream.state;

  // Arriving with an empty URL but a filled tray: adopt the tray.
  useEffect(() => {
    if (ids.length === 0 && tray.ids.length >= 2) setParams({ ids: tray.ids.join(",") }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setIds = (next: number[]) => {
    stream.reset();
    setParams(next.length ? { ids: next.join(",") } : {}, { replace: true });
  };

  const comparison = useQuery({
    queryKey: ["compare", ids],
    queryFn: () => api.compare(ids),
    enabled: ids.length >= 2,
    staleTime: 60_000,
  });
  const partial = useQuery({
    queryKey: ["compare-single", ids],
    queryFn: () => api.listRepos({ filters: { repo_ids: ids, star_state: "all" }, limit: 10 }),
    enabled: ids.length === 1,
  });

  const save = useMutation({
    mutationFn: () => api.saveComparison(ids),
    onSuccess: (report) => {
      qc.invalidateQueries({ queryKey: ["reports"] });
      toast.success("Comparison saved.", { label: "Open", onClick: () => navigate(`/reports/${report.id}`) });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const narrate = (routeOverride?: RouteOverride | null) => {
    const useRoute = routeOverride === undefined ? route : routeOverride;
    gate.run((confirmCloud) => {
      stream
        .start((opts) => api.streamCompareNarrative({ repo_ids: ids, focus: focus.trim(), route: useRoute, confirm_cloud: confirmCloud }, opts))
        .then(() => qc.invalidateQueries({ queryKey: ["reports"] }));
    });
  };

  const running = s.phase === "running";
  const data = comparison.data;
  const evidence = s.evidence.length ? s.evidence : (data?.evidence ?? []);

  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 pb-20 pt-6 md:px-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Compare</h1>
          <p className="mt-0.5 text-sm text-fg-3">Capabilities, architecture, maturity, dependencies and licensing, side by side. The table needs no model.</p>
        </div>
        {data && (
          <Button icon={<BookmarkPlus className="size-3.5" />} loading={save.isPending} onClick={() => save.mutate()}>
            Save comparison
          </Button>
        )}
      </header>

      <div className="mt-5 flex flex-wrap items-center gap-2 border-b border-line pb-4">
        {(data?.repos ?? partial.data?.items ?? []).map((repo) => (
          <span key={repo.id} className="inline-flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-raised pl-2.5 pr-1.5 text-sm">
            <span className="max-w-[240px] truncate">
              <span className="text-fg-3">{repo.owner}/</span>
              <span className="font-medium text-fg">{repo.name}</span>
            </span>
            <button type="button" aria-label={`Remove ${repo.full_name}`} onClick={() => setIds(ids.filter((x) => x !== repo.id))} className="cursor-pointer text-fg-4 hover:text-fg">
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        {ids.length < MAX_COMPARE && (
          <RepoPicker
            className="w-[300px] max-w-full"
            excludeIds={ids}
            placeholder={ids.length === 0 ? "Add the first repository…" : "Add another repository…"}
            onPick={(repo) => setIds([...ids, repo.id])}
          />
        )}
        <span className="ml-auto text-xs text-fg-4 tnum">
          {ids.length} / {MAX_COMPARE}
        </span>
      </div>

      {ids.length < 2 ? (
        <EmptyState icon={<ArrowLeftRight />} title={ids.length === 0 ? "Pick repositories to compare" : "Add one more repository"}>
          Choose two to six repositories from your library. You can also select them in Search or Library with the compare action, or press{" "}
          <span className="mono text-fg-2">x</span> on a focused result.
        </EmptyState>
      ) : comparison.isLoading ? (
        <div className="mt-6 flex flex-col gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : comparison.isError ? (
        <ErrorState className="mt-6" title="Could not build the comparison" message={errorMessage(comparison.error)} onRetry={() => comparison.refetch()} />
      ) : data ? (
        <div className="animate-rise">
          <div className="mt-4">
            <CompareTable comparison={data} onRemove={(id) => setIds(ids.filter((x) => x !== id))} />
          </div>

          {data.license_notes.length > 0 && (
            <section className="mt-8 max-w-[900px]">
              <SectionHeader title="License notes" />
              <ul className="mt-1.5 flex flex-col gap-1.5">
                {data.license_notes.map((note, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm leading-relaxed text-fg-2">
                    <Scale className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
                    {note}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="mt-8">
            <SharedLists comparison={data} />
          </div>

          <section className="mt-10 max-w-[980px] border-t border-line pt-6" aria-label="AI narrative">
            <SectionHeader title="AI narrative">{s.report && <UsageLine usage={s.report.usage} />}</SectionHeader>
            <p className="mt-1 text-sm text-fg-3">
              Optional. A model reads the indexed evidence for these repositories and writes up trade-offs. It is saved as a comparison report.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Input
                value={focus}
                onChange={(e) => setFocus(e.target.value)}
                placeholder='Angle (optional), e.g. "for a local RAG stack"'
                aria-label="Comparison focus"
                className="max-w-[340px]"
                disabled={running}
              />
              <ModelPicker value={route} onChange={setRoute} disabled={running} />
              {running ? (
                <Button icon={<Square className="size-3" />} onClick={stream.cancel}>
                  Stop
                </Button>
              ) : (
                <Button variant="primary" icon={<Sparkles className="size-3.5" />} onClick={() => narrate()}>
                  {s.text ? "Regenerate narrative" : "Generate narrative"}
                </Button>
              )}
            </div>
            {running && !s.text && (
              <div className="mt-4 max-w-[320px]">
                <ProgressBar label="Generating narrative" />
                <p className="mt-1.5 text-xs text-fg-3">{s.status?.message ?? "Reading evidence…"}</p>
              </div>
            )}
            {s.text && (
              <div className="mt-5">
                <CitedAnswer text={narrativeOnly(s.text)} evidence={evidence} streaming={running} />
              </div>
            )}
            {s.phase === "error" && s.error && (
              <ProviderErrorPanel
                className="mt-4"
                error={s.error}
                alternatives={s.alternatives}
                onRetry={() => narrate()}
                onSwitch={(opt) => {
                  const next = { provider_id: opt.provider_id, model: opt.default_model ?? "" };
                  setRoute(next);
                  narrate(next);
                }}
              />
            )}
            {s.report && s.phase === "done" && (
              <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
                <ReportActions report={s.report} onChanged={(report) => stream.setState((st) => ({ ...st, report }))} />
                <Link to={`/reports/${s.report.id}`} className="text-xs text-fg-3 hover:text-fg">
                  Open report page
                </Link>
              </div>
            )}
          </section>
        </div>
      ) : null}
      {gate.dialog}
    </div>
  );
}
