import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownUp, ChevronLeft, ChevronRight, CopyCheck, GitCompareArrows, Layers, Library as LibraryIcon, LayoutGrid, List, ListFilter, Rows3, Search as SearchIcon, Tag as TagIcon, X } from "lucide-react";
import { useCallback, useMemo, useReducer, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { invalidateRepoData, useCategories, useCollections, useFacets, useRepoList } from "@/api/queries";
import type { IndexLevel, LibrarySort, RepoListRequestInput, RepoSummary } from "@/api/types";
import { CollectionMenu } from "@/components/repo/CollectionMenu";
import { RepoRow } from "@/components/repo/RepoRow";
import { RepoGrid, RepoMiniList, RepoTable, ResultSkeleton } from "@/components/repo/RepoViews";
import { RepoFacts, RepoTitle } from "@/components/repo/RepoBits";
import { FilterSidebar } from "@/components/search/FilterSidebar";
import { Button, Chip, Dialog, Drawer, EmptyState, ErrorState, Field, Input, Menu, Segmented, Tabs, type MenuItem } from "@/components/ui";
import { cn } from "@/lib/cn";
import { activeChips, applyChipRemoval, countActiveFilters, filtersReducer, type FilterAction } from "@/lib/filters";
import { formatNumber, INDEX_LEVEL_LABEL, timeAgo } from "@/lib/format";
import { filtersFromParams, filtersToParams, type ResultView } from "@/lib/urlState";
import { useDebounced } from "@/lib/useDebounced";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { useResultNav } from "@/lib/useResultNav";
import { MAX_COMPARE, toast, useCompareTray } from "@/state/stores";

const PAGE_SIZE = 50;

const SORTS: { value: LibrarySort; label: string; order: "asc" | "desc" }[] = [
  { value: "starred_at", label: "Date starred", order: "desc" },
  { value: "stars", label: "Stars", order: "desc" },
  { value: "pushed_at", label: "Last pushed", order: "desc" },
  { value: "updated_at", label: "Last updated", order: "desc" },
  { value: "last_opened", label: "Recently opened", order: "desc" },
  { value: "priority", label: "Priority", order: "desc" },
  { value: "size", label: "Size", order: "desc" },
  { value: "name", label: "Name", order: "asc" },
];

function OverviewStrip() {
  const overview = useQuery({ queryKey: ["library", "overview"], queryFn: api.libraryOverview, staleTime: 30_000 });
  const [tab, setTab] = useState<"starred" | "rediscovered" | "attention" | "favorites">("starred");
  const o = overview.data;
  if (!o) return <div className="h-[168px] border-b border-line" aria-hidden />;
  const lists: Record<typeof tab, { repos: RepoSummary[]; meta: (r: RepoSummary) => string; empty: string }> = {
    starred: { repos: o.recently_starred, meta: (r) => `starred ${timeAgo(r.starred_at)}`, empty: "Nothing starred yet." },
    rediscovered: {
      repos: o.recently_rediscovered,
      meta: (r) => `starred ${timeAgo(r.starred_at)}`,
      empty: "Repositories you reopen after a long time show up here.",
    },
    attention: {
      repos: o.needs_attention,
      meta: (r) => (r.is_archived ? "archived" : r.access_state !== "ok" ? "inaccessible" : r.maintenance),
      empty: "Nothing archived or inaccessible.",
    },
    favorites: { repos: o.favorites, meta: (r) => `pushed ${timeAgo(r.pushed_at)}`, empty: "Mark a repository as favorite to pin it here." },
  };
  const current = lists[tab];
  return (
    <section aria-label="Library overview" className="border-b border-line pb-4">
      <Tabs
        label="Overview lists"
        value={tab}
        onChange={setTab}
        className="!border-b-0"
        tabs={[
          { value: "starred", label: "Recently starred" },
          { value: "rediscovered", label: "Recently rediscovered" },
          { value: "favorites", label: "Favorites", count: o.favorites.length },
          { value: "attention", label: "Needs attention", count: o.needs_attention.length },
        ]}
      />
      <div className="mt-1 grid gap-x-10 md:grid-cols-2">
        {[current.repos.slice(0, 4), current.repos.slice(4, 8)].map((col, i) =>
          col.length || i === 0 ? <RepoMiniList key={i} repos={col} meta={current.meta} empty={current.empty} /> : null,
        )}
      </div>
    </section>
  );
}

function DuplicatesView() {
  const dupes = useQuery({ queryKey: ["library", "duplicates"], queryFn: api.libraryDuplicates });
  const navigate = useNavigate();
  if (dupes.isLoading) return <ResultSkeleton rows={4} />;
  if (dupes.isError) return <ErrorState className="mt-4" message={errorMessage(dupes.error)} onRetry={() => dupes.refetch()} />;
  if (!dupes.data?.length) {
    return (
      <EmptyState icon={<CopyCheck />} title="No duplicates or overlapping forks found">
        StarIndex looks for forks of repositories you also starred, repositories with the same name under different owners, and
        near-identical projects by embedding similarity.
      </EmptyState>
    );
  }
  const reasonLabel = { fork: "Fork relationship", same_name: "Same name", near_duplicate: "Near duplicate" } as const;
  return (
    <div className="flex flex-col">
      {dupes.data.map((group, i) => (
        <section key={i} className="border-b border-line py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="label">{reasonLabel[group.reason]}</p>
              <p className="mt-0.5 text-sm text-fg-2">{group.detail}</p>
            </div>
            <Button size="sm" icon={<GitCompareArrows className="size-3.5" />} onClick={() => navigate(`/compare?ids=${group.repos.slice(0, MAX_COMPARE).map((r) => r.id).join(",")}`)}>
              Compare
            </Button>
          </div>
          <ul className="mt-2 flex flex-col gap-2">
            {group.repos.map((repo) => (
              <li key={repo.id} className="min-w-0">
                <RepoTitle repo={repo} size="sm" />
                <RepoFacts repo={repo} className="mt-0.5" />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function TagDialog({ open, onClose, repos }: { open: boolean; onClose: () => void; repos: RepoSummary[] }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const apply = useMutation({
    mutationFn: async () => {
      const tags = value.split(",").map((t) => t.trim()).filter(Boolean);
      await Promise.all(repos.map((r) => api.setRepoTags(r.id, [...new Set([...r.tags, ...tags])])));
      return tags;
    },
    onSuccess: (tags) => {
      invalidateRepoData(qc);
      toast.success(`Tagged ${repos.length} repositor${repos.length === 1 ? "y" : "ies"} with ${tags.join(", ")}.`);
      setValue("");
      onClose();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      title={`Tag ${repos.length} repositor${repos.length === 1 ? "y" : "ies"}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!value.trim()} loading={apply.isPending} onClick={() => apply.mutate()}>
            Add tags
          </Button>
        </>
      }
    >
      <Field label="Tags" hint="Comma separated. Existing tags on each repository are kept." htmlFor="bulk-tags">
        <Input id="bulk-tags" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. evaluate, sims-builder" onKeyDown={(e) => e.key === "Enter" && value.trim() && apply.mutate()} />
      </Field>
    </Dialog>
  );
}

export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const narrow = useMediaQuery("(max-width: 767px)");
  const view = (params.get("view") as ResultView | "duplicates" | null) ?? (narrow ? "list" : "table");
  const sort = (SORTS.find((s) => s.value === params.get("sort")) ?? SORTS[0]!).value;
  const order = (params.get("order") as "asc" | "desc" | null) ?? SORTS.find((s) => s.value === sort)!.order;
  const page = Math.max(1, Number(params.get("page")) || 1);
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const [text, setText] = useState(params.get("text") ?? "");
  const debouncedText = useDebounced(text.trim(), 200);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [tagging, setTagging] = useState(false);
  const [, force] = useReducer((x: number) => x + 1, 0);

  const setParam = useCallback(
    (patch: Record<string, string | null>, resetPage = true) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      if (resetPage && !("page" in patch)) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const dispatchFilter = useCallback(
    (action: FilterAction) => {
      const nextFilters = filtersReducer(filters, action);
      const next = filtersToParams(nextFilters);
      for (const key of ["view", "sort", "order"]) {
        const v = params.get(key);
        if (v) next.set(key, v);
      }
      setParams(next, { replace: true });
    },
    [filters, params, setParams],
  );

  const request = useMemo<RepoListRequestInput>(
    () => ({ filters, text: debouncedText, sort, order, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    [filters, debouncedText, sort, order, page],
  );
  const list = useRepoList(request, view !== "duplicates");
  const facets = useFacets({ filters, text: debouncedText }, view !== "duplicates");
  const repos = list.data?.items ?? [];
  const nav = useResultNav(repos, view !== "duplicates");
  const totalPages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;
  const activeCount = countActiveFilters(filters);
  const collections = useCollections();
  const categories = useCategories();
  const chips = activeChips(filters, {
    collections: Object.fromEntries((collections.data ?? []).map((c) => [c.id, c.name])),
    categories: Object.fromEntries((categories.data ?? []).map((c) => [c.slug, c.name])),
  });

  const toggle = (id: number, value: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (value) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  const toggleAll = (value: boolean) => setSelected(value ? new Set(repos.map((r) => r.id)) : new Set());
  const selectedRepos = repos.filter((r) => selected.has(r.id));
  const selectedIds = [...selected];

  const indexBatch = useMutation({
    mutationFn: (level: IndexLevel) => api.indexBatch({ level, repo_ids: selectedIds }),
    onSuccess: (res, level) => {
      invalidateRepoData(qc);
      qc.invalidateQueries({ queryKey: ["sync"] });
      toast.success(`Queued ${res.queued} for ${INDEX_LEVEL_LABEL[level]?.toLowerCase()} indexing${res.skipped ? `, ${res.skipped} skipped` : ""}.`, {
        label: "View progress",
        onClick: () => navigate("/sync"),
      });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const levelItems: MenuItem[] = (["metadata", "docs", "deep"] as IndexLevel[]).map((level) => ({
    key: level,
    label: `${INDEX_LEVEL_LABEL[level]} indexing`,
    hint: level === "deep" ? "source code" : level === "docs" ? "README + docs" : "no content",
    onSelect: () => indexBatch.mutate(level),
  }));

  const sortItems: MenuItem[] = SORTS.map((s) => ({
    key: s.value,
    label: s.label,
    checked: s.value === sort,
    onSelect: () => setParam({ sort: s.value, order: s.order }),
  }));

  const sidebar = <FilterSidebar filters={filters} dispatch={dispatchFilter} facets={facets.data} />;
  const tray = useCompareTray();

  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 pb-24 pt-6 md:px-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Library</h1>
          <p className="mt-0.5 text-sm text-fg-3">
            {list.data ? (
              <>
                <span className="text-fg-2 tnum">{formatNumber(list.data.total)}</span> repositor{list.data.total === 1 ? "y" : "ies"}
                {activeCount > 0 || debouncedText ? " match" : " starred"}
              </>
            ) : (
              "Every repository you have starred, organised your way."
            )}
          </p>
        </div>
        <Tabs
          label="Library view"
          className="!border-b-0"
          value={view === "duplicates" ? "duplicates" : "all"}
          onChange={(v) => setParam({ view: v === "duplicates" ? "duplicates" : null })}
          tabs={[
            { value: "all", label: "All repositories" },
            { value: "duplicates", label: "Duplicates & forks" },
          ]}
        />
      </header>

      {view === "duplicates" ? (
        <div className="mt-4 max-w-[980px]">
          <DuplicatesView />
        </div>
      ) : (
        <>
          <div className="mt-5">
            <OverviewStrip />
          </div>
          <div className="mt-5 grid gap-x-8 grid-cols-1 lg:grid-cols-[236px_minmax(0,1fr)]">
            <div className="hidden lg:block">
              <div className="sticky top-4 max-h-[calc(100dvh-5.5rem)] overflow-y-auto pr-2">{sidebar}</div>
            </div>
            <section className="min-w-0" aria-label="Repositories">
              <div className="flex flex-wrap items-center gap-2 border-b border-line pb-2">
                <Button size="sm" className="lg:hidden" icon={<ListFilter className="size-3.5" />} onClick={() => setFiltersOpen(true)}>
                  Filters{activeCount ? ` · ${activeCount}` : ""}
                </Button>
                <div className="relative min-w-[180px] flex-1 sm:max-w-[340px]">
                  <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-4" aria-hidden />
                  <Input
                    value={text}
                    onChange={(e) => {
                      setText(e.target.value);
                      if (page > 1) setParam({ page: null });
                    }}
                    placeholder="Quick filter by name, description or topic"
                    aria-label="Quick filter"
                    className="!h-7 pl-8 text-sm"
                  />
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <Menu
                    align="end"
                    width={200}
                    items={sortItems}
                    footer={
                      <button type="button" className="flex w-full cursor-pointer items-center gap-2 py-1 text-xs text-fg-3 hover:text-fg" onClick={() => setParam({ order: order === "asc" ? "desc" : "asc" })}>
                        <ArrowDownUp className="size-3" /> {order === "asc" ? "Ascending" : "Descending"} — switch
                      </button>
                    }
                    trigger={(p) => (
                      <Button size="sm" {...p}>
                        Sort: {SORTS.find((s) => s.value === sort)?.label}
                      </Button>
                    )}
                  />
                  <Segmented
                    size="sm"
                    label="View"
                    value={view as ResultView}
                    onChange={(v) => setParam({ view: v === "table" && !narrow ? null : v }, false)}
                    options={[
                      { value: "table", label: <Rows3 className="size-3.5" aria-label="Table" />, title: "Compact table" },
                      { value: "list", label: <List className="size-3.5" aria-label="List" />, title: "Detailed list" },
                      { value: "grid", label: <LayoutGrid className="size-3.5" aria-label="Grid" />, title: "Card grid" },
                    ]}
                  />
                </div>
              </div>

              {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 border-b border-line py-2">
                  {chips.map((chip) => (
                    <Chip
                      key={chip.id}
                      mono
                      onRemove={() => {
                        const next = filtersToParams(applyChipRemoval(filters, chip));
                        for (const key of ["view", "sort", "order"]) {
                          const v = params.get(key);
                          if (v) next.set(key, v);
                        }
                        setParams(next, { replace: true });
                      }}
                    >
                      {chip.label}
                    </Chip>
                  ))}
                  <button type="button" className="ml-1 cursor-pointer text-xs text-fg-3 hover:text-fg" onClick={() => dispatchFilter({ type: "clear" })}>
                    Clear all
                  </button>
                </div>
              )}

              {list.isError ? (
                <ErrorState className="mt-4" message={errorMessage(list.error)} onRetry={() => list.refetch()} />
              ) : !list.data ? (
                <ResultSkeleton />
              ) : repos.length === 0 ? (
                <EmptyState
                  icon={<LibraryIcon />}
                  title={activeCount > 0 || debouncedText ? "Nothing matches these filters" : "Your library is empty"}
                  action={
                    activeCount > 0 || debouncedText ? (
                      <Button
                        onClick={() => {
                          setText("");
                          dispatchFilter({ type: "clear" });
                        }}
                      >
                        Clear filters
                      </Button>
                    ) : (
                      <Button variant="primary" onClick={() => navigate("/sync")}>
                        Sync starred repositories
                      </Button>
                    )
                  }
                >
                  {activeCount > 0 || debouncedText
                    ? "Loosen a filter or clear the quick filter to see more of your library."
                    : "Connect GitHub and run the first sync to import every repository you have starred."}
                </EmptyState>
              ) : (
                <div className={cn("transition-opacity", list.isPlaceholderData && "opacity-60")}>
                  {view === "table" && (
                    <RepoTable
                      repos={repos}
                      selected={selected}
                      onToggle={toggle}
                      onToggleAll={toggleAll}
                      focusedId={nav.focusedId}
                      sort={{
                        key: sort,
                        order,
                        onSort: (key) =>
                          setParam({ sort: key, order: key === sort ? (order === "asc" ? "desc" : "asc") : (SORTS.find((s) => s.value === key)?.order ?? "desc") }),
                      }}
                    />
                  )}
                  {view === "list" &&
                    repos.map((repo) => (
                      <RepoRow
                        key={repo.id}
                        repo={repo}
                        selectable
                        selected={selected.has(repo.id)}
                        onSelect={(v) => toggle(repo.id, v)}
                        focused={nav.focusedId === repo.id}
                        onFocus={() => nav.setFocusedId(repo.id)}
                        showStarred
                        showIndex
                        onTopic={(topic) => dispatchFilter({ type: "toggle", key: "topics", value: topic })}
                      />
                    ))}
                  {view === "grid" && <RepoGrid repos={repos} focusedId={nav.focusedId} selected={selected} onToggle={toggle} />}

                  {totalPages > 1 && (
                    <div className="flex items-center justify-end gap-2 py-4">
                      <Button size="sm" disabled={page <= 1} onClick={() => setParam({ page: String(page - 1) })} icon={<ChevronLeft className="size-3.5" />}>
                        Previous
                      </Button>
                      <span className="text-xs text-fg-3 tnum">
                        Page {page} of {totalPages}
                      </span>
                      <Button size="sm" disabled={page >= totalPages} onClick={() => setParam({ page: String(page + 1) })}>
                        Next
                        <ChevronRight className="size-3.5" />
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </section>
          </div>
        </>
      )}

      {selected.size > 0 && view !== "duplicates" && (
        <div className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex justify-center px-3 max-md:bottom-[70px]">
          <div
            role="toolbar"
            aria-label="Bulk actions"
            className="pointer-events-auto flex max-w-full flex-wrap items-center gap-1.5 rounded-lg border border-line-strong bg-overlay py-1.5 pl-3.5 pr-1.5 shadow-pop animate-rise"
          >
            <span className="mr-1 text-sm font-medium text-fg tnum">{selected.size} selected</span>
            <CollectionMenu repoIds={selectedIds} />
            <Button size="sm" icon={<TagIcon className="size-3.5" />} onClick={() => setTagging(true)}>
              Tag
            </Button>
            <Menu
              width={240}
              header={<span className="label">Set index level</span>}
              items={levelItems}
              trigger={(p) => (
                <Button size="sm" icon={<Layers className="size-3.5" />} loading={indexBatch.isPending} {...p}>
                  Index level
                </Button>
              )}
            />
            <Button
              size="sm"
              variant="primary"
              icon={<GitCompareArrows className="size-3.5" />}
              disabled={selected.size < 2 || selected.size > MAX_COMPARE}
              title={selected.size > MAX_COMPARE ? `Compare supports up to ${MAX_COMPARE}` : selected.size < 2 ? "Select at least two" : undefined}
              onClick={() => {
                tray.clear();
                navigate(`/compare?ids=${selectedIds.join(",")}`);
              }}
            >
              Compare
            </Button>
            <button type="button" aria-label="Clear selection" onClick={() => { setSelected(new Set()); force(); }} className="inline-flex size-7 cursor-pointer items-center justify-center rounded-sm text-fg-3 hover:bg-hover hover:text-fg">
              <X className="size-3.5" />
            </button>
          </div>
        </div>
      )}

      <Drawer open={filtersOpen} onClose={() => setFiltersOpen(false)} title="Filters" width={340}>
        <div className="p-4">{sidebar}</div>
      </Drawer>
      <TagDialog open={tagging} onClose={() => setTagging(false)} repos={selectedRepos} />
    </div>
  );
}
