import { ExternalLink, GitCompareArrows, Heart } from "lucide-react";
import { memo } from "react";
import type { Match, RepoSummary, ResultSignals } from "@/api/types";
import { SourceRef, kindIcon } from "@/components/evidence/EvidenceBits";
import { Checkbox, IconButton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { cleanSnippet, Highlighted } from "@/lib/highlight";
import { useCompareTray, useEvidenceViewer } from "@/state/stores";
import { RepoFacts, RepoTitle, TopicList } from "./RepoBits";

export function MatchSnippet({ match }: { match: Match }) {
  const open = useEvidenceViewer((s) => s.open);
  return (
    <div className="group/match grid grid-cols-[auto_1fr] gap-x-2 text-xs">
      <span className="mt-[3px] text-fg-4" aria-hidden>
        {kindIcon(match.kind)}
      </span>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={() => open({ type: "chunk", chunkId: match.chunk_id, label: "Matched passage" })}
            className="min-w-0 cursor-pointer text-left text-fg-2 hover:text-accent"
            title="Open the cited passage"
          >
            <SourceRef path={match.path} start={match.start_line} end={match.end_line} sha={match.commit_sha} />
          </button>
          {match.heading && <span className="hidden min-w-0 truncate text-fg-4 sm:inline">› {match.heading}</span>}
          {match.html_url && (
            <a
              href={match.html_url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Open this passage on GitHub"
              className="shrink-0 text-fg-4 opacity-0 transition-opacity hover:text-fg focus-visible:opacity-100 group-hover/match:opacity-100"
            >
              <ExternalLink className="size-3" />
            </a>
          )}
        </div>
        <p className="mt-0.5 line-clamp-2 leading-relaxed text-fg-3">
          <Highlighted text={cleanSnippet(match.snippet)} />
        </p>
      </div>
    </div>
  );
}

export function SignalsLine({ signals, score }: { signals: ResultSignals; score: number }) {
  const parts: string[] = [`score ${score.toFixed(4)}`];
  if (signals.lexical_rank !== null && signals.lexical_rank !== undefined) parts.push(`lexical #${signals.lexical_rank}`);
  if (signals.semantic_rank !== null && signals.semantic_rank !== undefined) parts.push(`semantic #${signals.semantic_rank}`);
  if (signals.fused_score !== null && signals.fused_score !== undefined) parts.push(`fused ${signals.fused_score.toFixed(4)}`);
  if (signals.rerank_score !== null && signals.rerank_score !== undefined) parts.push(`rerank ${signals.rerank_score.toFixed(3)}`);
  if (signals.prior !== null && signals.prior !== undefined) parts.push(`prior ×${signals.prior.toFixed(3)}`);
  if (signals.name_match) parts.push("name match");
  return <p className="mono text-2xs text-fg-4">{parts.join("  ·  ")}</p>;
}

export interface RepoRowProps {
  repo: RepoSummary;
  rank?: number;
  matches?: Match[];
  signals?: ResultSignals;
  score?: number;
  highlights?: Record<string, string>;
  focused?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (selected: boolean) => void;
  onTopic?: (topic: string) => void;
  showStarred?: boolean;
  showIndex?: boolean;
  explain?: boolean;
  onFocus?: () => void;
}

/** The detailed result row: identity, description, facts, topics, matched passages. */
export const RepoRow = memo(function RepoRow({
  repo,
  rank,
  matches = [],
  signals,
  score,
  highlights,
  focused,
  selectable,
  selected,
  onSelect,
  onTopic,
  showStarred,
  showIndex,
  explain,
  onFocus,
}: RepoRowProps) {
  const inTray = useCompareTray((s) => s.ids.includes(repo.id));
  const toggleTray = useCompareTray((s) => s.toggle);
  return (
    <article
      data-result-row
      data-repo-id={repo.id}
      data-focused={focused || undefined}
      onMouseDown={onFocus}
      className={cn(
        "group relative grid grid-cols-[auto_1fr] gap-x-3 border-b border-line px-3 py-3.5 transition-colors",
        focused ? "bg-raised" : "hover:bg-surface",
        repo.star_state === "unstarred" && "opacity-80",
      )}
    >
      <div className="flex w-7 flex-col items-end gap-2 pt-[3px]">
        {selectable ? (
          <Checkbox checked={Boolean(selected)} onChange={(v) => onSelect?.(v)} label={<span className="sr-only">Select {repo.full_name}</span>} />
        ) : rank !== undefined ? (
          <span className="mono text-xs text-fg-4 tnum">{rank}</span>
        ) : null}
      </div>

      <div className="min-w-0">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <RepoTitle repo={repo} highlight={highlights?.name} />
            {repo.favorite && <Heart className="size-3 shrink-0 fill-fg-2 text-fg-2" aria-label="Favorite" />}
          </div>
          <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 data-[on=true]:opacity-100" data-on={inTray}>
            <IconButton
              label={inTray ? "Remove from comparison" : "Add to comparison"}
              size="sm"
              active={inTray}
              onClick={() => toggleTray(repo.id, repo.full_name)}
            >
              <GitCompareArrows className="size-3.5" />
            </IconButton>
            <a
              href={repo.html_url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open ${repo.full_name} on GitHub`}
              title="Open on GitHub"
              className="inline-flex size-7 items-center justify-center rounded-sm text-fg-3 hover:bg-hover hover:text-fg"
            >
              <ExternalLink className="size-3.5" />
            </a>
          </div>
        </div>

        {(highlights?.description || repo.description) && (
          <p className="mt-0.5 line-clamp-2 max-w-[92ch] text-base text-fg-2">
            {highlights?.description ? <Highlighted text={highlights.description} /> : repo.description}
          </p>
        )}

        <RepoFacts repo={repo} className="mt-1.5" showStarred={showStarred} showIndex={showIndex} />

        {(repo.topics.length > 0 || repo.categories.length > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <TopicList topics={repo.topics} max={5} onPick={onTopic} />
            {repo.categories.length > 0 && (
              <span className="text-xs text-fg-4">{repo.categories.map((c) => c.name).join(" · ")}</span>
            )}
          </div>
        )}

        {matches.length > 0 && (
          <div className="mt-2.5 flex flex-col gap-2 border-l border-line-strong pl-3">
            {matches.slice(0, 3).map((m) => (
              <MatchSnippet key={m.chunk_id} match={m} />
            ))}
          </div>
        )}

        {explain && signals && score !== undefined && (
          <div className="mt-2">
            <SignalsLine signals={signals} score={score} />
          </div>
        )}
      </div>
    </article>
  );
});
