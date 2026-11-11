/**
 * State machine for one streamed AI request (ask, research, comparison narrative, summary).
 * Provider failures surface as state — nothing is retried or re-routed automatically.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/errors";
import type { StreamOptions } from "@/api/sse";
import type {
  Evidence,
  ProviderErrorInfo,
  ProviderOption,
  Report,
  RepoSummaryText,
  StatusEvent,
  StreamEvent,
  TraceStep,
} from "@/api/types";

export interface AiStreamState {
  phase: "idle" | "running" | "done" | "error" | "cancelled";
  status: StatusEvent | null;
  plan: string[];
  steps: TraceStep[];
  evidence: Evidence[];
  text: string;
  report: Report | null;
  summary: RepoSummaryText | null;
  error: ProviderErrorInfo | null;
  alternatives: ProviderOption[];
  reportId: number | null;
  startedAt: number | null;
}

export const IDLE_STREAM: AiStreamState = {
  phase: "idle",
  status: null,
  plan: [],
  steps: [],
  evidence: [],
  text: "",
  report: null,
  summary: null,
  error: null,
  alternatives: [],
  reportId: null,
  startedAt: null,
};

/** Pure reducer so the event handling can be unit-tested without React. */
export function reduceStream(state: AiStreamState, event: StreamEvent): AiStreamState {
  switch (event.type) {
    case "status":
      return { ...state, status: { ...state.status, ...event.data } };
    case "plan":
      return { ...state, plan: event.data.steps ?? [] };
    case "step": {
      const others = state.steps.filter((s) => s.index !== event.data.index);
      return { ...state, steps: [...others, event.data].sort((a, b) => a.index - b.index) };
    }
    case "evidence": {
      const byRef = new Map(state.evidence.map((e) => [e.ref, e]));
      for (const ev of event.data.evidence ?? []) byRef.set(ev.ref, ev);
      return { ...state, evidence: [...byRef.values()] };
    }
    case "delta":
      return { ...state, text: state.text + (event.data.text ?? "") };
    case "done": {
      const report = event.data.report ?? null;
      const summary = event.data.summary ?? null;
      return {
        ...state,
        phase: "done",
        report,
        summary,
        text: report?.answer_md || summary?.content || state.text,
        evidence: report?.evidence?.length ? report.evidence : summary?.evidence?.length ? summary.evidence : state.evidence,
        steps: report?.trace?.length ? report.trace : state.steps,
        reportId: report?.id ?? state.reportId,
      };
    }
    case "error":
      return {
        ...state,
        phase: "error",
        error: event.data.error,
        alternatives: event.data.alternatives ?? [],
        reportId: event.data.report_id ?? state.reportId,
      };
  }
}

export type StreamRunner = (opts: StreamOptions) => Promise<void>;

export function useAiStream() {
  const [state, setState] = useState<AiStreamState>(IDLE_STREAM);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((s) => (s.phase === "running" ? { ...s, phase: "cancelled" } : s));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(IDLE_STREAM);
  }, []);

  const start = useCallback(async (run: StreamRunner, keep?: Partial<AiStreamState>) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ ...IDLE_STREAM, ...keep, phase: "running", startedAt: Date.now() });
    try {
      await run({
        signal: controller.signal,
        onEvent: (event) => {
          if (controller.signal.aborted) return;
          setState((s) => reduceStream(s, event));
        },
      });
      if (controller.signal.aborted) return;
      // The server closed the stream without a terminal event: report it, never pretend success.
      setState((s) =>
        s.phase === "running"
          ? {
              ...s,
              phase: "error",
              error: {
                kind: "stream_incomplete",
                message: "The response ended before it was complete.",
                provider_id: s.status?.provider_id ?? null,
                provider_kind: s.status?.provider_kind ?? null,
                status: null,
                code: null,
                retryable: true,
                retry_after_s: null,
                action_url: null,
                action_label: null,
              },
            }
          : s,
      );
    } catch (err) {
      if ((err as Error).name === "AbortError" || controller.signal.aborted) return;
      const api = err instanceof ApiError ? err : null;
      setState((s) => ({
        ...s,
        phase: "error",
        error: {
          kind: api?.kind ?? "network",
          message: api?.message ?? "The request failed.",
          provider_id: null,
          provider_kind: null,
          status: api?.status ?? null,
          code: null,
          retryable: true,
          retry_after_s: null,
          action_url: null,
          action_label: null,
        },
      }));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, []);

  // Abort on unmount. The abort is deferred one tick so that React StrictMode's simulated
  // unmount/remount in development does not kill a stream that was started during mount.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      setTimeout(() => {
        if (!mounted.current) abortRef.current?.abort();
      }, 0);
    };
  }, []);

  return { state, start, cancel, reset, setState };
}
