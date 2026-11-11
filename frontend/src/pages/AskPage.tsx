import { ArrowUp, Cloud, FlaskConical, HardDrive, MessagesSquare, Settings2, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { useModelOptions, useRepo } from "@/api/queries";
import type { Evidence, Report, RouteOverride, SearchResponse } from "@/api/types";
import { ModelPicker, ProviderErrorPanel, UsageLine, useCloudGate, useProviderName } from "@/components/ai/ModelControls";
import { CitedAnswer, EvidenceAside, ReportActions } from "@/components/ai/ReportView";
import { useAiStream, type AiStreamState } from "@/components/ai/useAiStream";
import { Button, Chip, ProgressBar } from "@/components/ui";
import { cn } from "@/lib/cn";
import { stripHighlights } from "@/lib/highlight";

interface Turn {
  id: number;
  question: string;
  state: AiStreamState;
  fallbackEvidence?: Evidence[];
}

const STARTERS = [
  "Which of my starred repositories are relevant to AI governance and observability?",
  "What have I starred that could help build a Sims-style 3D house builder?",
  "Which projects offer semantic search without requiring a cloud service?",
  "Find Python libraries I starred that can process PDFs locally.",
];

/** When no model is configured, searching still works: show the passages we would have used. */
function searchToEvidence(res: SearchResponse): Evidence[] {
  const out: Evidence[] = [];
  for (const result of res.results) {
    for (const m of result.matches.slice(0, 2)) {
      out.push({
        ref: `E${out.length + 1}`,
        evidence_class: m.kind === "code" || m.kind === "manifest" ? "observed" : "documented",
        kind: (["readme", "doc", "manifest", "config", "api", "example", "code"].includes(m.kind) ? m.kind : "doc") as Evidence["kind"],
        repo_id: result.repo.id,
        repo_full_name: result.repo.full_name,
        title: m.heading || m.path,
        text: stripHighlights(m.snippet),
        path: m.path,
        start_line: m.start_line,
        end_line: m.end_line,
        commit_sha: m.commit_sha,
        indexed_at: m.indexed_at,
        html_url: m.html_url,
        chunk_id: m.chunk_id,
        document_id: m.document_id,
        injection_flag: false,
      });
    }
  }
  return out.slice(0, 16);
}

function TurnView({
  turn,
  live,
  onRetry,
  onSwitch,
  onReport,
  selected,
  onSelect,
}: {
  turn: Turn;
  live: boolean;
  onRetry: () => void;
  onSwitch: (route: RouteOverride) => void;
  onReport: (report: Report) => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const s = turn.state;
  const providerName = useProviderName();
  const running = s.phase === "running";
  const evidence = s.evidence.length ? s.evidence : (turn.fallbackEvidence ?? []);
  return (
    <article className={cn("border-b border-line py-6", !selected && "opacity-90")} onMouseDown={onSelect} data-testid="ask-turn">
      <h2 className="text-xl font-semibold leading-snug text-fg">{turn.question}</h2>

      {(s.status?.provider_id || running) && (
        <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-3">
          {s.status?.is_cloud ? <Cloud className="size-3.5" aria-hidden /> : <HardDrive className="size-3.5" aria-hidden />}
          {s.status?.provider_id && <span className="font-medium text-fg-2">{providerName(s.status.provider_id)}</span>}
          {s.status?.model && <span className="mono">{s.status.model}</span>}
          {running && <span>· {s.status?.message ?? "Retrieving evidence…"}</span>}
          {s.phase === "cancelled" && <span>· stopped</span>}
        </p>
      )}

      {running && !s.text && <ProgressBar className="mt-3 max-w-[280px]" label="Generating answer" />}

      {s.text && (
        <div className="mt-4">
          <CitedAnswer text={s.text} evidence={evidence} streaming={running} />
        </div>
      )}

      {s.phase === "error" && s.error && s.error.kind === "not_configured" && (
        <div className="mt-3 max-w-[70ch] text-base leading-relaxed text-fg-2" role="status" data-testid="ask-evidence-only">
          <p>
            <span className="font-semibold text-fg">No answer was written because no model is configured.</span>{" "}
            {evidence.length > 0
              ? `Retrieval still ran: the ${evidence.length} most relevant passages from your library are in the evidence panel, each with its source file, lines and commit.`
              : "Retrieval still ran, but nothing in your library matched this question."}
          </p>
          <Link to="/settings/models" className="link mt-1.5 inline-block text-sm">
            Configure a model to get written answers
          </Link>
        </div>
      )}
      {s.phase === "error" && s.error && s.error.kind !== "not_configured" && (
        <div className="mt-4">
          <ProviderErrorPanel
            error={s.error}
            alternatives={s.alternatives}
            onRetry={live ? onRetry : undefined}
            onSwitch={live ? (opt) => onSwitch({ provider_id: opt.provider_id, model: opt.default_model ?? "" }) : undefined}
          />
        </div>
      )}

      {s.phase === "done" && s.report && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <ReportActions report={s.report} onChanged={onReport} />
          <UsageLine usage={s.report.usage} />
        </div>
      )}
      {s.phase === "done" && s.report && s.report.warnings.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-sm text-fg-3">
          {s.report.warnings.map((w, i) => (
            <li key={i}>· {w}</li>
          ))}
        </ul>
      )}
    </article>
  );
}

export default function AskPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const repoId = Number(params.get("repo")) || null;
  const scopedRepo = useRepo(repoId ?? Number.NaN);
  const options = useModelOptions();
  const [route, setRoute] = useState<RouteOverride | null>(null);
  const gate = useCloudGate(route);
  const stream = useAiStream();
  const [history, setHistory] = useState<Turn[]>([]);
  const [current, setCurrent] = useState<{ id: number; question: string; fallbackEvidence?: Evidence[] } | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const turnId = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const autoRan = useRef(false);

  const liveTurn: Turn | null = current ? { ...current, state: stream.state } : null;
  const turns = liveTurn ? [...history, liveTurn] : history;
  const selected = turns.find((t) => t.id === selectedId) ?? turns[turns.length - 1] ?? null;
  const running = stream.state.phase === "running";
  const configured = options.data?.active.configured ?? true;

  const ask = (question: string, routeOverride?: RouteOverride | null, replaceCurrent = false) => {
    const q = question.trim();
    if (!q || running) return;
    const useRoute = routeOverride === undefined ? route : routeOverride;
    const prior = replaceCurrent ? history : turns;
    const start = (confirmCloud: boolean) => {
      if (!replaceCurrent && liveTurn) setHistory((h) => [...h, liveTurn]);
      const id = ++turnId.current;
      setCurrent({ id, question: q });
      setSelectedId(id);
      setDraft("");
      const past = prior
        .filter((t) => t.state.phase === "done" && t.state.text)
        .slice(-4)
        .flatMap((t) => [
          { role: "user", content: t.question },
          { role: "assistant", content: t.state.text },
        ]);
      stream.start((opts) =>
        api.streamAsk({ question: q, repo_ids: repoId ? [repoId] : [], route: useRoute, confirm_cloud: confirmCloud, history: past }, opts),
      );
      // Retrieval works without any model: fetch passages in parallel as a safety net.
      api
        .search({ query: q, mode: "hybrid", scope: "docs", limit: 8, record: false, filters: repoId ? { repo_ids: [repoId] } : undefined })
        .then((res) => setCurrent((c) => (c && c.id === id ? { ...c, fallbackEvidence: searchToEvidence(res) } : c)))
        .catch(() => undefined);
      requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }));
    };
    gate.run(start);
  };

  // A stream that asks for confirmation (server-side check) re-opens the consent dialog.
  useEffect(() => {
    if (stream.state.phase === "error" && stream.state.error?.kind === "confirmation_required" && current) {
      gate.run(() => ask(current.question, route, true), true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.state.phase, stream.state.error?.kind]);

  // Arriving with ?q= runs the question once.
  useEffect(() => {
    const q = params.get("q");
    if (q && !autoRan.current && options.data) {
      autoRan.current = true;
      const next = new URLSearchParams(params);
      next.delete("q");
      setParams(next, { replace: true });
      ask(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, options.data]);

  const evidence = useMemo(() => {
    if (!selected) return [];
    return selected.state.evidence.length ? selected.state.evidence : (selected.fallbackEvidence ?? []);
  }, [selected]);

  return (
    <div className="mx-auto grid h-full w-full max-w-[1500px] grid-rows-[minmax(0,1fr)] gap-x-10 px-4 md:px-8 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_400px]">
      <section className="flex min-h-0 min-w-0 flex-col" aria-label="Conversation">
        <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-fg">Ask AI</h1>
            <p className="mt-0.5 text-sm text-fg-3">Answers are written only from evidence retrieved out of your starred repositories, and cite it.</p>
          </div>
          <ModelPicker value={route} onChange={setRoute} disabled={running} />
        </header>

        {repoId && (
          <div className="flex items-center gap-2 border-b border-line py-2 text-sm text-fg-3">
            Scoped to
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

        {!configured && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line py-3 text-sm" role="status">
            <Settings2 className="size-4 shrink-0 text-fg-3" aria-hidden />
            <p className="min-w-0 flex-1 text-fg-2">
              <span className="font-medium text-fg">No model is configured.</span> Ask AI needs a local or cloud model. Until then you still get the retrieved evidence for any question, and Search works fully.
            </p>
            <Link to="/settings/models" className="inline-flex h-7 items-center rounded-sm bg-accent-solid px-2.5 text-xs font-medium text-white hover:bg-accent-solid-hover">
              Configure a model
            </Link>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {turns.length === 0 ? (
            <div className="max-w-[760px] py-10 animate-rise">
              <MessagesSquare className="size-6 text-fg-4" aria-hidden />
              <h2 className="mt-3 text-xl font-semibold text-fg">
                {repoId && scopedRepo.data ? `Ask about ${scopedRepo.data.full_name}` : "Ask a question about what you have starred"}
              </h2>
              <p className="mt-1.5 max-w-[62ch] text-base leading-relaxed text-fg-3">
                StarIndex retrieves passages with hybrid search, then the model answers from those passages only. Every claim links to its source file, lines and commit.
              </p>
              {!repoId && (
                <ul className="mt-6 flex flex-col">
                  {STARTERS.map((s) => (
                    <li key={s}>
                      <button type="button" onClick={() => ask(s)} className="group flex w-full cursor-pointer items-center gap-3 border-b border-line py-2.5 text-left text-md text-fg-2 hover:text-fg">
                        <span className="min-w-0 flex-1">{s}</span>
                        <ArrowUp className="size-3.5 shrink-0 rotate-45 text-fg-4 group-hover:text-accent" aria-hidden />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <div className="max-w-[860px]">
              {turns.map((turn) => {
                const isLive = liveTurn?.id === turn.id;
                return (
                  <TurnView
                    key={turn.id}
                    turn={turn}
                    live={isLive}
                    selected={selected?.id === turn.id}
                    onSelect={() => setSelectedId(turn.id)}
                    onRetry={() => ask(turn.question, route, true)}
                    onSwitch={(next) => {
                      setRoute(next);
                      ask(turn.question, next, true);
                    }}
                    onReport={(report) => {
                      if (isLive) stream.setState((st) => ({ ...st, report }));
                      else setHistory((h) => h.map((t) => (t.id === turn.id ? { ...t, state: { ...t.state, report } } : t)));
                    }}
                  />
                );
              })}
              {/* On narrower screens the evidence list follows the conversation. */}
              {evidence.length > 0 && (
                <div className="py-4 xl:hidden">
                  <EvidenceAside evidence={evidence} text={selected?.state.text} />
                </div>
              )}
              <div ref={endRef} className="h-4" />
            </div>
          )}
        </div>

        <form
          className="shrink-0 border-t border-line bg-bg py-3"
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          <div className="flex max-w-[860px] items-end gap-2 rounded-lg border border-line-strong bg-surface p-2 focus-within:border-accent">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(draft);
                }
              }}
              rows={draft.split("\n").length > 2 || draft.length > 120 ? 3 : 1}
              placeholder={turns.length ? "Ask a follow-up…" : "Ask about your starred repositories…"}
              aria-label="Question"
              data-search-input
              className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-2 py-1.5 text-md text-fg outline-none placeholder:text-fg-4"
            />
            {running ? (
              <Button variant="secondary" icon={<Square className="size-3" />} onClick={stream.cancel}>
                Stop
              </Button>
            ) : (
              <Button type="submit" variant="primary" disabled={!draft.trim()} icon={<ArrowUp className="size-3.5" />}>
                Ask
              </Button>
            )}
          </div>
          <div className="mt-1.5 flex max-w-[860px] flex-wrap items-center justify-between gap-2 text-xs text-fg-4">
            <span>
              {gate.effective.configured
                ? `${gate.effective.isCloud ? "Cloud" : "Local"} · ${gate.effective.providerName ?? ""} ${gate.effective.model ? `· ${gate.effective.model}` : ""}${gate.effective.isCloud ? " — retrieved passages are sent to this provider" : " — nothing leaves this machine"}`
                : "No model configured — evidence only"}
            </span>
            <span className="flex items-center gap-3">
              {turns.length > 0 && (
                <>
                  <button
                    type="button"
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-fg"
                    onClick={() => navigate(`/research?q=${encodeURIComponent(turns[turns.length - 1]!.question)}${repoId ? `&repo=${repoId}` : ""}`)}
                  >
                    <FlaskConical className="size-3" /> Research this in depth
                  </button>
                  <button
                    type="button"
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-fg"
                    onClick={() => {
                      stream.reset();
                      setHistory([]);
                      setCurrent(null);
                      setSelectedId(null);
                    }}
                  >
                    <X className="size-3" /> New conversation
                  </button>
                </>
              )}
            </span>
          </div>
        </form>
      </section>

      <div className="hidden min-h-0 overflow-y-auto border-l border-line py-4 pl-6 xl:block">
        <EvidenceAside
          evidence={evidence}
          text={selected?.state.text}
          live={selected ? selected.state.phase === "running" : false}
        />
      </div>
      {gate.dialog}
    </div>
  );
}
