import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookmarkPlus,
  ChevronLeft,
  ChevronRight,
  Clock,
  Info,
  LayoutGrid,
  List,
  ListFilter,
  Rows3,
  Save,
  Search as SearchIcon,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { keys, useCategories, useCollections, useFacets, useSearch, useSettings, useSystemStatus } from "@/api/queries";
import type { RepoFilters, SavedSearch, SearchRequestInput, SearchResponse, SearchScope, SearchSort } from "@/api/types";
import { RepoRow } from "@/components/repo/RepoRow";
import { RepoGrid, RepoTable, ResultSkeleton } from "@/components/repo/RepoViews";
import { FilterSidebar } from "@/components/search/FilterSidebar";
import { SearchBar, type BarMode, type SearchBarHandle } from "@/components/search/SearchBar";
import { Button, Chip, Dialog, Drawer, EmptyState, ErrorState, Field, IconButton, Input, Kbd, Menu, ProgressBar, Segmented, type MenuItem } from "@/components/ui";
import { cn } from "@/lib/cn";
import { activeChips, applyChipRemoval, countActiveFilters, filtersReducer, type FilterAction } from "@/lib/filters";
import { formatMs, formatNumber, timeAgo } from "@/lib/format";
import { OPERATORS } from "@/lib/query";
import { searchStateFromParams, searchStateToParams, type ResultView, type SearchUrlState } from "@/lib/urlState";
import { useResultNav } from "@/lib/useResultNav";
import { toast } from "@/state/stores";

const PAGE_SIZE = 25;

const EXAMPLES = [
  "agentic orchestration with MCP support",
  "open-source alternatives to Azure API Management",
  "React component libraries for visually impressive dashboards",
  "generate cinematic animations from code",
  "Python libraries that process PDFs locally",
  "semantic search without a cloud service",
];

const SORT_LABEL: Record<SearchSort, string> = {
  relevance: "Relevance",
  stars: "Stars",
  pushed_at: "Recently pushed",
  starred_at: "Recently starred",
  name: "Name",
};

function requestFromState(state: SearchUrlState): SearchRequestInput {
  return {
    query: state.q,
    mode: state.mode,
    scope: state.scope,
    filters: state.filters,
    sort: state.sort,
    limit: PAGE_SIZE,
    offset: (state.page - 1) * PAGE_SIZE,
    explain: state.explain,
  };
}

function SaveSearchDialog({ open, onClose, request }: { open: boolean; onClose: () => void; request: SearchRequestInput }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  useEffect(() => {
    if (open) setName(request.query.slice(0, 80));
  }, [open, request.query]);
  const save = useMutation({
    mutationFn: () => api.createSavedSearch(name.trim(), { ...request, offset: 0, record: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.saved });
      toast.success("Search saved.");
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Save this search"
      description="Saved searches keep the query, mode and every filter. You can also turn one into a smart collection."
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim()} loading={save.isPending} onClick={() => save.mutate()}>
            Save search
          </Button>
        </>
      }
    >
      <Field label="Name" htmlFor="saved-search-name" error={save.isError ? errorMessage(save.error) : undefined}>
        <Input
          id="saved-search-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && save.mutate()}
          maxLength={120}
        />
      </Field>
    </Dialog>
  );
}

function stateFromSaved(saved: SavedSearch): Partial<SearchUrlState> {
  const r = saved.request;
  return { q: r.query, mode: r.mode, scope: r.scope, sort: r.sort, filters: r.filters as RepoFilters, page: 1 };
}

/** Quick access to saved and recent searches while results are on screen. */
function SavedMenu({ onApply }: { onApply: (partial: Partial<SearchUrlState>) => void }) {
  const saved = useQuery({ queryKey: keys.saved, queryFn: api.savedSearches });
  const history = useQuery({ queryKey: keys.history, queryFn: () => api.searchHistory(12) });
  const items: (MenuItem | "separator")[] = [];
  for (const s of saved.data ?? []) items.push({ key: `s${s.id}`, label: s.name, icon: <BookmarkPlus />, onSelect: () => onApply(stateFromSaved(s)) });
  const recent = (history.data ?? []).slice(0, 6);
  if (items.length && recent.length) items.push("separator");
  for (const h of recent) {
    items.push({ key: `h${h.id}`, label: h.query || "(filters only)", icon: <Clock />, hint: String(h.result_count), onSelect: () => onApply({ q: h.query, mode: h.mode as SearchUrlState["mode"], scope: h.scope as SearchScope, filters: h.filters, page: 1 }) });
  }
  if (items.length === 0) items.push({ key: "none", label: "Nothing saved yet", disabled: true, onSelect: () => undefined });
  return (
    <Menu
      align="end"
      width={300}
      header={<span className="label">Saved and recent searches</span>}
      items={items}
      trigger={(p) => (
        <IconButton label="Saved and recent searches" size="sm" {...p}>
          <Clock className="size-3.5" />
        </IconButton>
      )}
    />
  );
}

function StartPanel({
  onRun,
  onApply,
}: {
  onRun: (query: string) => void;
  onApply: (partial: Partial<SearchUrlState>) => void;
}) {
  const qc = useQueryClient();
  const saved = useQuery({ queryKey: keys.saved, queryFn: api.savedSearches });
  const history = useQuery({ queryKey: keys.history, queryFn: () => api.searchHistory(12) });
  const status = useSystemStatus();
  const removeSaved = useMutation({
    mutationFn: (id: number) => api.deleteSavedSearch(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.saved }),
  });
  const clearHistory = useMutation({
    mutationFn: api.clearSearchHistory,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.history }),
  });
  const counts = status.data?.counts;
  return (
    <div className="grid max-w-[1180px] gap-x-16 gap-y-9 pt-8 grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] animate-rise">
      <section>
        <h2 className="label">Try</h2>
        <ul className="mt-2 flex flex-col">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button
                type="button"
                onClick={() => onRun(ex)}
                className="group flex w-full cursor-pointer items-center gap-3 border-b border-line py-2.5 text-left text-md text-fg-2 transition-colors hover:text-fg"
              >
                <SearchIcon className="size-3.5 shrink-0 text-fg-4 group-hover:text-accent" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{ex}</span>
                <ChevronRight className="size-3.5 shrink-0 text-fg-4 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
              </button>
            </li>
          ))}
        </ul>

        <h2 className="label mt-9">Operators</h2>
        <dl className="mt-2 flex flex-col gap-y-1.5">
          {OPERATORS.map((op) => (
            <div key={op.op} className="grid grid-cols-[minmax(0,210px)_1fr] items-baseline gap-4 text-sm">
              <dt className="mono truncate text-xs text-fg">{op.example}</dt>
              <dd className="min-w-0 text-fg-3">{op.description}</dd>
            </div>
          ))}
          <div className="grid grid-cols-[minmax(0,210px)_1fr] items-baseline gap-4 text-sm">
            <dt className="mono truncate text-xs text-fg">"exact phrase" -term OR</dt>
            <dd className="text-fg-3">Phrases, exclusion, alternatives</dd>
          </div>
        </dl>
      </section>

      <section className="flex flex-col gap-8">
        {counts && (
          <p className="text-sm leading-relaxed text-fg-3">
            Searching <span className="font-medium text-fg tnum">{formatNumber(counts.starred)}</span> starred repositories,{" "}
            <span className="font-medium text-fg tnum">{formatNumber(counts.documents)}</span> indexed files and{" "}
            <span className="font-medium text-fg tnum">{formatNumber(counts.chunks)}</span> passages. Everything is stored and searched on this machine.
          </p>
        )}
        <div>
          <h2 className="label">Saved searches</h2>
          {saved.data && saved.data.length > 0 ? (
            <ul className="mt-1.5 flex flex-col">
              {saved.data.map((s) => (
                <li key={s.id} className="group flex items-center gap-2 border-b border-line py-1.5">
                  <button type="button" onClick={() => onApply(stateFromSaved(s))} className="min-w-0 flex-1 cursor-pointer text-left">
                    <span className="block truncate text-base font-medium text-fg group-hover:underline">{s.name}</span>
                    <span className="mono block truncate text-xs text-fg-3">
                      {s.request.query || "(filters only)"} · {s.request.mode}
                    </span>
                  </button>
                  <IconButton label="Delete saved search" size="sm" className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100" onClick={() => removeSaved.mutate(s.id)}>
                    <Trash2 className="size-3.5" />
                  </IconButton>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-sm text-fg-4">Run a search and choose Save to keep it here.</p>
          )}
        </div>
        <div>
          <div className="flex items-center justify-between">
            <h2 className="label">Recent</h2>
            {history.data && history.data.length > 0 && (
              <button type="button" onClick={() => clearHistory.mutate()} className="cursor-pointer text-xs text-fg-4 hover:text-fg">
                Clear history
              </button>
            )}
          </div>
          {history.data && history.data.length > 0 ? (
            <ul className="mt-1.5 flex flex-col">
              {history.data.map((h) => (
                <li key={h.id}>
                  <button
                    type="button"
                    onClick={() => onApply({ q: h.query, mode: h.mode as SearchUrlState["mode"], scope: h.scope as SearchScope, filters: h.filters, page: 1 })}
                    className="group flex w-full cursor-pointer items-baseline gap-3 py-1 text-left"
                  >
                    <Clock className="size-3 shrink-0 translate-y-[1px] text-fg-4" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-sm text-fg-2 group-hover:text-fg">{h.query || "(filters only)"}</span>
                    <span className="shrink-0 text-xs text-fg-4 tnum">
                      {h.result_count} · {timeAgo(h.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-sm text-fg-4">Your recent searches will appear here.</p>
          )}
        </div>
      </section>
    </div>
  );
}

function ResultHeader({ data }: { data: SearchResponse }) {
  const parts = [
    `${formatNumber(data.total)} ${data.scope === "repos" ? (data.total === 1 ? "repository" : "repositories") : data.total === 1 ? "passage" : "passages"}`,
    formatMs(data.took_ms),
    data.mode,
  ];
  if (data.reranked) parts.push("reranked");
  // "fastembed:BAAI/bge-small-en-v1.5" -> "bge-small-en-v1.5"
  if (data.embedding_model && data.mode !== "keyword") parts.push(data.embedding_model.split(/[:/]/).pop() ?? data.embedding_model);
  return (
    <p className="mono min-w-0 truncate text-xs text-fg-3" data-testid="result-summary" title={data.embedding_model ?? undefined}>
      {parts.join("  ·  ")}
    </p>
  );
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const settings = useSettings();
  const defaultMode = settings.data?.search.default_mode ?? "hybrid";
  const state = useMemo(() => searchStateFromParams(params, { mode: defaultMode }), [params, defaultMode]);
  const [draft, setDraft] = useState(state.q);
  const [barMode, setBarMode] = useState<BarMode>(state.mode);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const barRef = useRef<SearchBarHandle>(null);

  // Keep the input in step with URL-driven changes (back/forward, saved searches).
  useEffect(() => setDraft(state.q), [state.q]);
  useEffect(() => {
    setBarMode((m) => (m === "ask" || m === "research" ? m : state.mode));
  }, [state.mode]);
  useEffect(() => {
    if (params.get("focus") === "1") barRef.current?.focus();
  }, [params]);

  const update = useCallback(
    (partial: Partial<SearchUrlState>, replace = false) => {
      const next = { ...state, page: 1, ...partial };
      setParams(searchStateToParams(next), { replace });
    },
    [state, setParams],
  );

  const dispatchFilter = useCallback(
    (action: FilterAction) => update({ filters: filtersReducer(state.filters, action) }, true),
    [state.filters, update],
  );

  const submit = (text: string, modeOverride?: BarMode) => {
    const q = text.trim();
    const mode = modeOverride ?? barMode;
    if (mode === "ask" || mode === "research") {
      if (q) navigate(`/${mode}?q=${encodeURIComponent(q)}`);
      return;
    }
    update({ q, mode });
  };

  const hasQuery = state.q.trim().length > 0;
  const activeCount = countActiveFilters(state.filters);
  const shouldSearch = hasQuery || activeCount > 0;
  const request = useMemo(() => (shouldSearch ? requestFromState(state) : null), [shouldSearch, state]);
  const search = useSearch(request);
  const data = search.data;

  const facetReq = useMemo(() => ({ filters: state.filters }), [state.filters]);
  const fallbackFacets = useFacets(facetReq, !data?.facets);
  const facets = data?.facets ?? fallbackFacets.data;

  const collections = useCollections();
  const categories = useCategories();
  const chips = useMemo(
    () =>
      activeChips(state.filters, {
        collections: Object.fromEntries((collections.data ?? []).map((c) => [c.id, c.name])),
        categories: Object.fromEntries((categories.data ?? []).map((c) => [c.slug, c.name])),
      }),
    [state.filters, collections.data, categories.data],
  );

  const repos = useMemo(() => (data?.results ?? []).map((r) => r.repo), [data]);
  const nav = useResultNav(repos, shouldSearch);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const corrections = data?.parsed.corrections ?? [];

  const searchInstead = () => {
    let q = state.q;
    for (const c of corrections) q = q.replace(new RegExp(`\\b${c.original.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"), `"${c.original}"`);
    setDraft(q);
    update({ q });
  };

  const viewOptions: { value: ResultView; label: React.ReactNode; title: string }[] = [
    { value: "list", label: <List className="size-3.5" aria-label="Detailed list" />, title: "Detailed list with matched passages" },
    { value: "table", label: <Rows3 className="size-3.5" aria-label="Compact table" />, title: "Compact table" },
    { value: "grid", label: <LayoutGrid className="size-3.5" aria-label="Card grid" />, title: "Card grid" },
  ];

  const sortItems: MenuItem[] = (Object.keys(SORT_LABEL) as SearchSort[]).map((s) => ({
    key: s,
    label: SORT_LABEL[s],
    checked: state.sort === s,
    onSelect: () => update({ sort: s }),
  }));

  const sidebar = <FilterSidebar filters={state.filters} dispatch={dispatchFilter} facets={facets} />;

  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 pb-16 pt-6 md:px-8">
      <div>
        <SearchBar
          ref={barRef}
          value={draft}
          onChange={setDraft}
          onSubmit={(v) => submit(v)}
          mode={barMode}
          onMode={(m) => {
            setBarMode(m);
            if (m !== "ask" && m !== "research" && shouldSearch) update({ mode: m, q: draft.trim() });
          }}
          onOpenRepo={(id) => navigate(`/repo/${id}`)}
          loading={search.isFetching}
          autoFocus={!shouldSearch}
        />
      </div>

      {!shouldSearch ? (
        <div>
          <StartPanel
            onRun={(q) => {
              setDraft(q);
              submit(q, barMode === "ask" || barMode === "research" ? "hybrid" : barMode);
              if (barMode === "ask" || barMode === "research") setBarMode("hybrid");
            }}
            onApply={(partial) => update(partial)}
          />
        </div>
      ) : (
        <div className="mt-6 grid gap-x-8 grid-cols-1 lg:grid-cols-[236px_minmax(0,1fr)]">
          <div className="hidden lg:block">
            <div className="sticky top-4 max-h-[calc(100dvh-5.5rem)] overflow-y-auto pr-2">{sidebar}</div>
          </div>

          <section aria-label="Search results" className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line pb-2">
              <Button size="sm" className="lg:hidden" icon={<ListFilter className="size-3.5" />} onClick={() => setFiltersOpen(true)}>
                Filters{activeCount ? ` · ${activeCount}` : ""}
              </Button>
              <div className="min-w-0 flex-1">{search.isFetching ? <p className="mono text-xs text-fg-3" role="status">Searching {state.mode}…</p> : data ? <ResultHeader data={data} /> : null}</div>
              <Segmented
                size="sm"
                label="Search scope"
                value={state.scope}
                onChange={(scope) => update({ scope })}
                options={[
                  { value: "repos", label: "Repos", title: "Rank repositories" },
                  { value: "docs", label: "Docs", title: "Search documentation passages" },
                  { value: "code", label: "Code", title: "Search indexed source code" },
                ]}
              />
              <Menu
                align="end"
                width={190}
                items={sortItems}
                trigger={(p) => (
                  <Button size="sm" {...p}>
                    Sort: {SORT_LABEL[state.sort]}
                  </Button>
                )}
              />
              <Segmented size="sm" label="Result view" value={state.view} onChange={(view) => update({ view, page: state.page })} options={viewOptions} />
              <IconButton label={state.explain ? "Hide ranking signals" : "Explain ranking"} size="sm" active={state.explain} onClick={() => update({ explain: !state.explain, page: state.page })}>
                <Info className="size-3.5" />
              </IconButton>
              <IconButton label="Save this search" size="sm" onClick={() => setSaving(true)}>
                <Save className="size-3.5" />
              </IconButton>
              <SavedMenu onApply={(partial) => update(partial)} />
            </div>

            <div className="h-[2px]" aria-hidden>
              {search.isFetching && <ProgressBar className="!h-[2px] !rounded-none" label="Searching" />}
            </div>

            {chips.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 border-b border-line py-2">
                {chips.map((chip) => (
                  <Chip key={chip.id} mono onRemove={() => update({ filters: applyChipRemoval(state.filters, chip) }, true)}>
                    {chip.label}
                  </Chip>
                ))}
                <button type="button" className="ml-1 cursor-pointer text-xs text-fg-3 hover:text-fg" onClick={() => dispatchFilter({ type: "clear" })}>
                  Clear all
                </button>
              </div>
            )}

            {data && (corrections.length > 0 || data.notices.length > 0 || data.parsed.expansions.length > 0 || data.mode !== data.requested_mode) && (
              <div className="flex flex-col gap-1 border-b border-line py-2.5 text-sm" data-testid="search-notices">
                {corrections.length > 0 && (
                  <p className="text-fg-2">
                    Showing results for{" "}
                    <span className="font-semibold text-fg">
                      {corrections.reduce((q, c) => q.replace(new RegExp(c.original.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), c.corrected), data.query)}
                    </span>
                    .{" "}
                    <button type="button" className="link cursor-pointer" onClick={searchInstead}>
                      Search instead for {corrections.map((c) => c.original).join(", ")}
                    </button>
                  </p>
                )}
                {data.parsed.expansions.length > 0 && (
                  <p className="text-fg-3">
                    Also searched: <span className="mono text-xs text-fg-2">{data.parsed.expansions.join(" · ")}</span>
                  </p>
                )}
                {data.notices.map((n) => (
                  <p key={n} className="flex items-start gap-2 text-fg-2">
                    <Info className="mt-[3px] size-3.5 shrink-0 text-fg-3" aria-hidden />
                    {n}
                  </p>
                ))}
              </div>
            )}

            {search.isError ? (
              <ErrorState className="mt-4" title="Search failed" message={errorMessage(search.error)} onRetry={() => search.refetch()} />
            ) : !data ? (
              <ResultSkeleton />
            ) : data.results.length === 0 ? (
              <EmptyState
                icon={<SearchIcon />}
                title="No repositories match"
                action={
                  <>
                    {activeCount > 0 && <Button onClick={() => dispatchFilter({ type: "clear" })}>Clear {activeCount} filter{activeCount === 1 ? "" : "s"}</Button>}
                    {state.mode !== "hybrid" && <Button onClick={() => update({ mode: "hybrid" })}>Try hybrid search</Button>}
                    {state.filters.star_state === "starred" && (
                      <Button variant="ghost" onClick={() => dispatchFilter({ type: "setStarState", value: "all" })}>
                        Include unstarred repositories
                      </Button>
                    )}
                  </>
                }
              >
                Nothing in your library matched <span className="font-medium text-fg-2">“{data.query}”</span>
                {activeCount > 0 ? " with the current filters" : ""}. Semantic and hybrid modes find related projects even when the words differ.
              </EmptyState>
            ) : (
              <div className={cn("transition-opacity", search.isPlaceholderData && "opacity-60")}>
                {state.view === "list" &&
                  data.results.map((r, i) => (
                    <RepoRow
                      key={r.repo.id}
                      repo={r.repo}
                      rank={(state.page - 1) * PAGE_SIZE + i + 1}
                      matches={r.matches}
                      signals={r.signals}
                      score={r.score}
                      highlights={r.field_highlights}
                      focused={nav.focusedId === r.repo.id}
                      onFocus={() => nav.setFocusedId(r.repo.id)}
                      explain={state.explain}
                      onTopic={(topic) => dispatchFilter({ type: "toggle", key: "topics", value: topic })}
                    />
                  ))}
                {state.view === "table" && <RepoTable repos={repos} focusedId={nav.focusedId} showRank />}
                {state.view === "grid" && <RepoGrid repos={repos} focusedId={nav.focusedId} />}

                <div className="flex items-center justify-between gap-3 px-1 py-4">
                  <p className="hidden items-center gap-1.5 text-xs text-fg-4 sm:flex">
                    <Kbd>j</Kbd>
                    <Kbd>k</Kbd> move <Kbd>Enter</Kbd> open <Kbd>x</Kbd> compare <Kbd>?</Kbd> all shortcuts
                  </p>
                  {totalPages > 1 && (
                    <div className="ml-auto flex items-center gap-2">
                      <Button size="sm" disabled={state.page <= 1} onClick={() => update({ page: state.page - 1 })} icon={<ChevronLeft className="size-3.5" />}>
                        Previous
                      </Button>
                      <span className="text-xs text-fg-3 tnum">
                        Page {state.page} of {totalPages}
                      </span>
                      <Button size="sm" disabled={state.page >= totalPages} onClick={() => update({ page: state.page + 1 })}>
                        Next
                        <ChevronRight className="size-3.5" />
                      </Button>
                    </div>
                  )}
                </div>

                {state.scope === "repos" && hasQuery && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line pt-4 text-sm text-fg-3">
                    <span>Want an answer rather than a list?</span>
                    <Button size="sm" onClick={() => navigate(`/ask?q=${encodeURIComponent(state.q)}`)}>
                      Ask AI about this
                    </Button>
                    <Button size="sm" onClick={() => navigate(`/research?q=${encodeURIComponent(state.q)}`)}>
                      Research it
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<BookmarkPlus className="size-3.5" />}
                      onClick={() => navigate("/collections?new=smart", { state: { smartQuery: request } })}
                    >
                      Smart collection from this search
                    </Button>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      <Drawer open={filtersOpen} onClose={() => setFiltersOpen(false)} title="Filters" width={340}>
        <div className="p-4">{sidebar}</div>
      </Drawer>
      {request && <SaveSearchDialog open={saving} onClose={() => setSaving(false)} request={request} />}
      <span className="sr-only" aria-live="polite">
        {data ? `${data.total} results` : ""}
      </span>
    </div>
  );
}
