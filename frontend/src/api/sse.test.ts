import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { postStream, SseParser, toStreamEvent } from "./sse";
import type { StreamEvent } from "./types";
import { streamResponse } from "@/test/utils";

describe("SseParser", () => {
  it("parses complete frames", () => {
    const p = new SseParser();
    expect(p.feed('event: delta\ndata: {"text":"hi"}\n\n')).toEqual([{ event: "delta", data: '{"text":"hi"}' }]);
  });

  it("buffers frames split across arbitrary chunk boundaries", () => {
    const p = new SseParser();
    const raw = 'event: status\ndata: {"message":"Retrieving"}\n\nevent: delta\ndata: {"text":"a"}\n\n';
    const frames = [];
    for (const ch of raw) frames.push(...p.feed(ch));
    expect(frames.map((f) => f.event)).toEqual(["status", "delta"]);
    expect(JSON.parse(frames[1]!.data)).toEqual({ text: "a" });
  });

  it("handles CRLF line endings, comments and multi-line data", () => {
    const p = new SseParser();
    const frames = p.feed(": keep-alive\r\n\r\nevent: delta\r\ndata: line one\r\ndata: line two\r\n\r\n");
    expect(frames).toEqual([{ event: "delta", data: "line one\nline two" }]);
  });

  it("defaults the event name to message and ignores frames without data", () => {
    const p = new SseParser();
    expect(p.feed("data: x\n\nevent: done\n\n")).toEqual([{ event: "message", data: "x" }]);
  });

  it("flushes an unterminated trailing frame on end()", () => {
    const p = new SseParser();
    expect(p.feed('event: done\ndata: {"ok":true}')).toEqual([]);
    expect(p.end()).toEqual([{ event: "done", data: '{"ok":true}' }]);
    expect(p.end()).toEqual([]);
  });

  it("keeps a colon inside the data value", () => {
    const p = new SseParser();
    expect(p.feed('data: {"url":"https://x.y/z"}\n\n')[0]!.data).toBe('{"url":"https://x.y/z"}');
  });
});

describe("toStreamEvent", () => {
  it("maps known events and rejects unknown or malformed ones", () => {
    expect(toStreamEvent({ event: "delta", data: '{"text":"x"}' })).toEqual({ type: "delta", data: { text: "x" } });
    expect(toStreamEvent({ event: "ping", data: "{}" })).toBeNull();
    expect(toStreamEvent({ event: "delta", data: "{not json" })).toBeNull();
  });
});

describe("postStream", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs JSON and dispatches events in order through completion", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse(['event: status\ndata: {"message":"go"}\n\nevent: del', 'ta\ndata: {"text":"Hel"}\n\nevent: delta\ndata: {"text":"lo"}\n\n', 'event: done\ndata: {"report":{"id":7}}\n\n']),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: StreamEvent[] = [];
    await postStream("/ask", { question: "q" }, { onEvent: (e) => events.push(e) });
    expect(fetchMock).toHaveBeenCalledWith("/api/ask", expect.objectContaining({ method: "POST", body: JSON.stringify({ question: "q" }) }));
    expect(events.map((e) => e.type)).toEqual(["status", "delta", "delta", "done"]);
    expect(events.filter((e) => e.type === "delta").map((e) => (e.data as { text: string }).text).join("")).toBe("Hello");
  });

  it("raises ApiError with the server's error body on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { kind: "validation", message: "Question is required.", detail: {} } }), { status: 422 })),
    );
    await expect(postStream("/ask", {}, { onEvent: () => undefined })).rejects.toMatchObject({ status: 422, kind: "validation", message: "Question is required." });
  });

  it("turns a network failure into a readable ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const err = await postStream("/ask", {}, { onEvent: () => undefined }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe("network");
  });

  it("propagates AbortError so callers can tell a cancel from a failure", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(postStream("/ask", {}, { onEvent: () => undefined, signal: new AbortController().signal })).rejects.toMatchObject({ name: "AbortError" });
  });
});
