/** Compact, locale-stable formatting for dense metadata rows. */

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1).replace(/\.0$/, "")} ${units[i]}`;
}

export function formatKb(kb: number | null | undefined): string {
  return kb === null || kb === undefined ? "—" : formatBytes(kb * 1024);
}

/** "3d ago", "5mo ago", "in 2h". Returns "—" for missing/invalid input. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const future = t > now;
  const seconds = Math.abs(Math.round((now - t) / 1000));
  if (seconds < 45) return future ? "in moments" : "just now";
  let value: number;
  let unit: string;
  if (seconds < 3600) {
    value = Math.floor(seconds / 60);
    unit = "m";
  } else if (seconds < 86_400) {
    value = Math.floor(seconds / 3600);
    unit = "h";
  } else if (seconds < 30 * 86_400) {
    value = Math.floor(seconds / 86_400);
    unit = "d";
  } else {
    // Calendar arithmetic so "exactly two years ago" reads as 2y regardless of leap days.
    const [a, b] = t < now ? [new Date(t), new Date(now)] : [new Date(now), new Date(t)];
    let months = (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
    if (b.getUTCDate() < a.getUTCDate()) months -= 1;
    months = Math.max(1, months);
    if (months < 12) {
      value = months;
      unit = "mo";
    } else {
      value = Math.floor(months / 12);
      unit = "y";
    }
  }
  value = Math.max(1, value);
  return future ? `in ${value}${unit}` : `${value}${unit} ago`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(usd: number | null | undefined): string | null {
  if (usd === null || usd === undefined) return null;
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "";
}

export function pct(part: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, (part / total) * 100));
}

export function plural(n: number, one: string, many?: string): string {
  return `${formatNumber(n)} ${n === 1 ? one : (many ?? `${one}s`)}`;
}

export function lineRange(start: number | null | undefined, end: number | null | undefined): string {
  if (!start) return "";
  return end && end !== start ? `L${start}–${end}` : `L${start}`;
}

export const MAINTENANCE_LABEL: Record<string, string> = {
  active: "Active",
  maintained: "Maintained",
  slowing: "Slowing",
  stale: "Stale",
  archived: "Archived",
  unknown: "Unknown",
};

export const INDEX_LEVEL_LABEL: Record<string, string> = {
  metadata: "Metadata",
  docs: "Docs",
  deep: "Deep",
};

export const TASK_LABEL: Record<string, string> = {
  classify: "Metadata classification",
  summarize: "Repository summaries",
  expand_query: "Query expansion",
  answer: "Answer synthesis",
  reasoning: "Multi-repository reasoning",
  compare: "Comparison reports",
};

export const JOB_LABEL: Record<string, string> = {
  sync_full: "Full sync",
  sync_incremental: "Incremental sync",
  index_repo: "Index repository",
  embed_pending: "Create embeddings",
  classify_repos: "Classify repositories",
  rebuild_search_index: "Rebuild search index",
  rebuild_vector_index: "Rebuild vector index",
  refresh_metadata: "Refresh metadata",
  summarize_repo: "Summarise repository",
  cleanup: "Cleanup",
  backup: "Backup",
};
