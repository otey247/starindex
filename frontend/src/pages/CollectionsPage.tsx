import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FolderKanban, Pencil, Plus, Sparkles, Tag as TagIcon, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { invalidateRepoData, keys, useCategories, useCollections, useTags } from "@/api/queries";
import type { Category, SearchMode, SearchRequestInput } from "@/api/types";
import { NewCollectionDialog } from "@/components/repo/CollectionMenu";
import { Button, Dialog, EmptyState, ErrorState, Field, IconButton, Input, Segmented, Select, Skeleton, Tabs, Textarea } from "@/components/ui";
import { countActiveFilters } from "@/lib/filters";
import { formatNumber, plural, timeAgo } from "@/lib/format";
import { toast } from "@/state/stores";
import { EMPTY_FILTERS } from "@/api/types";

type TabKey = "collections" | "reports" | "tags" | "categories";

function SmartCollectionDialog({ open, onClose, initial }: { open: boolean; onClose: () => void; initial?: SearchRequestInput | null }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const saved = useQuery({ queryKey: keys.saved, queryFn: api.savedSearches, enabled: open });
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [base, setBase] = useState<SearchRequestInput | null>(null);
  useEffect(() => {
    if (!open) return;
    setBase(initial ?? null);
    setQuery(initial?.query ?? "");
    setMode(initial?.mode ?? "hybrid");
    setName(initial?.query ? initial.query.slice(0, 60) : "");
  }, [open, initial]);
  const create = useMutation({
    mutationFn: () =>
      api.createCollection({
        name: name.trim(),
        kind: "smart",
        smart_query: { ...(base ?? {}), query: query.trim(), mode, offset: 0, limit: 200, record: false },
      }),
    onSuccess: (c) => {
      invalidateRepoData(qc);
      toast.success(`Created smart collection “${c.name}”.`);
      onClose();
      navigate(`/collections/${c.id}`);
    },
  });
  const filterCount = base?.filters ? countActiveFilters({ ...EMPTY_FILTERS, ...base.filters }) : 0;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New smart collection"
      description="A smart collection is a saved search. Its members are re-evaluated whenever you open it, so newly starred repositories join automatically."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim() || (!query.trim() && filterCount === 0)} loading={create.isPending} onClick={() => create.mutate()}>
            Create smart collection
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3.5">
        <Field label="Name" htmlFor="smart-name" error={create.isError ? errorMessage(create.error) : undefined}>
          <Input id="smart-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. MCP servers in TypeScript" />
        </Field>
        {saved.data && saved.data.length > 0 && (
          <Field label="Start from a saved search" htmlFor="smart-saved">
            <Select
              id="smart-saved"
              defaultValue=""
              onChange={(e) => {
                const s = saved.data.find((x) => String(x.id) === e.target.value);
                if (!s) return;
                setBase(s.request);
                setQuery(s.request.query);
                setMode(s.request.mode);
                if (!name) setName(s.name);
              }}
            >
              <option value="">Choose…</option>
              {saved.data.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Query" htmlFor="smart-query" hint='Operators work here: lang:typescript topic:mcp stars:>500 "exact phrase"'>
          <Input id="smart-query" value={query} onChange={(e) => setQuery(e.target.value)} className="mono" placeholder="topic:mcp lang:typescript" />
        </Field>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium text-fg">Mode</span>
          <Segmented
            size="sm"
            label="Search mode"
            value={mode}
            onChange={setMode}
            options={[
              { value: "keyword", label: "Keyword" },
              { value: "semantic", label: "Semantic" },
              { value: "hybrid", label: "Hybrid" },
            ]}
          />
        </div>
        {filterCount > 0 && <p className="text-xs text-fg-3">Includes {filterCount} filter{filterCount === 1 ? "" : "s"} from the search you came from.</p>}
      </div>
    </Dialog>
  );
}

function CollectionsTab({ onNew, onNewSmart }: { onNew: () => void; onNewSmart: () => void }) {
  const collections = useCollections();
  if (collections.isLoading) return <Skeleton className="mt-6 h-40 w-full" />;
  if (collections.isError) return <ErrorState className="mt-6" message={errorMessage(collections.error)} onRetry={() => collections.refetch()} />;
  if (!collections.data?.length) {
    return (
      <EmptyState
        icon={<FolderKanban />}
        title="No collections yet"
        action={
          <>
            <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={onNew}>
              New collection
            </Button>
            <Button icon={<Sparkles className="size-3.5" />} onClick={onNewSmart}>
              New smart collection
            </Button>
          </>
        }
      >
        Group repositories around something you are building or evaluating. A repository can live in several collections, and saved research can be filed alongside it.
      </EmptyState>
    );
  }
  return (
    <ul className="mt-2">
      {collections.data.map((c) => (
        <li key={c.id}>
          <Link to={`/collections/${c.id}`} className="group grid grid-cols-[1fr_auto] gap-x-6 border-b border-line py-3.5 transition-colors hover:bg-surface sm:px-2">
            <div className="min-w-0">
              <p className="flex items-center gap-2">
                <span className="truncate text-lg font-semibold text-fg group-hover:underline">{c.name}</span>
                {c.kind === "smart" && (
                  <span className="label inline-flex items-center gap-1">
                    <Sparkles className="size-3" /> Smart
                  </span>
                )}
              </p>
              {c.description && <p className="mt-0.5 line-clamp-1 text-sm text-fg-3">{c.description}</p>}
              {c.kind === "smart" && c.smart_query && <p className="mono mt-0.5 truncate text-xs text-fg-3">{c.smart_query.query || "(filters only)"} · {c.smart_query.mode}</p>}
              {c.preview.length > 0 && (
                <p className="mt-1.5 truncate text-xs text-fg-4">
                  {c.preview
                    .slice(0, 5)
                    .map((r) => r.full_name)
                    .join("  ·  ")}
                  {c.repo_count > 5 ? `  ·  +${c.repo_count - 5}` : ""}
                </p>
              )}
            </div>
            <div className="flex flex-col items-end justify-center text-right">
              <span className="text-lg font-semibold text-fg tnum">{formatNumber(c.repo_count)}</span>
              <span className="text-2xs text-fg-4">
                repos{c.report_count ? ` · ${c.report_count} report${c.report_count === 1 ? "" : "s"}` : ""}
              </span>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function ReportsTab() {
  const [kind, setKind] = useState<"all" | "research" | "ask" | "compare" | "usage_ideas">("all");
  const [savedOnly, setSavedOnly] = useState(false);
  const params = { kind: kind === "all" ? undefined : kind, saved: savedOnly ? true : undefined, limit: 100 };
  const reports = useQuery({ queryKey: ["reports", params], queryFn: () => api.reports(params) });
  const KIND: Record<string, string> = { ask: "Answer", research: "Research", compare: "Comparison", usage_ideas: "Usage ideas" };
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-3 border-b border-line pb-2.5">
        <Segmented
          size="sm"
          label="Report kind"
          value={kind}
          onChange={setKind}
          options={[
            { value: "all", label: "All" },
            { value: "research", label: "Research" },
            { value: "ask", label: "Answers" },
            { value: "compare", label: "Comparisons" },
          ]}
        />
        <Segmented
          size="sm"
          label="Saved filter"
          value={savedOnly ? "saved" : "any"}
          onChange={(v) => setSavedOnly(v === "saved")}
          options={[
            { value: "any", label: "Everything" },
            { value: "saved", label: "Saved only" },
          ]}
        />
      </div>
      {reports.isLoading ? (
        <Skeleton className="mt-4 h-40 w-full" />
      ) : reports.isError ? (
        <ErrorState className="mt-4" message={errorMessage(reports.error)} onRetry={() => reports.refetch()} />
      ) : !reports.data?.length ? (
        <EmptyState icon={<FileText />} title="No reports here yet">
          Answers from Ask AI, research runs and comparisons are kept in this library. Save the ones you want to keep and file them into collections.
        </EmptyState>
      ) : (
        <ul>
          {reports.data.map((r) => (
            <li key={r.id}>
              <Link to={`/reports/${r.id}`} className="group grid grid-cols-[92px_1fr_auto] items-baseline gap-x-4 border-b border-line py-2.5 hover:bg-surface sm:px-2">
                <span className="label">{KIND[r.kind] ?? r.kind}</span>
                <span className="min-w-0">
                  <span className="block truncate text-base font-medium text-fg group-hover:underline">{r.title}</span>
                  <span className="block truncate text-xs text-fg-4">
                    {r.model ? <span className="mono">{r.model}</span> : "no model"}
                    {r.repo_ids.length ? ` · ${plural(r.repo_ids.length, "repository", "repositories")}` : ""}
                    {r.status !== "completed" ? ` · ${r.status}` : ""}
                  </span>
                </span>
                <span className="shrink-0 text-right text-xs text-fg-4">
                  {r.saved && <span className="mr-2 text-fg-2">saved</span>}
                  {timeAgo(r.created_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TagsTab() {
  const qc = useQueryClient();
  const tags = useTags();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState<{ name: string; count: number } | null>(null);
  const done = (data: unknown) => {
    qc.setQueryData(keys.tags, data);
    invalidateRepoData(qc);
  };
  const rename = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) => api.renameTag(from, to),
    onSuccess: (data, vars) => {
      done(data);
      setRenaming(null);
      toast.success(`Renamed “${vars.from}” to “${vars.to}”.`);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const remove = useMutation({
    mutationFn: (name: string) => api.deleteTag(name),
    onSuccess: (data, name) => {
      done(data);
      setDeleting(null);
      toast.success(`Tag “${name}” removed from every repository.`);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  if (tags.isLoading) return <Skeleton className="mt-6 h-24 w-full" />;
  if (tags.isError) return <ErrorState className="mt-6" message={errorMessage(tags.error)} onRetry={() => tags.refetch()} />;
  if (!tags.data?.length) {
    return (
      <EmptyState icon={<TagIcon />} title="No tags yet">
        Tags are your own free-form labels. Add them from a repository page or by selecting several repositories in the Library.
      </EmptyState>
    );
  }
  const commit = (from: string) => {
    const to = draft.trim();
    if (!to || to === from) return setRenaming(null);
    rename.mutate({ from, to });
  };
  const willMerge = renaming !== null && draft.trim() !== renaming && tags.data.some((t) => t.name.toLowerCase() === draft.trim().toLowerCase());
  return (
    <div className="mt-4">
      <p className="border-b border-line pb-2.5 text-sm text-fg-3">Rename a tag to tidy up; renaming onto an existing tag merges the two. Deleting a tag only removes the label.</p>
      <ul className="max-w-[760px]">
        {tags.data.map((t) => (
          <li key={t.name} className="group grid grid-cols-[1fr_auto_auto] items-center gap-x-4 border-b border-line py-2" data-testid={`tag-${t.name}`}>
            {renaming === t.name ? (
              <form
                className="flex min-w-0 items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  commit(t.name);
                }}
              >
                <Input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setRenaming(null)} aria-label={`New name for ${t.name}`} className="mono !h-7 max-w-[260px] text-xs" />
                <Button size="sm" variant="primary" type="submit" loading={rename.isPending}>
                  {willMerge ? "Merge" : "Rename"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                  Cancel
                </Button>
                {willMerge && <span className="truncate text-xs text-fg-3">merges into the existing tag</span>}
              </form>
            ) : (
              <Link to={`/library?tag=${encodeURIComponent(t.name)}`} className="mono min-w-0 truncate text-sm text-fg hover:underline">
                {t.name}
              </Link>
            )}
            <span className="text-xs text-fg-3 tnum">
              {t.count} repo{t.count === 1 ? "" : "s"}
            </span>
            <span className="flex gap-0.5 opacity-60 group-hover:opacity-100 focus-within:opacity-100">
              <IconButton
                label={`Rename ${t.name}`}
                size="sm"
                onClick={() => {
                  setDraft(t.name);
                  setRenaming(t.name);
                }}
              >
                <Pencil className="size-3.5" />
              </IconButton>
              <IconButton label={`Delete ${t.name}`} size="sm" onClick={() => setDeleting(t)}>
                <Trash2 className="size-3.5" />
              </IconButton>
            </span>
          </li>
        ))}
      </ul>
      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        size="sm"
        title={`Delete the tag “${deleting?.name ?? ""}”?`}
        description={`It is removed from ${plural(deleting?.count ?? 0, "repository", "repositories")}. The repositories and their notes are untouched.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting.name)}>
              Delete tag
            </Button>
          </>
        }
      />
    </div>
  );
}

function CategoryDialog({ open, onClose, category }: { open: boolean; onClose: () => void; category: Category | null }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState("");
  useEffect(() => {
    if (!open) return;
    setName(category?.name ?? "");
    setDescription(category?.description ?? "");
    setKeywords((category?.keywords ?? []).join(", "));
  }, [open, category]);
  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), description: description.trim(), keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean) };
      return category ? api.patchCategory(category.id, body) : api.createCategory(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.categories });
      invalidateRepoData(qc);
      toast.success(category ? "Category updated." : "Category created. Repositories are reclassified in the background.");
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={category ? `Edit “${category.name}”` : "New category"}
      description="Keywords drive the automatic classifier; the description is used for similarity matching. Your manual assignments always win."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim()} loading={save.isPending} onClick={() => save.mutate()}>
            {category ? "Save changes" : "Create category"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3.5">
        <Field label="Name" htmlFor="cat-name" error={save.isError ? errorMessage(save.error) : undefined}>
          <Input id="cat-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
        </Field>
        <Field label="Description" htmlFor="cat-desc">
          <Textarea id="cat-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Keywords" htmlFor="cat-kw" hint="Comma separated. Matched against topics, descriptions and READMEs.">
          <Textarea id="cat-kw" rows={3} value={keywords} onChange={(e) => setKeywords(e.target.value)} className="mono text-xs" />
        </Field>
      </div>
    </Dialog>
  );
}

function CategoriesTab() {
  const qc = useQueryClient();
  const categories = useCategories();
  const [editing, setEditing] = useState<Category | null | "new">(null);
  const [deleting, setDeleting] = useState<Category | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteCategory(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.categories });
      invalidateRepoData(qc);
      setDeleting(null);
      toast.success("Category deleted.");
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const reclassify = useMutation({
    mutationFn: () => api.enqueueJob("classify_repos"),
    onSuccess: () => toast.success("Reclassification queued."),
    onError: (err) => toast.error(errorMessage(err)),
  });
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-2.5">
        <p className="max-w-[70ch] text-sm text-fg-3">
          Categories are assigned automatically from metadata and documentation, and optionally by a model. Edit them freely; a repository can belong to several.
        </p>
        <div className="flex gap-2">
          <Button size="sm" loading={reclassify.isPending} onClick={() => reclassify.mutate()}>
            Reclassify library
          </Button>
          <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setEditing("new")}>
            New category
          </Button>
        </div>
      </div>
      {categories.isLoading ? (
        <Skeleton className="mt-4 h-40 w-full" />
      ) : (
        <ul>
          {(categories.data ?? []).map((c) => (
            <li key={c.id} className="group grid grid-cols-[1fr_auto_auto] items-start gap-x-5 border-b border-line py-3 sm:px-2">
              <div className="min-w-0">
                <Link to={`/library?category=${encodeURIComponent(c.slug)}`} className="text-md font-semibold text-fg hover:underline">
                  {c.name}
                </Link>
                {c.description && <p className="mt-0.5 line-clamp-1 text-sm text-fg-3">{c.description}</p>}
                {c.keywords.length > 0 && <p className="mono mt-1 line-clamp-1 text-2xs text-fg-4">{c.keywords.slice(0, 14).join(" · ")}</p>}
              </div>
              <span className="pt-0.5 text-sm text-fg-2 tnum">{formatNumber(c.repo_count)}</span>
              <span className="flex gap-0.5 opacity-60 group-hover:opacity-100">
                <IconButton label={`Edit ${c.name}`} size="sm" onClick={() => setEditing(c)}>
                  <Pencil className="size-3.5" />
                </IconButton>
                <IconButton label={`Delete ${c.name}`} size="sm" onClick={() => setDeleting(c)}>
                  <Trash2 className="size-3.5" />
                </IconButton>
              </span>
            </li>
          ))}
        </ul>
      )}
      <CategoryDialog open={editing !== null} onClose={() => setEditing(null)} category={editing === "new" ? null : editing} />
      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        size="sm"
        title={`Delete “${deleting?.name ?? ""}”?`}
        description={`${plural(deleting?.repo_count ?? 0, "repository", "repositories")} will lose this category. The repositories themselves are untouched.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting.id)}>
              Delete category
            </Button>
          </>
        }
      />
    </div>
  );
}

export default function CollectionsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const tab = (params.get("tab") as TabKey | null) ?? "collections";
  const [creating, setCreating] = useState(false);
  const [smart, setSmart] = useState(false);
  const smartInitial = (location.state as { smartQuery?: SearchRequestInput } | null)?.smartQuery ?? null;
  const collections = useCollections();
  const tags = useTags();
  const categories = useCategories();

  useEffect(() => {
    if (params.get("new") === "smart") {
      setSmart(true);
      const next = new URLSearchParams(params);
      next.delete("new");
      setParams(next, { replace: true, state: location.state });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 pb-20 pt-6 md:px-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Collections</h1>
          <p className="mt-0.5 text-sm text-fg-3">Your groups, tags, categories and saved research. None of this is ever changed by GitHub sync.</p>
        </div>
        {tab === "collections" && (
          <div className="flex gap-2">
            <Button icon={<Sparkles className="size-3.5" />} onClick={() => setSmart(true)}>
              Smart collection
            </Button>
            <Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setCreating(true)}>
              New collection
            </Button>
          </div>
        )}
      </header>
      <Tabs
        label="Collections sections"
        className="mt-4"
        value={tab}
        onChange={(v) => setParams(v === "collections" ? {} : { tab: v }, { replace: true })}
        tabs={[
          { value: "collections", label: "Collections", count: collections.data?.length ?? null },
          { value: "reports", label: "Reports" },
          { value: "tags", label: "Tags", count: tags.data?.length ?? null },
          { value: "categories", label: "Categories", count: categories.data?.length ?? null },
        ]}
      />
      {tab === "collections" && <CollectionsTab onNew={() => setCreating(true)} onNewSmart={() => setSmart(true)} />}
      {tab === "reports" && <ReportsTab />}
      {tab === "tags" && <TagsTab />}
      {tab === "categories" && <CategoriesTab />}
      <NewCollectionDialog open={creating} onClose={() => setCreating(false)} />
      <SmartCollectionDialog open={smart} onClose={() => setSmart(false)} initial={smartInitial} />
    </div>
  );
}
