import { describe, expect, it } from "vitest";
import type { SearchFeedback } from "@/api/types";
import { applyFeedback, exampleArguments, feedbackByRepo, formatSettingValue, nextFeedbackAction, settingLabel, settingsDiff } from "./feedback";

const fb = (over: Partial<SearchFeedback> = {}): SearchFeedback => ({ id: 1, query: "vector database", repo_id: 10, repo_full_name: "qdrant/qdrant", verdict: "relevant", mode: "hybrid", rank: 1, created_at: "2026-11-01T00:00:00Z", ...over });

describe("nextFeedbackAction", () => {
  it("sets a verdict when there is none", () => {
    expect(nextFeedbackAction(undefined, "relevant")).toEqual({ type: "set", verdict: "relevant" });
  });
  it("clears the verdict when the active one is clicked again", () => {
    expect(nextFeedbackAction(fb({ id: 7 }), "relevant")).toEqual({ type: "delete", id: 7 });
  });
  it("replaces the verdict when the other one is clicked", () => {
    expect(nextFeedbackAction(fb(), "not_relevant")).toEqual({ type: "set", verdict: "not_relevant" });
    expect(nextFeedbackAction(fb({ verdict: "missing" }), "relevant")).toEqual({ type: "set", verdict: "relevant" });
  });
});

describe("feedback cache updates", () => {
  it("indexes feedback by repository", () => {
    const map = feedbackByRepo([fb(), fb({ id: 2, repo_id: 11, verdict: "not_relevant" })]);
    expect(map.get(10)?.verdict).toBe("relevant");
    expect(map.get(11)?.verdict).toBe("not_relevant");
    expect(feedbackByRepo(undefined).size).toBe(0);
  });
  it("removes a deleted row", () => {
    expect(applyFeedback([fb(), fb({ id: 2, repo_id: 11 })], { removedId: 1 }).map((f) => f.id)).toEqual([2]);
  });
  it("replaces the previous verdict for the same repository instead of duplicating it", () => {
    const next = applyFeedback([fb()], { upserted: fb({ id: 5, verdict: "not_relevant" }) });
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ id: 5, verdict: "not_relevant" });
  });
  it("adds feedback for a new repository", () => {
    expect(applyFeedback([fb()], { upserted: fb({ id: 3, repo_id: 12 }) })).toHaveLength(2);
  });
});

describe("settingsDiff", () => {
  const current = { search: { default_mode: "hybrid", semantic_weight: 1, rrf_k: 60 }, rerank: { enabled: false, top_n: 30 } };
  it("lists only leaf values that change, with from and to", () => {
    expect(settingsDiff(current, { search: { default_mode: "hybrid", semantic_weight: 1.5 }, rerank: { enabled: true } })).toEqual([
      { path: "search.semantic_weight", from: 1, to: 1.5 },
      { path: "rerank.enabled", from: false, to: true },
    ]);
  });
  it("is empty when nothing would change or there is no patch", () => {
    expect(settingsDiff(current, { search: { semantic_weight: 1 } })).toEqual([]);
    expect(settingsDiff(current, null)).toEqual([]);
  });
  it("reports unknown current values as not set", () => {
    const [change] = settingsDiff(undefined, { search: { semantic_weight: 2 } });
    expect(change).toEqual({ path: "search.semantic_weight", from: undefined, to: 2 });
    expect(formatSettingValue(change.from)).toBe("not set");
  });
  it("labels and formats values for people", () => {
    expect(settingLabel("rerank.enabled")).toBe("Reranking");
    expect(settingLabel("something.else")).toBe("something.else");
    expect(formatSettingValue(true)).toBe("on");
    expect(formatSettingValue(false)).toBe("off");
    expect(formatSettingValue(1.5)).toBe("1.5");
    expect(formatSettingValue("hybrid")).toBe("hybrid");
  });
});

describe("exampleArguments", () => {
  const schema = { type: "object", properties: { query: { type: "string" }, limit: { anyOf: [{ type: "integer" }, { type: "null" }], default: 8 }, repos: { type: "array" }, depth: { type: "integer" } }, required: ["query", "repos", "depth"] };
  it("prefills only required parameters, typed", () => {
    expect(exampleArguments(schema)).toEqual({ query: "", repos: [], depth: 1 });
  });
  it("uses hints when available", () => {
    expect(exampleArguments(schema, { query: "vector database" })).toMatchObject({ query: "vector database" });
  });
  it("tolerates a missing schema", () => {
    expect(exampleArguments(undefined)).toEqual({});
  });
});
