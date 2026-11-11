import { Archive, CircleDashed, GitFork, Layers, Lock, Scale, Star, StarOff, StickyNote, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import type { RepoSummary } from "@/api/types";
import { Chip, Tooltip } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCount, formatDate, INDEX_LEVEL_LABEL, MAINTENANCE_LABEL, timeAgo } from "@/lib/format";
import { Highlighted } from "@/lib/highlight";

export function RepoTitle({
  repo,
  highlight,
  size = "md",
  className,
  onNavigate,
}: {
  repo: Pick<RepoSummary, "id" | "owner" | "name" | "full_name">;
  highlight?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  onNavigate?: () => void;
}) {
  return (
    <Link
      to={`/repo/${repo.id}`}
      onClick={onNavigate}
      className={cn(
        "group/title inline-flex min-w-0 max-w-full items-baseline",
        size === "lg" ? "text-xl" : size === "md" ? "text-md" : "text-sm",
        className,
      )}
    >
      <span className="shrink truncate text-fg-3 group-hover/title:text-fg-2">{repo.owner}</span>
      <span className="shrink-0 px-[3px] text-fg-4">/</span>
      <span className="truncate font-semibold text-fg group-hover/title:underline group-hover/title:decoration-fg-4 group-hover/title:underline-offset-4">
        {highlight ? <Highlighted text={highlight} /> : repo.name}
      </span>
    </Link>
  );
}

/** Maintenance as plain words. Archived/stale get an icon; nothing is colour-coded. */
export function MaintenanceLabel({ value, className }: { value: string; className?: string }) {
  const label = MAINTENANCE_LABEL[value] ?? value;
  const strong = value === "active";
  return (
    <span className={cn("inline-flex items-center gap-1", strong ? "text-fg-2" : "text-fg-3", className)}>
      {value === "archived" && <Archive className="size-3" aria-hidden />}
      {value === "stale" && <CircleDashed className="size-3" aria-hidden />}
      {label}
    </span>
  );
}

export function IndexLabel({ repo, className }: { repo: Pick<RepoSummary, "index_status" | "indexed_level" | "index_level">; className?: string }) {
  const { index_status: status, indexed_level: level } = repo;
  let text: string;
  let title: string;
  if (status === "indexed") {
    text = INDEX_LEVEL_LABEL[level ?? "metadata"] ?? "Indexed";
    title = `Indexed at ${text.toLowerCase()} level`;
  } else if (status === "indexing") {
    text = "Indexing";
    title = "Indexing in progress";
  } else if (status === "queued") {
    text = "Queued";
    title = "Waiting in the indexing queue";
  } else if (status === "failed") {
    text = "Failed";
    title = "Indexing failed — see Sync for details";
  } else if (status === "skipped") {
    text = "Skipped";
    title = "Skipped by indexing limits";
  } else {
    text = "Pending";
    title = "Not indexed yet";
  }
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 text-fg-3", className)}>
      {status === "failed" ? <TriangleAlert className="size-3 text-danger" aria-hidden /> : <Layers className="size-3" aria-hidden />}
      {text}
    </span>
  );
}

/** One dense, typographic line of facts. */
export function RepoFacts({
  repo,
  className,
  showStarred,
  showIndex,
}: {
  repo: RepoSummary;
  className?: string;
  showStarred?: boolean;
  showIndex?: boolean;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-0.5 text-xs text-fg-3", className)}>
      <span className="inline-flex items-center gap-1 text-fg-2 tnum" title={`${repo.stars.toLocaleString()} stars`}>
        <Star className="size-3" aria-hidden />
        {formatCount(repo.stars)}
      </span>
      {repo.language && <span className="mono text-fg-2">{repo.language}</span>}
      {repo.license_spdx && repo.license_spdx !== "NOASSERTION" && (
        <span className="inline-flex items-center gap-1" title="License">
          <Scale className="size-3" aria-hidden />
          {repo.license_spdx}
        </span>
      )}
      {repo.pushed_at && <span title={`Last push ${formatDate(repo.pushed_at)}`}>pushed {timeAgo(repo.pushed_at)}</span>}
      <MaintenanceLabel value={repo.maintenance} />
      {showStarred && repo.starred_at && <span title={`Starred ${formatDate(repo.starred_at)}`}>starred {timeAgo(repo.starred_at)}</span>}
      {repo.is_fork && (
        <span className="inline-flex items-center gap-1">
          <GitFork className="size-3" aria-hidden />
          fork
        </span>
      )}
      {repo.is_private && (
        <span className="inline-flex items-center gap-1">
          <Lock className="size-3" aria-hidden />
          private
        </span>
      )}
      {repo.star_state === "unstarred" && (
        <span className="inline-flex items-center gap-1 text-fg-2">
          <StarOff className="size-3" aria-hidden />
          unstarred
        </span>
      )}
      {repo.access_state !== "ok" && (
        <span className="inline-flex items-center gap-1 text-fg-2">
          <TriangleAlert className="size-3 text-warn" aria-hidden />
          {repo.access_state === "not_found" ? "no longer accessible" : repo.access_state}
        </span>
      )}
      {showIndex && <IndexLabel repo={repo} />}
      {repo.has_note && (
        <Tooltip content="Has a personal note" side="top">
          <StickyNote className="size-3 text-fg-2" aria-label="Has a note" />
        </Tooltip>
      )}
    </div>
  );
}

export function TopicList({
  topics,
  max = 6,
  onPick,
  className,
}: {
  topics: string[];
  max?: number;
  onPick?: (topic: string) => void;
  className?: string;
}) {
  if (!topics.length) return null;
  const shown = topics.slice(0, max);
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((t) => (
        <Chip key={t} mono onClick={onPick ? () => onPick(t) : undefined} title={onPick ? `Filter by topic ${t}` : undefined}>
          {t}
        </Chip>
      ))}
      {topics.length > max && <span className="text-xs text-fg-4">+{topics.length - max}</span>}
    </div>
  );
}

/**
 * Language share as a single monochrome ramp: the dominant language takes the accent,
 * the rest step down through neutrals. Readable without a colour legend.
 */
const RAMP = ["var(--color-accent)", "#aab3c0", "#7b8491", "#59606b", "#3f454d"];

export function LanguageBar({ languages, className }: { languages: Record<string, number>; className?: string }) {
  const entries = Object.entries(languages).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, v]) => sum + v, 0);
  if (!total) return <p className="text-sm text-fg-4">No language data yet.</p>;
  const top = entries.slice(0, 4);
  const rest = entries.slice(4).reduce((sum, [, v]) => sum + v, 0);
  const parts = rest > 0 ? [...top, ["Other", rest] as [string, number]] : top;
  return (
    <div className={className}>
      <div className="flex h-1.5 w-full gap-px overflow-hidden rounded-full" role="img" aria-label="Language breakdown">
        {parts.map(([name, bytes], i) => (
          <span key={name} style={{ width: `${(bytes / total) * 100}%`, background: RAMP[Math.min(i, RAMP.length - 1)] }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-col gap-0.5">
        {parts.map(([name, bytes], i) => (
          <li key={name} className="flex items-center justify-between gap-3 text-xs">
            <span className="inline-flex items-center gap-2 text-fg-2">
              <span className="size-1.5 rounded-full" style={{ background: RAMP[Math.min(i, RAMP.length - 1)] }} aria-hidden />
              <span className="mono">{name}</span>
            </span>
            <span className="text-fg-3 tnum">{((bytes / total) * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function OwnerAvatar({ repo, size = 20 }: { repo: Pick<RepoSummary, "owner" | "owner_avatar_url">; size?: number }) {
  // Avatars are remote images; a typographic monogram keeps the UI fully offline-capable.
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, fontSize: Math.round(size * 0.48) }}
      className="inline-flex shrink-0 items-center justify-center rounded-[5px] border border-line-strong bg-raised font-mono font-medium uppercase leading-none text-fg-2"
    >
      {repo.owner.slice(0, 1)}
    </span>
  );
}
