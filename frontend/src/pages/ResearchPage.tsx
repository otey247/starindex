import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, HardDrive, ListChecks, Play, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useCollections, useRepo, useSettings } from "@/api/queries";
import type { ResearchCapability, RouteOverride } from "@/api/types";
import { ModelPicker, ProviderErrorPanel, UsageLine, useCloudGate, useProviderName } from "@/components/ai/ModelControls";
import { CitedAnswer, EvidenceAside, ReportActions, ResearchFacts, TraceTimeline, WarningsBlock } from "@/components/ai/ReportView";
import { useAiStream } from "@/components/ai/useAiStream";
import { Button, Chip, ProgressBar, Select, Textarea } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDuration, timeAgo } from "@/lib/format";
import { toast } from "@/state/stores";

const CAPABILITIES: { value: ResearchCapability; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Let the planner choose the approach from the question." },
  { value: "discovery", label: "Repository discovery", hint: "Find repositories relevant to an objective and explain the connection." },
  { value: "comparison", label: "Capability comparison", hint: "Compare features, APIs, frameworks, deployment models and trade-offs." },
  { value: "composition", label: "Solution composition", hint: "Combine repositories into an application with an integration architecture." },
  { value: "gap_analysis", label: "Gap analysis", hint: "Missing components, unsupported assumptions, compatibility and licensing constraints." },
  { value: "scouting", label: "Technology scouting", hint: "Spot older or inactive projects and better-maintained alternatives you already starred." },
  { value: "relationships", label: "Relationship discovery", hint: "Shared dependencies, technologies, patterns and overlapping functions." },
];

const EXAMPLES = [
  "Using only repositories I have starred, design a local-first AI search platform with agentic capabilities. Recommend a stack, explain which repositories contribute which components, identify incompatible licenses, and provide an implementation plan.",
  "Which of my starred agent frameworks support MCP, and how do their orchestration models differ?",
  "Find stale projects in my 3D and rendering stars and suggest better-maintained alternatives I already starred.",
];

function useElapsed(startedAt: number | null, running: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  return startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : 0;
}

export default function ResearchPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const settings = useSettings();
  const collections = useCollections();
  const repoId = Number(params.get("repo")) || null;
  const scopedRepo = useRepo(repoId ?? Number.NaN);
  const [question, setQuestion] = useState(params.get("q") ?? "");
  const [capability, setCapability] = useState<ResearchCapability>((params.get("capability") as ResearchCapability) ?? "auto");
  const [collectionId, setCollectionId] = useState<number | null>(Number(params.get("collection")) || null);
  const [route, setRoute] = useState<RouteOverride | null>(null);
  const gate = useCloudGate(route);
  const stream = useAiStream();
  const s = stream.state;
  const running = s.phase === "running";
  const elapsed = useElapsed(s.startedAt, running);
  const reportRef = useRef<HTMLDivElement>(null);
  const autoRan = useRef(false);
  const providerName = useProviderName();

  const recent = useQuery({ queryKey: ["reports", { kind: "research", limit: 8 }], queryFn: () => api.reports({ kind: "research", limit: 8 }) });
  const limits = s.status?.limits?.max_tool_calls ? { ...settings.data?.research, ...s.status.limits } as NonNullable<typeof settings.data>["research"] : settings.data?.research;

  const run = (routeOverride?: RouteOverride | null) => {
    const q = question.trim();
    if (!q || running) return;
    const useRoute = routeOverride === undefined ? route : routeOverride;
    gate.run((confirmCloud) => {
      stream
        .start((opts) =>
          api.streamResearch(
            { question: q, capability, repo_ids: repoId ? [repoId] : [], collection_id: collectionId, route: useRoute, confirm_cloud: confirmCloud },
            opts,
          ),
        )
        .then(() => qc.invalidateQueries({ queryKey: ["reports"] }));
      requestAnimationFrame(() => reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    });
  };

  useEffect(() => {
    if (s.phase === "error" && s.error?.kind === "confirmation_required") gate.run(() => run(), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.phase, s.error?.kind]);

  useEffect(() => {
    if (params.get("run") === "1" && !autoRan.current && question.trim()) {
      autoRan.current = true;
      const next = new URLSearchParams(params);
      next.delete("run");
      setParams(next, { replace: true });
      run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancel = () => {
    stream.cancel();
    if (s.reportId) api.cancelResearch(s.reportId).catch(() => undefined);
    toast.info("Research stopped.");
  };

  const repoNames = useMemo(() => {
    const map: Record<number, string> = {};
    for (const e of s.evidence) map[e.repo_id] = e.repo_full_name;
    for (const r of s.report?.repos ?? []) map[r.id] = r.full_name;
    return map;
  }, [s.evidence, s.report]);

  const toolCalls = s.steps.filter((st) => st.kind === "tool").length;
  const active = s.phase !== "idle";
  const cap = CAPABILITIES.find((c) => c.value === capability)!;

  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 pb-20 pt-6 md:px-8">
      <header className="max-w-[980px]">
        <h1 className="text-2xl font-semibold text-fg">Research</h1>
        <p className="mt-0.5 text-sm text-fg-3">
          A bounded, multi-step investigation that uses your local indexes as tools and reports with evidence you can open.
        </p>
      </header>

      <form
        className="mt-5 max-w-[980px]"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <div className="rounded-lg border border-line-strong bg-surface focus-within:border-accent">
          <Textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                run();
              }
            }}
            rows={question.length > 160 ? 4 : 3}
            placeholder="Describe what you want investigated across your starred repositories…"
            aria-label="Research question"
            data-search-input
            disabled={running}
            className="!resize-none !border-0 !bg-transparent !px-4 !py-3 !text-md focus:!outline-none"
          />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-line px-3 py-2">
            <Select value={capability} onChange={(e) => setCapability(e.target.value as ResearchCapability)} aria-label="Research capability" disabled={running}>
              {CAPABILITIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
            <Select
              value={collectionId ?? ""}
              onChange={(e) => setCollectionId(e.target.value ? Number(e.target.value) : null)}
              aria-label="Limit to a collection"
              disabled={running}
            >
              <option value="">Whole library</option>
              {(collections.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  Collection: {c.name}
                </option>
              ))}
            </Select>
            <ModelPicker value={route} onChange={setRoute} disabled={running} />
            <div className="ml-auto flex items-center gap-2">
              {running ? (
                <Button key="stop" icon={<Square className="size-3" />} onClick={cancel}>
                  Stop
                </Button>
              ) : (
                <Button key="run" type="submit" variant="primary" disabled={!question.trim()} icon={<Play className="size-3.5" />}>
                  Run research
                </Button>
              )}
            </div>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-6 gap-y-1 text-xs text-fg-4">
          <span className="min-w-0">{cap.hint}</span>
          {limits && (
            <span className="mono shrink-0" title="Hard limits enforced by the orchestrator. Change them in Settings → Models.">
              ≤ {limits.max_tool_calls} tool calls · {limits.max_seconds}s · {Math.round(limits.max_context_chars / 1000)}k chars context
            </span>
          )}
        </div>
        {repoId && (
          <div className="mt-2 flex items-center gap-2 text-sm text-fg-3">
            Focused on
            <Chip
              onRemove={() => {
                const next = new URLSearchParams(params);
                next.delete("repo");
                setParams(next, { replace: true });
              }}
            >
              {scopedRepo.data?.full_name ?? `repository #${repoId}`}
            </Chip>
          </div>
        )}
      </form>

      {!active ? (
        <div className="mt-9 grid max-w-[1180px] gap-x-12 gap-y-8 grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] animate-rise">
          <section>
            <h2 className="label">Example questions</h2>
            <ul className="mt-2 flex flex-col">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button type="button" onClick={() => setQuestion(ex)} className="w-full cursor-pointer border-b border-line py-3 text-left text-base leading-relaxed text-fg-2 hover:text-fg">
                    {ex}
                  </button>
                </li>
              ))}
            </ul>
            <h2 className="label mt-8">What a report contains</h2>
            <ul className="mt-2 grid gap-x-8 gap-y-1.5 text-sm text-fg-3 sm:grid-cols-2">
              {[
                "Recommended repositories and why they fit",
                "Component-level architecture",
                "Dependencies and integration boundaries",
                "Evidence from indexed repository content",
                "Licensing and maintenance considerations",
                "Technical uncertainties and unknowns",
                "Links to repository and file sources",
                "A practical implementation plan",
              ].map((line) => (
                <li key={line} className="flex items-baseline gap-2">
                  <span className="size-1 shrink-0 translate-y-[-2px] rounded-full bg-fg-4" aria-hidden />
                  {line}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <div className="flex items-center justify-between">
              <h2 className="label">Recent research</h2>
              <Link to="/collections?tab=reports" className="text-xs text-fg-3 hover:text-fg">
                All reports
              </Link>
            </div>
            {recent.data && recent.data.length > 0 ? (
              <ul className="mt-1.5 flex flex-col">
                {recent.data.map((r) => (
                  <li key={r.id}>
                    <Link to={`/reports/${r.id}`} className="group block border-b border-line py-2">
                      <span className="line-clamp-1 text-base font-medium text-fg group-hover:underline">{r.title}</span>
                      <span className="mt-0.5 flex items-center gap-2 text-xs text-fg-4">
                        <span>{timeAgo(r.created_at)}</span>
                        {r.model && <span className="mono truncate">{r.model}</span>}
                        {r.saved && <span className="text-fg-3">saved</span>}
                        {r.status !== "completed" && <span className="text-fg-3">{r.status}</span>}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 text-sm text-fg-4">Finished research runs are kept here. Save the ones worth keeping.</p>
            )}
          </section>
        </div>
      ) : (
        <div ref={reportRef} className="mt-7 grid scroll-mt-16 gap-x-10 gap-y-8 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px]">
          <section className="min-w-0" aria-label="Research report" aria-busy={running}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line pb-2.5 text-xs text-fg-3">
              <span className="inline-flex items-center gap-1.5 font-medium text-fg-2">
                {s.status?.is_cloud ? <Cloud className="size-3.5" /> : <HardDrive className="size-3.5" />}
                {s.status?.provider_name || providerName(s.status?.provider_id) || gate.effective.providerName || "—"}
              </span>
              {(s.status?.model ?? gate.effective.model) && <span className="mono">{s.status?.model ?? gate.effective.model}</span>}
              <span className="tnum">
                {toolCalls}
                {limits ? ` / ${limits.max_tool_calls}` : ""} tool calls
              </span>
              <span className="tnum">{s.evidence.length} evidence</span>
              {running && <span className="tnum">{formatDuration(elapsed)}</span>}
              {running && <span className="text-fg-2">{s.status?.message ?? "Working…"}</span>}
              {s.phase === "cancelled" && <span className="text-fg-2">Stopped</span>}
              {s.report && <UsageLine usage={s.report.usage} className="ml-auto" />}
            </div>
            {running && <ProgressBar className="mt-0" label="Research in progress" value={limits ? Math.min(96, (toolCalls / limits.max_tool_calls) * 80 + (s.text ? 16 : 0)) : null} />}

            {s.plan.length > 0 && !s.text && (
              <div className="mt-5 animate-rise">
                <h2 className="label flex items-center gap-1.5">
                  <ListChecks className="size-3" /> Plan
                </h2>
                <ol className="mt-2 flex flex-col gap-1.5">
                  {s.plan.map((step, i) => (
                    <li key={i} className="flex gap-3 text-base text-fg-2">
                      <span className="mono w-4 shrink-0 text-right text-xs leading-[1.375rem] text-fg-4">{i + 1}</span>
                      {step}
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {!s.text && s.steps.length > 0 && (
              <div className="mt-6">
                <h2 className="label mb-3">Progress</h2>
                <TraceTimeline steps={s.steps} live={running} repoNames={repoNames} />
              </div>
            )}

            {s.text && (
              <div className={cn("mt-5 max-w-[900px]")}>
                {s.report?.title && <h2 className="mb-4 text-2xl font-semibold leading-tight text-fg">{s.report.title}</h2>}
                <CitedAnswer text={s.text} evidence={s.evidence} streaming={running} />
              </div>
            )}

            {s.phase === "error" && s.error && (
              <ProviderErrorPanel
                className="mt-5 max-w-[900px]"
                error={s.error}
                alternatives={s.alternatives}
                onRetry={() => run()}
                onSwitch={(opt) => {
                  const next = { provider_id: opt.provider_id, model: opt.default_model ?? "" };
                  setRoute(next);
                  run(next);
                }}
              />
            )}

            {s.report && s.phase === "done" && (
              <div className="max-w-[900px]">
                <WarningsBlock warnings={s.report.warnings} unsupported={s.report.unsupported} />
                <ResearchFacts data={s.report.data} />
                <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
                  <ReportActions report={s.report} onChanged={(report) => stream.setState((st) => ({ ...st, report }))} />
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/reports/${s.report!.id}`)}>
                    Open report page
                  </Button>
                </div>
              </div>
            )}

            {!running && !s.text && s.phase === "cancelled" && (
              <p className="mt-5 text-sm text-fg-3">The run was stopped before a report was written. The steps completed so far are in the trace.</p>
            )}
          </section>

          <EvidenceAside evidence={s.evidence} steps={s.steps} live={running} showTrace repoNames={repoNames} text={s.text} className="xl:sticky xl:top-4 xl:max-h-[calc(100dvh-5rem)] xl:overflow-y-auto xl:border-l xl:border-line xl:pl-6" />
        </div>
      )}
      {gate.dialog}
      <span className="sr-only" aria-live="polite">
        {running ? "Research running" : s.phase === "done" ? "Research complete" : ""}
      </span>
    </div>
  );
}
