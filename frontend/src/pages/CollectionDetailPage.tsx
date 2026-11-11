import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FlaskConical, GitCompareArrows, Layers, Pencil, Search as SearchIcon, Sparkles, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { ApiError, errorMessage } from "@/api/errors";
import { invalidateRepoData, keys } from "@/api/queries";
import type { IndexLevel, RepoFilters } from "@/api/types";
import { RepoPicker } from "@/components/repo/RepoPicker";
import { RepoRow } from "@/components/repo/RepoRow";
import { Button, Dialog, EmptyState, ErrorState, Field, IconButton, Input, Menu, SectionHeader, Skeleton, Textarea } from "@/components/ui";
import { INDEX_LEVEL_LABEL, plural, timeAgo } from "@/lib/format";
import { searchStateToParams, DEFAULT_SEARCH_STATE } from "@/lib/urlState";
import { MAX_COMPARE, toast } from "@/state/stores";
import { EMPTY_FILTERS } from "@/api/types";

export default function CollectionDetailPage() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: keys.collection(id), queryFn: () => api.collection(id), enabled: Number.isFinite(id) });
  const c = query.data;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.collection(id) });
    invalidateRepoData(qc);
  };
  const add = useMutation({ mutationFn: (repoId: number) => api.addToCollection(id, [repoId]), onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)) });
  const remove = useMutation({ mutationFn: (repoId: number) => api.removeFromCollection(id, [repoId]), onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)) });
  const patch = useMutation({
    mutationFn: () => api.patchCollection(id, { name: name.trim(), description: description.trim() }),
    onSuccess: () => {
      refresh();
      setEditing(false);
      toast.success("Collection updated.");
    },
  });
  const destroy = useMutation({
    mutationFn: () => api.deleteCollection(id),
    onSuccess: () => {
      invalidateRepoData(qc);
      toast.success("Collection deleted. Its repositories are untouched.");
      navigate("/collections");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const index = useMutation({
    mutationFn: (level: IndexLevel) => api.indexBatch({ level, collection_id: id }),
    onSuccess: (res, level) => {
      refresh();
      toast.success(`Queued ${res.queued} for ${INDEX_LEVEL_LABEL[level]?.toLowerCase()} indexing${res.skipped ? `, ${res.skipped} skipped` : ""}.`, { label: "View progress", onClick: () => navigate("/sync") });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (query.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[1180px] px-4 pt-8 md:px-8">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="mt-6 h-64 w-full" />
      </div>
    );
  }
  if (query.isError || !c) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 pt-10 md:px-8">
        {notFound ? (
          <EmptyState title="Collection not found" action={<Button onClick={() => navigate("/collections")}>All collections</Button>} />
        ) : (
          <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} />
        )}
      </div>
    );
  }

  const smart = c.kind === "smart";
  const searchUrl = smart && c.smart_query
    ? `/search?${searchStateToParams({ ...DEFAULT_SEARCH_STATE, q: c.smart_query.query, mode: c.smart_query.mode, scope: c.smart_query.scope, sort: c.smart_query.sort, filters: { ...EMPTY_FILTERS, ...(c.smart_query.filters as RepoFilters) } }).toString()}`
    : null;

  return (
    <div className="mx-auto w-full max-w-[1180px] px-4 pb-20 pt-5 md:px-8">
      <Link to="/collections" className="mb-3 inline-flex items-center gap-1 text-xs text-fg-3 hover:text-fg">
        <ArrowLeft className="size-3" /> Collections
      </Link>
      <header className="border-b border-line pb-4">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            {smart && (
              <p className="label mb-1 inline-flex items-center gap-1">
                <Sparkles className="size-3" /> Smart collection
              </p>
            )}
            <h1 className="text-3xl font-semibold tracking-[-0.02em] text-fg">{c.name}</h1>
            {c.description && <p className="mt-1.5 max-w-[80ch] text-base leading-relaxed text-fg-3">{c.description}</p>}
            {smart && c.smart_query && (
              <p className="mono mt-2 text-xs text-fg-3">
                {c.smart_query.query || "(filters only)"} · {c.smart_query.mode}
              </p>
            )}
            <p className="mt-2 text-xs text-fg-4">
              {plural(c.repo_count, "repository", "repositories")} · updated {timeAgo(c.updated_at)}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <IconButton
              label="Rename or describe"
              onClick={() => {
                setName(c.name);
                setDescription(c.description);
                setEditing(true);
              }}
            >
              <Pencil className="size-4" />
            </IconButton>
            <IconButton label="Delete collection" onClick={() => setDeleting(true)}>
              <Trash2 className="size-4" />
            </IconButton>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            icon={<FlaskConical className="size-3.5" />}
            onClick={() => navigate(`/research?collection=${c.id}`)}
            disabled={c.repo_count === 0}
          >
            Research this collection
          </Button>
          <Button
            icon={<GitCompareArrows className="size-3.5" />}
            disabled={c.repos.length < 2}
            onClick={() => navigate(`/compare?ids=${c.repos.slice(0, MAX_COMPARE).map((r) => r.id).join(",")}`)}
            title={c.repos.length > MAX_COMPARE ? `Compares the first ${MAX_COMPARE}` : undefined}
          >
            Compare
          </Button>
          <Menu
            width={240}
            header={<span className="label">Index every repository here</span>}
            items={(["docs", "deep"] as IndexLevel[]).map((level) => ({
              key: level,
              label: `${INDEX_LEVEL_LABEL[level]} indexing`,
              hint: level === "deep" ? "source code" : "README + docs",
              onSelect: () => index.mutate(level),
            }))}
            trigger={(p) => (
              <Button icon={<Layers className="size-3.5" />} loading={index.isPending} disabled={c.repo_count === 0} {...p}>
                Index level
              </Button>
            )}
          />
          {searchUrl && (
            <Button variant="ghost" icon={<SearchIcon className="size-3.5" />} onClick={() => navigate(searchUrl)}>
              Open as search
            </Button>
          )}
        </div>
      </header>

      <div className="mt-5 grid gap-x-10 gap-y-8 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className="min-w-0" aria-label="Repositories in this collection">
          {!smart && (
            <RepoPicker className="mb-3 max-w-[420px]" excludeIds={c.repos.map((r) => r.id)} placeholder="Add a repository to this collection…" onPick={(repo) => add.mutate(repo.id)} />
          )}
          {c.repos.length === 0 ? (
            <EmptyState title={smart ? "No repositories match this search right now" : "This collection is empty"}>
              {smart
                ? "Smart collections fill themselves. As you star and index more repositories, matches appear here."
                : "Add repositories with the picker above, from a repository page, or by selecting several in the Library."}
            </EmptyState>
          ) : (
            <div className="border-t border-line">
              {c.repos.map((repo) => (
                <div key={repo.id} className="relative">
                  <RepoRow repo={repo} showIndex />
                  {!smart && (
                    <button
                      type="button"
                      aria-label={`Remove ${repo.full_name} from this collection`}
                      title="Remove from collection"
                      onClick={() => remove.mutate(repo.id)}
                      className="absolute left-2 top-3.5 inline-flex size-6 cursor-pointer items-center justify-center rounded-sm text-fg-4 hover:bg-hover hover:text-fg"
                    >
                      <X className="size-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
        <aside aria-label="Saved research">
          <SectionHeader title="Saved research" count={c.reports.length} />
          {c.reports.length === 0 ? (
            <p className="mt-1.5 text-sm leading-relaxed text-fg-4">Reports and comparisons you file into this collection appear here.</p>
          ) : (
            <ul className="mt-1">
              {c.reports.map((r) => (
                <li key={r.id}>
                  <Link to={`/reports/${r.id}`} className="group block border-b border-line py-2">
                    <span className="line-clamp-2 text-sm font-medium text-fg group-hover:underline">{r.title}</span>
                    <span className="mt-0.5 block text-2xs text-fg-4">
                      {r.kind} · {timeAgo(r.created_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      <Dialog
        open={editing}
        onClose={() => setEditing(false)}
        title="Edit collection"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!name.trim()} loading={patch.isPending} onClick={() => patch.mutate()}>
              Save
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <Field label="Name" htmlFor="edit-col-name" error={patch.isError ? errorMessage(patch.error) : undefined}>
            <Input id="edit-col-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Description" htmlFor="edit-col-desc">
            <Textarea id="edit-col-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </Dialog>
      <Dialog
        open={deleting}
        onClose={() => setDeleting(false)}
        size="sm"
        title={`Delete “${c.name}”?`}
        description="The collection is removed. Repositories, notes and reports stay in your library."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              Cancel
            </Button>
            <Button variant="danger" loading={destroy.isPending} onClick={() => destroy.mutate()}>
              Delete collection
            </Button>
          </>
        }
      />
    </div>
  );
}
