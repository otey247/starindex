export class ApiError extends Error {
  status: number;
  kind: string;
  detail: Record<string, unknown>;

  constructor(status: number, kind: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.detail = detail;
  }
}

/** Build an ApiError from a non-2xx response (`{"error": {kind, message, detail}}`). */
export async function toApiError(response: Response): Promise<ApiError> {
  let kind = "http_error";
  let message = `Request failed (${response.status})`;
  let detail: Record<string, unknown> = {};
  try {
    const body = await response.json();
    const err = body?.error ?? body?.detail;
    if (err && typeof err === "object") {
      kind = typeof err.kind === "string" ? err.kind : kind;
      message = typeof err.message === "string" ? err.message : message;
      detail = (err.detail as Record<string, unknown>) ?? {};
    } else if (typeof err === "string") {
      message = err;
    }
  } catch {
    // Body was not JSON (e.g. a proxy error page) — keep the generic message.
  }
  return new ApiError(response.status, kind, message, detail);
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
