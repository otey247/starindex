import { describe, expect, it } from "vitest";
import type { StreamEvent, TraceStep } from "@/api/types";
import { makeEvidence } from "@/test/utils";
import { IDLE_STREAM, reduceStream } from "./useAiStream";

const step = (index: number, title: string): TraceStep => ({ index, kind: "tool", title, tool: "search_repositories", arguments: {}, summary: "", repo_ids: [], repo_names: [], evidence_refs: [], duration_ms: 5, ok: true });
const run = (events: StreamEvent[], from = { ...IDLE_STREAM, phase: "running" as const }) => events.reduce(reduceStream, from);

describe("reduceStream", () => {
  it("accumulates deltas and merges status updates", () => {
    const s = run([
      { type: "status", data: { message: "Retrieving", provider_id: "ollama", model: "qwen3", is_cloud: false } },
      { type: "status", data: { message: "Writing" } },
      { type: "delta", data: { text: "Hel" } },
      { type: "delta", data: { text: "lo" } },
    ]);
    expect(s.text).toBe("Hello");
    expect(s.status).toMatchObject({ message: "Writing", provider_id: "ollama", model: "qwen3" });
    expect(s.phase).toBe("running");
  });

  it("keeps steps ordered and replaces a step that is re-sent", () => {
    const s = run([
      { type: "step", data: step(2, "second") },
      { type: "step", data: step(1, "first") },
      { type: "step", data: { ...step(2, "second, updated"), ok: false } },
    ]);
    expect(s.steps.map((x) => x.title)).toEqual(["first", "second, updated"]);
    expect(s.steps[1]!.ok).toBe(false);
  });

  it("de-duplicates evidence by ref across events", () => {
    const s = run([
      { type: "evidence", data: { evidence: [makeEvidence({ ref: "E1" })] } },
      { type: "evidence", data: { evidence: [makeEvidence({ ref: "E1", title: "updated" }), makeEvidence({ ref: "E2" })] } },
    ]);
    expect(s.evidence.map((e) => e.ref)).toEqual(["E1", "E2"]);
    expect(s.evidence[0]!.title).toBe("updated");
  });

  it("completes only on a done event and adopts the final report", () => {
    const report = { id: 9, answer_md: "Final [E1]", evidence: [makeEvidence()], trace: [step(1, "t")] } as never;
    const s = run([
      { type: "delta", data: { text: "partial" } },
      { type: "done", data: { report } },
    ]);
    expect(s.phase).toBe("done");
    expect(s.text).toBe("Final [E1]");
    expect(s.reportId).toBe(9);
    expect(s.steps).toHaveLength(1);
  });

  it("records provider errors with the alternatives the user may choose", () => {
    const s = run([
      { type: "delta", data: { text: "partial" } },
      {
        type: "error",
        data: {
          error: { kind: "usage_limit", message: "Limit reached", provider_id: "chatgpt-plan", provider_kind: "chatgpt_plan", status: 429, code: "subscription_sharing_usage_limit_exceeded", retryable: false, retry_after_s: null, action_url: null, action_label: null },
          alternatives: [{ provider_id: "ollama", provider_kind: "ollama", name: "Ollama", is_local: true, is_paid: false, default_model: "qwen3" }],
          report_id: 4,
        },
      },
    ]);
    expect(s.phase).toBe("error");
    expect(s.error?.kind).toBe("usage_limit");
    expect(s.alternatives.map((a) => a.provider_id)).toEqual(["ollama"]);
    expect(s.text).toBe("partial"); // what was streamed is kept, never discarded silently
    expect(s.reportId).toBe(4);
  });
});

describe("narrativeOnly", () => {
  it("drops the appended markdown table of a comparison report", async () => {
    const { narrativeOnly } = await import("./CompareTable");
    expect(narrativeOnly("## Answer\n\nA beats B [E1].\n\n## Comparison table\n\n| | A | B |\n|---|---|---|")).toBe("## Answer\n\nA beats B [E1].");
    expect(narrativeOnly("## Comparison table\n\n| | A |")).toBe("");
    expect(narrativeOnly("Just prose.")).toBe("Just prose.");
  });
});
