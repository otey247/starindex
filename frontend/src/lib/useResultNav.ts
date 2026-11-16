import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { RepoSummary } from "@/api/types";
import { useCompareTray } from "@/state/stores";
import { useHotkeys } from "./hotkeys";

/** j / k / Enter / o / x navigation over a list of repositories. */
export function useResultNav(repos: RepoSummary[], enabled = true, onVerdict?: (repo: RepoSummary, verdict: "relevant" | "not_relevant") => void) {
  const navigate = useNavigate();
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const toggleTray = useCompareTray((s) => s.toggle);

  useEffect(() => {
    if (focusedId !== null && !repos.some((r) => r.id === focusedId)) setFocusedId(null);
  }, [repos, focusedId]);

  const move = useCallback(
    (delta: number) => {
      if (repos.length === 0) return;
      const index = repos.findIndex((r) => r.id === focusedId);
      const next = index === -1 ? (delta > 0 ? 0 : repos.length - 1) : Math.max(0, Math.min(repos.length - 1, index + delta));
      const id = repos[next]!.id;
      setFocusedId(id);
      requestAnimationFrame(() => {
        document.querySelector(`[data-result-row][data-repo-id="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    },
    [repos, focusedId],
  );

  const focused = repos.find((r) => r.id === focusedId) ?? null;

  useHotkeys(
    [
      { keys: "j", handler: () => move(1) },
      { keys: "k", handler: () => move(-1) },
      { keys: "enter", handler: () => (focused ? navigate(`/repo/${focused.id}`) : false) },
      { keys: "o", handler: () => (focused ? void window.open(focused.html_url, "_blank", "noopener,noreferrer") : false) },
      { keys: "x", handler: () => (focused ? toggleTray(focused.id, focused.full_name) : false) },
      { keys: "plus", handler: () => (focused && onVerdict ? onVerdict(focused, "relevant") : false) },
      { keys: "minus", handler: () => (focused && onVerdict ? onVerdict(focused, "not_relevant") : false) },
      { keys: "escape", handler: () => (focusedId !== null ? setFocusedId(null) : false) },
    ],
    enabled,
  );

  return { focusedId, setFocusedId };
}
