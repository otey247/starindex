import { useQuery } from "@tanstack/react-query";
import { Plus, Search as SearchIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import type { RepoSummary } from "@/api/types";
import { Spinner } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";
import { useDebounced } from "@/lib/useDebounced";

/** Search-as-you-type repository picker (keyword search, no LLM involved). */
export function RepoPicker({
  onPick,
  excludeIds = [],
  placeholder = "Add a repository…",
  autoFocus,
  className,
}: {
  onPick: (repo: RepoSummary) => void;
  excludeIds?: number[];
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const debounced = useDebounced(q.trim(), 160);
  const wrap = useRef<HTMLDivElement>(null);
  const results = useQuery({
    queryKey: ["repo-picker", debounced],
    queryFn: ({ signal }) =>
      debounced
        ? api.search({ query: debounced, mode: "keyword", limit: 8, record: false }, signal).then((r) => r.results.map((x) => x.repo))
        : api.listRepos({ sort: "last_opened", limit: 8 }, signal).then((r) => r.items),
    enabled: open,
    staleTime: 20_000,
  });
  const items = (results.data ?? []).filter((r) => !excludeIds.includes(r.id));

  useEffect(() => setCursor(0), [debounced]);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const pick = (repo: RepoSummary) => {
    onPick(repo);
    setQ("");
    setOpen(false);
  };

  return (
    <div ref={wrap} className={cn("relative", className)}>
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-4" aria-hidden />
        <input
          role="combobox"
          aria-expanded={open}
          aria-controls="repo-picker-list"
          aria-label={placeholder}
          autoFocus={autoFocus}
          value={q}
          placeholder={placeholder}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => Math.min(c + 1, items.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) => Math.max(c - 1, 0));
            } else if (e.key === "Enter" && items[cursor]) {
              e.preventDefault();
              pick(items[cursor]);
            } else if (e.key === "Escape" && open) {
              e.stopPropagation();
              setOpen(false);
            }
          }}
          className="h-8 w-full rounded-sm border border-line-strong bg-bg pl-8 pr-8 text-sm text-fg placeholder:text-fg-4 hover:border-fg-4 focus:border-accent focus:outline-none"
        />
        {results.isFetching && <Spinner className="absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2" />}
      </div>
      {open && (
        <ul
          id="repo-picker-list"
          role="listbox"
          className="absolute inset-x-0 top-full z-30 mt-1 max-h-[320px] overflow-y-auto rounded-md border border-line-strong bg-overlay py-1 shadow-pop animate-pop"
        >
          {items.length === 0 ? (
            <li className="px-3 py-2 text-sm text-fg-3">{results.isLoading ? "Searching…" : debounced ? "No repositories match." : "Type to search your library."}</li>
          ) : (
            items.map((repo, i) => (
              <li key={repo.id} role="option" aria-selected={i === cursor}>
                <button
                  type="button"
                  onMouseMove={() => cursor !== i && setCursor(i)}
                  onClick={() => pick(repo)}
                  className={cn("flex w-full cursor-pointer items-center gap-2.5 px-3 py-1.5 text-left", i === cursor && "bg-line")}
                >
                  <Plus className="size-3.5 shrink-0 text-fg-4" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">
                      <span className="text-fg-3">{repo.owner}/</span>
                      <span className="font-medium text-fg">{repo.name}</span>
                    </span>
                    {repo.description && <span className="block truncate text-xs text-fg-3">{repo.description}</span>}
                  </span>
                  <span className="shrink-0 text-xs text-fg-4 tnum">★ {formatCount(repo.stars)}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
