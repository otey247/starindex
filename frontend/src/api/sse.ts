/**
 * POST + Server-Sent Events over fetch.
 *
 * EventSource cannot POST, so we read the response body as a stream and parse
 * `event:` / `data:` frames ourselves. Supports AbortController cancellation.
 */
import { ApiError, toApiError } from "./errors";
import type { StreamEvent } from "./types";

export interface SseFrame {
  event: string;
  data: string;
}

/**
 * Incremental SSE frame parser. Feed it decoded text chunks; it returns the frames that
 * completed in that chunk. Handles CRLF, multi-line `data:`, comments and split chunks.
 */
export class SseParser {
  private buffer = "";

  feed(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    // Frames end at a blank line. Normalise CRLF/CR so one regex finds the boundary.
    this.buffer = this.buffer.replace(/\r\n?/g, "\n");
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      boundary = this.buffer.indexOf("\n\n");
    }
    return frames;
  }

  /** Flush a trailing frame that was not terminated by a blank line. */
  end(): SseFrame[] {
    const raw = this.buffer.trim();
    this.buffer = "";
    if (!raw) return [];
    const frame = parseFrame(raw);
    return frame ? [frame] : [];
  }
}

function parseFrame(raw: string): SseFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}

const KNOWN = new Set(["status", "plan", "step", "evidence", "delta", "done", "error"]);

export function toStreamEvent(frame: SseFrame): StreamEvent | null {
  if (!KNOWN.has(frame.event)) return null;
  try {
    return { type: frame.event, data: JSON.parse(frame.data) } as StreamEvent;
  } catch {
    return null;
  }
}

export interface StreamOptions {
  signal?: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}

/**
 * Run a streaming request to completion. Resolves when the server closes the stream.
 * Rejects with ApiError for HTTP-level failures; model/provider failures arrive as
 * `error` events (the caller decides what to do — nothing is retried automatically).
 */
export async function postStream(path: string, body: unknown, { signal, onEvent }: StreamOptions): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network", "StarIndex is not reachable. Is the local server running?");
  }
  if (!response.ok) throw await toApiError(response);
  if (!response.body) throw new ApiError(response.status, "stream", "The server returned an empty stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const dispatch = (frames: SseFrame[]) => {
    for (const frame of frames) {
      const event = toStreamEvent(frame);
      if (event) onEvent(event);
    }
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      dispatch(parser.feed(decoder.decode(value, { stream: true })));
    }
    dispatch(parser.feed(decoder.decode()));
    dispatch(parser.end());
  } finally {
    reader.releaseLock?.();
  }
}
