import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EvalRun, EvalStatus } from "@/api/types";
import { settingsDiff } from "@/lib/feedback";
import { renderApp } from "@/test/utils";
import QualitySettings, { ApplyDialog, EvalTable } from "./QualitySettings";

const api = vi.hoisted(() => ({
  evalStatus: vi.fn(),
  feedbackQueries: vi.fn(),
  settings: vi.fn(),
  runEval: vi.fn(),
  applyEval: vi.fn(),
  deleteSearchFeedback: vi.fn(),
  feedbackExportUrl: () => "/api/search/feedback/export",
}));
vi.mock("@/api/client", () => ({ api }));

const row = (config: string, label: string, ndcg: number, available = true) => ({ config, label, mode: config.startsWith("hybrid") ? "hybrid" : config, rerank: config.endsWith("rerank"), semantic_weight: 1, available, ndcg_at_10: ndcg, recall_at_10: ndcg, mrr: ndcg, rejected_in_top_10: 0.5, latency_ms_median: 42 });
const run = (over: Partial<EvalRun> = {}): EvalRun =>
  ({
    id: 1,
    created_at: new Date().toISOString(),
    queries: 16,
    judgments: 48,
    current_config: "hybrid-w1.0-plain",
    best_config: "hybrid-w1.5-rerank",
    results: [row("keyword", "Keyword only", 0.61), row("hybrid-w1.0-plain", "Hybrid, no reranking", 0.73), row("hybrid-w1.5-rerank", "Hybrid 1.5 + reranking", 0.8), row("semantic", "Semantic only", 0, false)],
    recommendation: "Hybrid 1.5 with reranking ranks your judged results best.",
    suggested_settings: { search: { semantic_weight: 1.5 }, rerank: { enabled: true } },
    ...over,
  }) as EvalRun;
const status = (over: Partial<EvalStatus> = {}): EvalStatus => ({ queries: 16, judgments: 48, min_queries_for_recommendation: 15, running: false, latest: run(), ...over }) as EvalStatus;
const settings = { search: { default_mode: "hybrid", semantic_weight: 1 }, rerank: { enabled: false, top_n: 30 } };

beforeEach(() => {
  for (const fn of Object.values(api)) if (typeof fn === "function" && "mockReset" in fn) fn.mockReset();
  api.evalStatus.mockResolvedValue(status());
  api.settings.mockResolvedValue(settings);
  api.feedbackQueries.mockResolvedValue([{ query: "vector database", updated_at: new Date().toISOString(), judgments: [{ feedback_id: 4, repo_id: 1, full_name: "qdrant/qdrant", verdict: "relevant" }] }]);
});

describe("EvalTable", () => {
  it("marks the current and the best configuration and dims unavailable ones", () => {
    render(<EvalTable run={run()} />);
    const current = screen.getByText("Hybrid, no reranking").closest("tr")!;
    expect(within(current).getByText("current")).toBeInTheDocument();
    expect(within(current).queryByText("best")).toBeNull();
    const best = screen.getByText("Hybrid 1.5 + reranking").closest("tr")!;
    expect(within(best).getByText("best")).toBeInTheDocument();
    expect(within(best).getAllByText("0.800")).toHaveLength(3);
    const unavailable = screen.getByText("Semantic only").closest("tr")!;
    expect(unavailable).toHaveAttribute("data-available", "false");
    expect(within(unavailable).getByText(/not available/)).toBeInTheDocument();
    expect(within(unavailable).queryByText("0.000")).toBeNull();
    const keyword = screen.getByText("Keyword only").closest("tr")!;
    expect(within(keyword).queryByText("current")).toBeNull();
    expect(within(keyword).queryByText("best")).toBeNull();
  });
  it("shows both markers when the current configuration is the best", () => {
    render(<EvalTable run={run({ best_config: "hybrid-w1.0-plain" })} />);
    const r = screen.getByText("Hybrid, no reranking").closest("tr")!;
    expect(within(r).getByText("current")).toBeInTheDocument();
    expect(within(r).getByText("best")).toBeInTheDocument();
  });
});

describe("ApplyDialog", () => {
  it("lists exactly the settings that change, from and to", () => {
    const changes = settingsDiff(settings, { search: { default_mode: "hybrid", semantic_weight: 1.5 }, rerank: { enabled: true } });
    render(<ApplyDialog open onClose={() => {}} onConfirm={() => {}} changes={changes} />);
    const items = within(screen.getByTestId("apply-changes")).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Semantic weight");
    expect(items[0]).toHaveTextContent("1");
    expect(items[0]).toHaveTextContent("1.5");
    expect(items[1]).toHaveTextContent("Reranking");
    expect(items[1]).toHaveTextContent("off");
    expect(items[1]).toHaveTextContent("on");
    expect(screen.getByRole("button", { name: "Apply 2 changes" })).toBeEnabled();
  });
});

describe("QualitySettings", () => {
  it("shows counts, the table and applies a suggestion only after confirmation", async () => {
    api.applyEval.mockResolvedValue({ ...settings, search: { ...settings.search, semantic_weight: 1.5 }, rerank: { enabled: true, top_n: 30 } });
    renderApp(<QualitySettings />);
    expect(await screen.findByTestId("eval-queries")).toHaveTextContent("16");
    expect(screen.getByTestId("eval-judgments")).toHaveTextContent("48");
    expect(screen.getByTestId("eval-table")).toBeInTheDocument();
    expect(screen.getByTestId("eval-recommendation")).toHaveTextContent("ranks your judged results best");
    await userEvent.click(screen.getByRole("button", { name: "Apply suggested settings" }));
    expect(api.applyEval).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Semantic weight")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply 2 changes" }));
    await waitFor(() => expect(api.applyEval).toHaveBeenCalledTimes(1));
  });

  it("has no Apply button when the run suggests nothing", async () => {
    api.evalStatus.mockResolvedValue(status({ queries: 2, latest: run({ suggested_settings: null, recommendation: "Judge more queries." }) }));
    renderApp(<QualitySettings />);
    expect(await screen.findByTestId("eval-table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply suggested settings" })).toBeNull();
    expect(screen.getByText(/2 of 15 judged queries/)).toBeInTheDocument();
  });

  it("starts an evaluation and removes a judgment", async () => {
    api.runEval.mockResolvedValue({ id: 1 });
    api.deleteSearchFeedback.mockResolvedValue({ ok: true, message: "" });
    renderApp(<QualitySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Run evaluation" }));
    expect(api.runEval).toHaveBeenCalledTimes(1);
    const link = await screen.findByRole("link", { name: "vector database" });
    expect(link).toHaveAttribute("href", "/search?q=vector%20database");
    await userEvent.click(screen.getByRole("button", { name: "Remove judgment for qdrant/qdrant" }));
    await waitFor(() => expect(api.deleteSearchFeedback).toHaveBeenCalledWith(4));
  });
});
