import { ArrowDown, ArrowUp, Heart } from "lucide-react";
import type { ReactNode } from "react";
import type { RepoSummary } from "@/api/types";
import { Checkbox, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCount, timeAgo } from "@/lib/format";
import { IndexLabel, MaintenanceLabel, RepoFacts, RepoTitle, TopicList } from "./RepoBits";

export interface TableColumnSort {
  key: string;
  order: "asc" | "desc";
  onSort: (key: string) => void;
}

function Th({ children, sortKey, sort, className }: { children: ReactNode; sortKey?: string; sort?: TableColumnSort; className?: string }) {
  const active = sort && sortKey && sort.key === sortKey;
  return (
    <th scope="col" className={cn("label sticky top-0 z-[1] whitespace-nowrap bg-bg px-3 py-2 text-left font-medium", className)}>
      {sortKey && sort ? (
        <button
          type="button"
          onClick={() => sort.onSort(sortKey)}
          className={cn("inline-flex cursor-pointer items-center gap-1 uppercase hover:text-fg", active && "!text-fg")}
        >
          {children}
          {active && (sort.order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
        </button>
      ) : (
        children
      )}
    </th>
  );
}

/** Compact, scannable table view. */
export function RepoTable({
  repos,
  selected,
  onToggle,
  onToggleAll,
  focusedId,
  sort,
  showRank,
}: {
  repos: RepoSummary[];
  selected?: Set<number>;
  onToggle?: (id: number, value: boolean) => void;
  onToggleAll?: (value: boolean) => void;
  focusedId?: number | null;
  sort?: TableColumnSort;
  showRank?: boolean;
}) {
  const allSelected = selected ? repos.length > 0 && repos.every((r) => selected.has(r.id)) : false;
  const someSelected = selected ? repos.some((r) => selected.has(r.id)) : false;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] table-fixed border-collapse text-sm">
        <colgroup>
          {selected && <col className="w-10" />}
          {showRank && <col className="w-10" />}
          <col />
          <col className="w-[68px]" />
          <col className="w-[104px]" />
          <col className="w-[100px]" />
          <col className="w-[82px]" />
          <col className="w-[82px]" />
          <col className="w-[98px]" />
          <col className="w-[84px]" />
        </colgroup>
        <thead>
          <tr className="border-b border-line">
            {selected && (
              <th scope="col" className="sticky top-0 z-[1] w-9 bg-bg px-3 py-2">
                <Checkbox
                  checked={allSelected}
                  indeterminate={!allSelected && someSelected}
                  onChange={(v) => onToggleAll?.(v)}
                  label={<span className="sr-only">Select all</span>}
                />
              </th>
            )}
            {showRank && <Th className="w-8">#</Th>}
            <Th sortKey="name" sort={sort}>Repository</Th>
            <Th sortKey="stars" sort={sort} className="text-right">Stars</Th>
            <Th>Language</Th>
            <Th>License</Th>
            <Th sortKey="pushed_at" sort={sort}>Pushed</Th>
            <Th sortKey="starred_at" sort={sort}>Starred</Th>
            <Th>Status</Th>
            <Th>Index</Th>
          </tr>
        </thead>
        <tbody>
          {repos.map((repo, i) => (
            <tr
              key={repo.id}
              data-result-row
              data-repo-id={repo.id}
              data-focused={focusedId === repo.id || undefined}
              className={cn("border-b border-line transition-colors", focusedId === repo.id ? "bg-raised" : "hover:bg-surface")}
            >
              {selected && (
                <td className="px-3 py-1.5">
                  <Checkbox
                    checked={selected.has(repo.id)}
                    onChange={(v) => onToggle?.(repo.id, v)}
                    label={<span className="sr-only">Select {repo.full_name}</span>}
                  />
                </td>
              )}
              {showRank && <td className="mono px-3 py-1.5 text-xs text-fg-4 tnum">{i + 1}</td>}
              <td className="px-3 py-1.5">
                <div className="flex items-center gap-2">
                  <RepoTitle repo={repo} size="sm" />
                  {repo.favorite && <Heart className="size-3 shrink-0 fill-fg-2 text-fg-2" aria-label="Favorite" />}
                </div>
                {repo.description && <p className="truncate text-xs text-fg-3">{repo.description}</p>}
              </td>
              <td className="px-3 py-1.5 text-right text-fg-2 tnum">{formatCount(repo.stars)}</td>
              <td className="mono truncate px-3 py-1.5 text-xs text-fg-2">{repo.language ?? "—"}</td>
              <td className="truncate px-3 py-1.5 text-xs text-fg-3">{repo.license_spdx ?? "—"}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-xs text-fg-3">{timeAgo(repo.pushed_at)}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-xs text-fg-3">{timeAgo(repo.starred_at)}</td>
              <td className="px-3 py-1.5 text-xs">
                <MaintenanceLabel value={repo.maintenance} />
              </td>
              <td className="px-3 py-1.5 text-xs">
                <IndexLabel repo={repo} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Card grid: a browsing view, borders only, no accents. */
export function RepoGrid({
  repos,
  focusedId,
  selected,
  onToggle,
}: {
  repos: RepoSummary[];
  focusedId?: number | null;
  selected?: Set<number>;
  onToggle?: (id: number, value: boolean) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 2xl:grid-cols-3">
      {repos.map((repo) => (
        <article
          key={repo.id}
          data-result-row
          data-repo-id={repo.id}
          data-focused={focusedId === repo.id || undefined}
          className={cn(
            "flex min-w-0 flex-col gap-2 rounded-md border bg-surface p-3.5 transition-colors",
            focusedId === repo.id ? "border-fg-4 bg-raised" : "border-line hover:border-line-strong",
          )}
        >
          <div className="flex items-start justify-between gap-2">
            <RepoTitle repo={repo} />
            {selected && (
              <Checkbox
                checked={selected.has(repo.id)}
                onChange={(v) => onToggle?.(repo.id, v)}
                label={<span className="sr-only">Select {repo.full_name}</span>}
              />
            )}
          </div>
          <p className="line-clamp-3 min-h-[3.9em] text-sm text-fg-2">{repo.description ?? <span className="text-fg-4">No description.</span>}</p>
          <TopicList topics={repo.topics} max={4} />
          <RepoFacts repo={repo} className="mt-auto pt-1" />
        </article>
      ))}
    </div>
  );
}

/** Minimal one-line listing for strips and side panels. */
export function RepoMiniList({ repos, meta, empty }: { repos: RepoSummary[]; meta?: (repo: RepoSummary) => ReactNode; empty?: string }) {
  if (repos.length === 0) return <p className="py-2 text-sm text-fg-4">{empty ?? "Nothing here yet."}</p>;
  return (
    <ul className="flex flex-col">
      {repos.map((repo) => (
        <li key={repo.id} className="flex items-baseline justify-between gap-3 py-1">
          <RepoTitle repo={repo} size="sm" />
          <span className="shrink-0 text-xs text-fg-4">{meta ? meta(repo) : timeAgo(repo.starred_at)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ResultSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading results">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="grid grid-cols-[auto_1fr] gap-x-3 border-b border-line px-3 py-4">
          <Skeleton className="mt-1 h-3 w-5" />
          <div className="flex flex-col gap-2.5">
            <Skeleton className="h-4 w-[38%]" />
            <Skeleton className="h-3 w-4/5" />
            <Skeleton className="h-3 w-2/5" />
          </div>
        </div>
      ))}
    </div>
  );
}
