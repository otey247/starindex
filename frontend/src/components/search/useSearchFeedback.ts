import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import type { FeedbackVerdict, RepoSummary, SearchFeedback, SearchMode } from "@/api/types";
import { applyFeedback, feedbackByRepo, nextFeedbackAction } from "@/lib/feedback";
import { toast } from "@/state/stores";

/**
 * Relevance verdicts for one free-text query. Verdicts are stored per (query, repository);
 * marking the same verdict again clears it.
 */
export function useSearchFeedback(query: string, mode: SearchMode) {
  const qc = useQueryClient();
  const q = query.trim();
  const key = useMemo(() => ["search-feedback", q] as const, [q]);
  const list = useQuery({ queryKey: key, queryFn: () => api.searchFeedback(q), enabled: q.length > 0, staleTime: 30_000 });
  const byRepo = useMemo(() => feedbackByRepo(list.data), [list.data]);

  const mutate = useMutation({
    mutationFn: async ({ repoId, verdict, rank }: { repoId: number; verdict: FeedbackVerdict; rank?: number | null }) => {
      const action = nextFeedbackAction(byRepo.get(repoId), verdict);
      if (action.type === "delete") {
        await api.deleteSearchFeedback(action.id);
        return { removedId: action.id };
      }
      return { upserted: await api.setSearchFeedback({ query: q, repo_id: repoId, verdict: action.verdict, mode, rank: rank ?? null }) };
    },
    onSuccess: (change) => {
      qc.setQueryData<SearchFeedback[]>(key, (prev) => applyFeedback(prev ?? [], change));
      qc.invalidateQueries({ queryKey: ["eval"] });
      qc.invalidateQueries({ queryKey: ["feedback-queries"] });
    },
    onError: (err) => toast.error(`Feedback not saved: ${errorMessage(err)}`),
  });

  const mark = useCallback(
    (repo: Pick<RepoSummary, "id">, verdict: FeedbackVerdict, rank?: number | null) => mutate.mutate({ repoId: repo.id, verdict, rank }),
    [mutate],
  );
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSearchFeedback(id),
    onSuccess: (_ok, id) => {
      qc.setQueryData<SearchFeedback[]>(key, (prev) => applyFeedback(prev ?? [], { removedId: id }));
      qc.invalidateQueries({ queryKey: ["eval"] });
      qc.invalidateQueries({ queryKey: ["feedback-queries"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const missing = useMemo(() => (list.data ?? []).filter((f) => f.verdict === "missing"), [list.data]);
  return { enabled: q.length > 0, byRepo, mark, missing, remove: remove.mutate };
}
