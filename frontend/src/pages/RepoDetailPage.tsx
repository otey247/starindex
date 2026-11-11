import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowLeftRight,
  Check,
  ExternalLink,
  FileText,
  FlaskConical,
  FolderPlus,
  Globe,
  Heart,
  History,
  ImageOff,
  Images,
  Layers,
  Lightbulb,
  MessagesSquare,
  Network,
  Plus,
  RefreshCw,
  Sparkles,
  Square,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { ApiError, errorMessage } from "@/api/errors";
import { invalidateRepoData, keys, useCategories, useRepo } from "@/api/queries";
import type { Dependency, DocumentInfo, IndexLevel, RepoDetail, RouteOverride } from "@/api/types";
import { ModelPicker, ProviderErrorPanel, useCloudGate } from "@/components/ai/ModelControls";
import { useAiStream } from "@/components/ai/useAiStream";
import { EvidenceList, kindIcon } from "@/components/evidence/EvidenceBits";
import { Markdown } from "@/components/Markdown";
import { CollectionMenu } from "@/components/repo/CollectionMenu";
import { LanguageBar, MaintenanceLabel, RepoFacts, RepoTitle, TopicList } from "@/components/repo/RepoBits";
import { RepoPicker } from "@/components/repo/RepoPicker";
import { Button, Chip, Dialog, EmptyState, ErrorState, IconButton, Menu, MetaRow, ProgressBar, SectionHeader, Select, Skeleton, Tabs, Textarea, Tooltip, type MenuItem } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatBytes, formatCount, formatDate, formatDateTime, formatKb, formatNumber, INDEX_LEVEL_LABEL, shortSha, timeAgo } from "@/lib/format";
import { useDebounced } from "@/lib/useDebounced";
import { toast, useEvidenceViewer } from "@/state/stores";

type TabKey = "overview" | "files" | "dependencies" | "related" | "ideas";

// ------------------------------------------------------------------ AI text (summary / usage ideas)

function AiText({ repo, kind }: { repo: RepoDetail; kind: "summary" | "usage_ideas" }) {
  const qc = useQueryClient();
  const cached = useQuery({ queryKey: keys.repoPart(repo.id, "summary", kind), queryFn: () => api.repoSummary(repo.id, kind) });
  const stream = useAiStream();
  const [route, setRoute] = useState<RouteOverride | null>(null);
  const gate = useCloudGate(route);
  const openEvidence = useEvidenceViewer((s) => s.open);
  const [showPicker, setShowPicker] = useState(false);

  const generate = (override?: RouteOverride | null) => {
    const useRoute = override === undefined ? route : override;
    gate.run((confirmCloud) => {
      stream.start((opts) => api.streamRepoSummary(repo.id, kind, { route: useRoute, confirm_cloud: confirmCloud }, opts)).then(() => {
        qc.invalidateQueries({ queryKey: keys.repoPart(repo.id, "summary", kind) });
      });
    });
  };

  const s = stream.state;
  const running = s.phase === "running";
  const live = s.phase !== "idle";
  const content = live ? s.text : (cached.data?.content ?? "");
  const evidence = live && s.evidence.length ? s.evidence : (cached.data?.evidence ?? []);
  const provider = live ? s.status?.provider_id : cached.data?.provider;
  const model = live ? s.status?.model : cached.data?.model;
  const known = useMemo(() => new Set(evidence.map((e) => e.ref)), [evidence]);
  const notIndexed = repo.index_status !== "indexed" || repo.indexed_level === "metadata";

  const title = kind === "summary" ? "AI summary" : "How could I use this?";
  const emptyCopy =
    kind === "summary"
      ? "A short description of what this repository is and what it offers, grounded in its indexed README and documentation."
      : "Concrete, grounded ideas for using this repository in your own work, each tied to something its documentation actually describes.";

  return (
    <section aria-label={title}>
      <SectionHeader title={title}>
        {cached.data?.stale && !live && (
          <Tooltip content="The repository's indexed content changed after this was generated." side="top">
            <span className="label inline-flex cursor-help items-center gap-1 !text-fg-2">
              <History className="size-3" /> Out of date
            </span>
          </Tooltip>
        )}
        {running ? (
          <Button size="sm" icon={<Square className="size-3" />} onClick={stream.cancel}>
            Stop
          </Button>
        ) : content ? (
          <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={() => generate()}>
            Regenerate
          </Button>
        ) : null}
        <IconButton label="Choose a model for this text" size="sm" active={showPicker} onClick={() => setShowPicker((v) => !v)}>
          <Layers className="size-3.5" />
        </IconButton>
      </SectionHeader>

      {showPicker && <ModelPicker value={route} onChange={setRoute} disabled={running} className="mt-2" />}

      <div className="mt-2">
        {cached.isLoading ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="w-full" />
            <Skeleton className="w-11/12" />
            <Skeleton className="w-3/5" />
          </div>
        ) : content ? (
          <>
            <Markdown compact streaming={running} onCite={(ref) => { const ev = evidence.find((e) => e.ref === ref); if (ev) openEvidence({ type: "evidence", evidence: ev }); }} knownRefs={known}>
              {content}
            </Markdown>
            <p className="mt-2 text-xs text-fg-4">
              {running ? (s.status?.message ?? "Generating…") : "Generated"}
              {provider ? ` with ${provider}` : ""}
              {model ? <span className="mono"> · {model}</span> : null}
              {!live && cached.data?.created_at ? ` · ${timeAgo(cached.data.created_at)}` : ""}. Based on the project's own documentation, not verified behaviour.
            </p>
            {kind === "usage_ideas" && evidence.length > 0 && !running && (
              <details className="mt-3">
                <summary className="label cursor-pointer hover:!text-fg">Evidence · {evidence.length}</summary>
                <div className="mt-2">
                  <EvidenceList evidence={evidence} />
                </div>
              </details>
            )}
          </>
        ) : running ? (
          <div className="flex flex-col gap-2">
            <ProgressBar label="Generating" />
            <p className="text-sm text-fg-3">{s.status?.message ?? "Reading the indexed documentation…"}</p>
          </div>
        ) : s.phase !== "error" ? (
          <div className="flex flex-col items-start gap-2.5">
            <p className="max-w-[70ch] text-sm leading-relaxed text-fg-3">{emptyCopy}</p>
            {notIndexed && (
              <p className="flex items-center gap-1.5 text-xs text-fg-3">
                <TriangleAlert className="size-3 text-warn" aria-hidden />
                Documentation is not indexed yet, so there is little to ground this in.
              </p>
            )}
            <Button variant="primary" size="sm" icon={kind === "summary" ? <Sparkles className="size-3.5" /> : <Lightbulb className="size-3.5" />} onClick={() => generate()}>
              {kind === "summary" ? "Generate summary" : "Generate ideas"}
            </Button>
          </div>
        ) : null}

        {s.phase === "error" && s.error && (
          <ProviderErrorPanel
            className="mt-3"
            error={s.error}
            alternatives={s.alternatives}
            onRetry={() => generate()}
            onSwitch={(opt) => {
              const next = { provider_id: opt.provider_id, model: opt.default_model ?? "" };
              setRoute(next);
              setShowPicker(true);
              generate(next);
            }}
          />
        )}
      </div>
      {gate.dialog}
    </section>
  );
}

// ------------------------------------------------------------------ README

function Readme({ repo }: { repo: RepoDetail }) {
  const [images, setImages] = useState(false);
  const readme = useQuery({
    queryKey: keys.repoPart(repo.id, "readme"),
    queryFn: () => api.repoReadme(repo.id),
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
  });
  const base = repo.default_branch ? `${repo.html_url}/blob/${repo.default_branch}/` : `${repo.html_url}/`;
  return (
    <section aria-label="README">
      <SectionHeader title="README">
        {readme.data && (
          <>
            <span className="mono text-2xs text-fg-4">
              {readme.data.path}
              {readme.data.commit_sha ? ` @${shortSha(readme.data.commit_sha)}` : ""} · indexed {timeAgo(readme.data.indexed_at)}
            </span>
            <IconButton label={images ? "Hide remote images" : "Load remote images (fetches from the internet)"} size="sm" active={images} onClick={() => setImages((v) => !v)}>
              {images ? <Images className="size-3.5" /> : <ImageOff className="size-3.5" />}
            </IconButton>
          </>
        )}
      </SectionHeader>
      <div className="mt-3">
        {readme.isLoading ? (
          <div className="flex flex-col gap-2.5">
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="w-full" />
            <Skeleton className="w-11/12" />
            <Skeleton className="w-4/5" />
          </div>
        ) : readme.isError ? (
          readme.error instanceof ApiError && readme.error.status === 404 ? (
            <p className="text-sm leading-relaxed text-fg-3">
              No README has been indexed for this repository yet. Index it at the documentation level to read it here and make it searchable.
            </p>
          ) : (
            <ErrorState message={errorMessage(readme.error)} onRetry={() => readme.refetch()} />
          )
        ) : readme.data ? (
          <>
            <Markdown baseUrl={base} loadImages={images}>
              {readme.data.content}
            </Markdown>
            {readme.data.truncated && <p className="mt-3 text-xs text-fg-4">This README was truncated at the indexing size limit.</p>}
          </>
        ) : null}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ Files browser

function FilesTab({ repo }: { repo: RepoDetail }) {
  const [params, setParams] = useSearchParams();
  const docs = useQuery({ queryKey: keys.repoPart(repo.id, "documents"), queryFn: () => api.repoDocuments(repo.id) });
  const docId = Number(params.get("doc")) || null;
  const line = Number(params.get("line")) || null;
  const selectedId = docId ?? docs.data?.[0]?.id ?? null;
  const doc = useQuery({
    queryKey: keys.repoPart(repo.id, "document", selectedId),
    queryFn: () => api.repoDocument(repo.id, selectedId as number),
    enabled: selectedId !== null,
  });
  const viewerRef = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!doc.data || !line) return;
    const el = viewerRef.current?.querySelector(`[data-line="${line}"]`);
    el?.scrollIntoView({ block: "center" });
  }, [doc.data, line]);

  const grouped = useMemo(() => {
    const out = new Map<string, DocumentInfo[]>();
    for (const d of docs.data ?? []) {
      if (filter && !d.path.toLowerCase().includes(filter.toLowerCase())) continue;
      const list = out.get(d.kind) ?? [];
      list.push(d);
      out.set(d.kind, list);
    }
    return out;
  }, [docs.data, filter]);

  if (docs.isLoading) return <Skeleton className="mt-4 h-40 w-full" />;
  if (docs.isError) return <ErrorState className="mt-4" message={errorMessage(docs.error)} onRetry={() => docs.refetch()} />;
  if (!docs.data?.length) {
    return (
      <EmptyState icon={<FileText />} title="No files indexed yet">
        Metadata-only repositories have no indexed content. Index at the documentation level for README, docs and manifests, or deep
        for source code.
      </EmptyState>
    );
  }

  const lines = doc.data?.content.split("\n") ?? [];
  const chunkStarts = new Map((doc.data?.chunks ?? []).map((c) => [c.start_line, c]));
  const KIND_LABEL: Record<string, string> = { readme: "README", doc: "Documentation", manifest: "Manifests", config: "Configuration", api: "API definitions", example: "Examples", code: "Source code" };

  return (
    <div className="mt-4 grid gap-5 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)]">
      <nav aria-label="Indexed files" className="min-w-0 lg:max-h-[72vh] lg:overflow-y-auto lg:pr-1">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={`Filter ${docs.data.length} files`}
          aria-label="Filter files"
          className="mb-2 h-7 w-full rounded-sm border border-line-strong bg-bg px-2 text-xs text-fg placeholder:text-fg-4 focus:border-accent focus:outline-none"
        />
        {[...grouped.entries()].map(([kind, list]) => (
          <div key={kind} className="mb-3">
            <p className="label mb-1">
              {KIND_LABEL[kind] ?? kind} <span className="text-fg-4">{list.length}</span>
            </p>
            <ul>
              {list.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    onClick={() => {
                      const next = new URLSearchParams(params);
                      next.set("tab", "files");
                      next.set("doc", String(d.id));
                      next.delete("line");
                      setParams(next, { replace: true });
                    }}
                    title={d.path}
                    className={cn(
                      "mono flex w-full cursor-pointer items-center gap-1.5 rounded-[4px] px-1.5 py-1 text-left text-xs",
                      d.id === selectedId ? "bg-overlay text-fg" : "text-fg-3 hover:bg-hover hover:text-fg",
                    )}
                  >
                    <span className="shrink-0 text-fg-4">{kindIcon(d.kind)}</span>
                    <span className="min-w-0 truncate [direction:rtl] text-left">{d.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="min-w-0">
        {doc.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : doc.isError ? (
          <ErrorState message={errorMessage(doc.error)} onRetry={() => doc.refetch()} />
        ) : doc.data ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2">
              <span className="mono min-w-0 break-all text-sm text-fg">{doc.data.path}</span>
              <span className="flex items-center gap-3 text-xs text-fg-3">
                <span>{formatBytes(doc.data.size_bytes)}</span>
                <span>{doc.data.chunk_count} passages</span>
                {doc.data.commit_sha && <span className="mono">@{shortSha(doc.data.commit_sha)}</span>}
                <span>indexed {timeAgo(doc.data.indexed_at)}</span>
                {doc.data.html_url && (
                  <a href={doc.data.html_url} target="_blank" rel="noopener noreferrer" className="link inline-flex items-center gap-1">
                    GitHub <ExternalLink className="size-3" />
                  </a>
                )}
              </span>
            </div>
            <div ref={viewerRef} className="mt-2 max-h-[70vh] overflow-auto rounded-md border border-line bg-[#0a0b0c] py-2">
              <table className="mono w-full border-collapse text-xs leading-[1.6]">
                <tbody>
                  {lines.map((text, i) => {
                    const n = i + 1;
                    const chunk = chunkStarts.get(n);
                    const target = line === n;
                    return (
                      <tr key={n} data-line={n} className={cn(target && "bg-white/[0.06]")}>
                        <td className={cn("w-px select-none whitespace-nowrap border-r border-line px-2.5 text-right align-top tnum", target ? "text-accent" : "text-fg-4")}>{n}</td>
                        <td className={cn("whitespace-pre-wrap break-words px-3 text-fg-2", chunk && n > 1 && "border-t border-dashed border-line")}>{text || " "}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-fg-4">
              Dashed rules mark passage boundaries used for search and citations.
              {doc.data.truncated ? " This file was truncated at the indexing size limit." : ""}
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Dependencies

function DependenciesTab({ repo }: { repo: RepoDetail }) {
  const navigate = useNavigate();
  const deps = useQuery({ queryKey: keys.repoPart(repo.id, "dependencies"), queryFn: () => api.repoDependencies(repo.id) });
  if (deps.isLoading) return <Skeleton className="mt-4 h-40 w-full" />;
  if (deps.isError) return <ErrorState className="mt-4" message={errorMessage(deps.error)} onRetry={() => deps.refetch()} />;
  if (!deps.data?.length) {
    return (
      <EmptyState icon={<Network />} title="No dependency manifests indexed">
        Dependencies are read from manifests such as package.json, pyproject.toml, Cargo.toml and go.mod once the repository is indexed at
        the documentation level or deeper. Nothing is ever installed or executed.
      </EmptyState>
    );
  }
  const byEco = new Map<string, Dependency[]>();
  for (const d of deps.data) byEco.set(d.ecosystem, [...(byEco.get(d.ecosystem) ?? []), d]);
  return (
    <div className="mt-4 flex flex-col gap-6">
      {[...byEco.entries()].map(([eco, list]) => (
        <section key={eco}>
          <SectionHeader title={eco} count={list.length} />
          <div className="mt-1 overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  <th className="label py-1.5 pr-4 font-medium">Package</th>
                  <th className="label py-1.5 pr-4 font-medium">Version</th>
                  <th className="label py-1.5 pr-4 font-medium">Scope</th>
                  <th className="label py-1.5 pr-4 font-medium">Declared in</th>
                  <th className="label py-1.5 text-right font-medium">Also used by</th>
                </tr>
              </thead>
              <tbody>
                {list
                  .slice()
                  .sort((a, b) => b.shared_with - a.shared_with || a.name.localeCompare(b.name))
                  .map((d) => (
                    <tr key={`${d.name}-${d.manifest_path}-${d.scope}`} className="border-b border-line">
                      <td className="mono py-1.5 pr-4 text-xs text-fg">{d.name}</td>
                      <td className="mono py-1.5 pr-4 text-xs text-fg-3">{d.version_spec || "—"}</td>
                      <td className="py-1.5 pr-4 text-xs text-fg-3">{d.scope}</td>
                      <td className="mono py-1.5 pr-4 text-xs text-fg-3">{d.manifest_path}</td>
                      <td className="py-1.5 text-right text-xs">
                        {d.shared_with > 0 ? (
                          <button
                            type="button"
                            className="link cursor-pointer"
                            onClick={() => navigate(`/search?q=${encodeURIComponent(`"${d.name}"`)}&mode=keyword&scope=docs`)}
                            title={`Find other starred repositories that mention ${d.name}`}
                          >
                            {d.shared_with} starred repo{d.shared_with === 1 ? "" : "s"}
                          </button>
                        ) : (
                          <span className="text-fg-4">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <p className="text-xs text-fg-4">Observed directly in indexed manifest files. Transitive dependencies are not resolved.</p>
    </div>
  );
}

// ------------------------------------------------------------------ Related

function RelatedTab({ repo }: { repo: RepoDetail }) {
  const related = useQuery({ queryKey: keys.repoPart(repo.id, "related"), queryFn: () => api.repoRelated(repo.id, 14) });
  const navigate = useNavigate();
  if (related.isLoading) return <Skeleton className="mt-4 h-40 w-full" />;
  if (related.isError) return <ErrorState className="mt-4" message={errorMessage(related.error)} onRetry={() => related.refetch()} />;
  if (!related.data?.length) {
    return (
      <EmptyState icon={<Network />} title="No related projects found yet">
        Related projects come from embedding similarity plus shared topics, dependencies, categories and fork relationships. They appear once
        this repository and its neighbours are indexed.
      </EmptyState>
    );
  }
  const REASON: Record<string, string> = { semantic: "Similar content", topics: "Shared topics", dependencies: "Shared dependencies", category: "Same category", owner: "Same owner", fork: "Fork relationship", language: "Same language" };
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between gap-3 border-b border-line py-2">
        <p className="text-sm text-fg-3">Starred repositories related by content and by concrete relationship evidence.</p>
        <Button
          size="sm"
          icon={<FlaskConical className="size-3.5" />}
          onClick={() => navigate(`/research?capability=relationships&repo=${repo.id}&q=${encodeURIComponent(`How does ${repo.full_name} relate to the other repositories I have starred? Which overlap, which complement it?`)}`)}
        >
          Research relationships
        </Button>
      </div>
      <ul>
        {related.data.map((r) => (
          <li key={r.repo.id} className="grid grid-cols-[1fr_auto] gap-x-4 border-b border-line py-3">
            <div className="min-w-0">
              <RepoTitle repo={r.repo} />
              {r.repo.description && <p className="mt-0.5 line-clamp-1 text-sm text-fg-2">{r.repo.description}</p>}
              <ul className="mt-1.5 flex flex-col gap-0.5">
                {r.reasons.map((reason, i) => (
                  <li key={i} className="flex items-baseline gap-2 text-xs">
                    <span className="label w-[136px] shrink-0">{REASON[reason.kind] ?? reason.kind}</span>
                    <span className="min-w-0 text-fg-3">{reason.detail}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-col items-end gap-1.5">
              <span className="mono text-xs text-fg-3" title="Combined relatedness score">
                {r.score.toFixed(2)}
              </span>
              <Button size="sm" variant="ghost" onClick={() => navigate(`/compare?ids=${repo.id},${r.repo.id}`)}>
                Compare
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ Aside panels

function Notes({ repo }: { repo: RepoDetail }) {
  const qc = useQueryClient();
  const [value, setValue] = useState(repo.note);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const debounced = useDebounced(value, 700);
  const lastSaved = useRef(repo.note);
  useEffect(() => {
    setValue(repo.note);
    lastSaved.current = repo.note;
  }, [repo.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (debounced === lastSaved.current) return;
    setState("saving");
    api
      .patchAnnotation(repo.id, { note: debounced })
      .then(() => {
        lastSaved.current = debounced;
        setState("saved");
        invalidateRepoData(qc);
      })
      .catch(() => setState("error"));
  }, [debounced, repo.id, qc]);
  return (
    <section>
      <SectionHeader title="Notes">
        <span className="text-2xs text-fg-4" aria-live="polite">
          {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : state === "error" ? "Not saved — retrying on next edit" : value !== lastSaved.current ? "Editing…" : ""}
        </span>
      </SectionHeader>
      <Textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={5}
        placeholder="Why did you star this? What would you use it for?"
        aria-label="Personal notes"
        className="mt-1.5 text-sm"
      />
      <p className="mt-1 text-2xs text-fg-4">Notes are yours. GitHub sync never changes them.</p>
    </section>
  );
}

function TagEditor({ repo }: { repo: RepoDetail }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const save = useMutation({
    mutationFn: (tags: string[]) => api.setRepoTags(repo.id, tags),
    onSuccess: (data) => {
      qc.setQueryData(keys.repo(repo.id), data);
      invalidateRepoData(qc);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const add = () => {
    const tags = draft.split(",").map((t) => t.trim()).filter(Boolean);
    if (!tags.length) return;
    save.mutate([...new Set([...repo.tags, ...tags])]);
    setDraft("");
  };
  return (
    <section>
      <SectionHeader title="Tags" />
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {repo.tags.map((t) => (
          <Chip key={t} onRemove={() => save.mutate(repo.tags.filter((x) => x !== t))}>
            {t}
          </Chip>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder={repo.tags.length ? "Add tag" : "Add a tag…"}
          aria-label="Add tag"
          className="h-[22px] w-24 min-w-0 flex-1 rounded-[4px] border border-transparent bg-transparent px-1 text-xs text-fg placeholder:text-fg-4 hover:border-line focus:border-accent focus:outline-none"
        />
      </div>
    </section>
  );
}

function CategoryEditor({ repo }: { repo: RepoDetail }) {
  const qc = useQueryClient();
  const all = useCategories();
  const save = useMutation({
    mutationFn: (ids: number[]) => api.setRepoCategories(repo.id, ids),
    onSuccess: (data) => {
      qc.setQueryData(keys.repo(repo.id), data);
      invalidateRepoData(qc);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const current = repo.categories.map((c) => c.id);
  const items: MenuItem[] = (all.data ?? []).map((c) => ({
    key: String(c.id),
    label: c.name,
    checked: current.includes(c.id),
    onSelect: () => save.mutate(current.includes(c.id) ? current.filter((x) => x !== c.id) : [...current, c.id]),
  }));
  const SOURCE: Record<string, string> = { rule: "matched by keywords", embedding: "matched by similarity", llm: "assigned by a model", user: "set by you" };
  return (
    <section>
      <SectionHeader title="Categories">
        <Menu
          align="end"
          width={270}
          items={items}
          header={<span className="label">Assign categories</span>}
          trigger={(p) => (
            <IconButton label="Edit categories" size="sm" noTooltip {...p}>
              <Plus className="size-3.5" />
            </IconButton>
          )}
        />
      </SectionHeader>
      {repo.categories.length === 0 ? (
        <p className="mt-1 text-sm text-fg-4">Not categorised yet.</p>
      ) : (
        <ul className="mt-1 flex flex-col">
          {repo.categories.map((c) => (
            <li key={c.id} className="group flex items-baseline justify-between gap-2 py-0.5 text-sm">
              <Link to={`/library?category=${encodeURIComponent(c.slug)}`} className="min-w-0 truncate text-fg-2 hover:text-fg hover:underline">
                {c.name}
              </Link>
              <span className="shrink-0 text-2xs text-fg-4" title={c.source !== "user" ? `confidence ${(c.confidence * 100).toFixed(0)}%` : undefined}>
                {SOURCE[c.source] ?? c.source}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function IndexPanel({ repo }: { repo: RepoDetail }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [level, setLevel] = useState<IndexLevel>(repo.index_level as IndexLevel);
  useEffect(() => setLevel(repo.index_level as IndexLevel), [repo.id, repo.index_level]);
  const index = useMutation({
    mutationFn: (force: boolean) => api.indexRepo(repo.id, level, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.repo(repo.id) });
      qc.invalidateQueries({ queryKey: keys.sync });
      toast.success(`Queued ${INDEX_LEVEL_LABEL[level]?.toLowerCase()} indexing for ${repo.name}.`, { label: "View queue", onClick: () => navigate("/sync") });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
  const busy = repo.index_status === "indexing" || repo.index_status === "queued";
  const status =
    repo.index_status === "indexed"
      ? `Indexed · ${INDEX_LEVEL_LABEL[repo.indexed_level ?? "metadata"]}`
      : repo.index_status === "indexing"
        ? "Indexing now"
        : repo.index_status === "queued"
          ? "Queued"
          : repo.index_status === "failed"
            ? "Indexing failed"
            : repo.index_status === "skipped"
              ? "Skipped by limits"
              : "Not indexed yet";
  return (
    <section>
      <SectionHeader title="Index" />
      <p className="mt-1 flex items-center gap-1.5 text-sm font-medium text-fg">
        {repo.index_status === "failed" ? <TriangleAlert className="size-3.5 text-danger" aria-hidden /> : repo.index_status === "indexed" ? <Check className="size-3.5 text-fg-2" aria-hidden /> : <Layers className="size-3.5 text-fg-3" aria-hidden />}
        {status}
      </p>
      {busy && <ProgressBar className="mt-2" label="Indexing" />}
      {repo.index_error && <p className="mt-1 text-xs leading-relaxed text-fg-3 break-words">{repo.index_error}</p>}
      <dl className="mt-1.5">
        <MetaRow label="Last indexed">{repo.indexed_at ? formatDateTime(repo.indexed_at) : "—"}</MetaRow>
        <MetaRow label="Commit" mono>
          {repo.indexed_commit_sha ? shortSha(repo.indexed_commit_sha) : "—"}
        </MetaRow>
        <MetaRow label="Files · passages">
          {formatNumber(repo.doc_count)} · {formatNumber(repo.chunk_count)}
        </MetaRow>
        <MetaRow label="Embedded">
          {formatNumber(repo.embedded_chunk_count)} / {formatNumber(repo.chunk_count)}
        </MetaRow>
      </dl>
      {repo.tree_truncated && <p className="mt-1 text-xs text-fg-4">GitHub truncated this repository's file tree; very large repositories are only partly listed.</p>}
      <div className="mt-2.5 flex items-center gap-2">
        <Select value={level} onChange={(e) => setLevel(e.target.value as IndexLevel)} aria-label="Index level" className="flex-1">
          <option value="metadata">Metadata only</option>
          <option value="docs">Documentation</option>
          <option value="deep">Deep (source code)</option>
        </Select>
        <Button
          size="md"
          loading={index.isPending}
          disabled={busy}
          onClick={() => index.mutate(level === repo.indexed_level)}
          icon={<RefreshCw className="size-3.5" />}
        >
          {level === repo.indexed_level ? "Refresh" : "Index"}
        </Button>
      </div>
      <p className="mt-1.5 text-2xs leading-relaxed text-fg-4">
        {level === "deep"
          ? "Deep indexing reads source files from a downloaded archive. Repository code is never executed."
          : level === "docs"
            ? "README, Markdown docs, examples, manifests and selected configuration."
            : "Description, topics and activity only."}
      </p>
    </section>
  );
}

function CompareWithDialog({ repo, open, onClose }: { repo: RepoDetail; open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  return (
    <Dialog open={open} onClose={onClose} title={`Compare ${repo.name} with…`} description="Pick another starred repository. You can add more on the comparison page." size="sm">
      <div className="min-h-[230px]">
        <RepoPicker autoFocus excludeIds={[repo.id]} placeholder="Search your library" onPick={(other) => navigate(`/compare?ids=${repo.id},${other.id}`)} />
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ Page

export default function RepoDetailPage() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const repoQuery = useRepo(id);
  const repo = repoQuery.data;
  const tab = (params.get("tab") as TabKey | null) ?? "overview";
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    if (Number.isFinite(id)) api.repoOpened(id).catch(() => undefined);
  }, [id]);

  // Poll while indexing so the page settles on its own.
  const busy = repo?.index_status === "indexing" || repo?.index_status === "queued";
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => qc.invalidateQueries({ queryKey: keys.repo(id) }), 2500);
    return () => clearInterval(t);
  }, [busy, id, qc]);

  const annotate = useMutation({
    mutationFn: (body: { favorite?: boolean; priority?: number }) => api.patchAnnotation(id, body),
    onSuccess: (data) => {
      qc.setQueryData(keys.repo(id), data);
      invalidateRepoData(qc);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const setTab = (next: TabKey) => {
    const sp = new URLSearchParams(params);
    if (next === "overview") sp.delete("tab");
    else sp.set("tab", next);
    if (next !== "files") {
      sp.delete("doc");
      sp.delete("line");
    }
    setParams(sp, { replace: true });
  };

  if (repoQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[1400px] px-4 pt-8 md:px-8">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="mt-3 h-4 w-[55%]" />
        <Skeleton className="mt-8 h-64 w-full" />
      </div>
    );
  }
  if (repoQuery.isError || !repo) {
    const notFound = repoQuery.error instanceof ApiError && repoQuery.error.status === 404;
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 pt-10 md:px-8">
        {notFound ? (
          <EmptyState title="Repository not found" action={<Button onClick={() => navigate("/library")}>Back to Library</Button>}>
            This repository is not in your library. It may have been removed by the unstarred-retention setting.
          </EmptyState>
        ) : (
          <ErrorState message={errorMessage(repoQuery.error)} onRetry={() => repoQuery.refetch()} />
        )}
      </div>
    );
  }

  const priorityItems: MenuItem[] = [0, 1, 2, 3].map((p) => ({
    key: String(p),
    label: p === 0 ? "No priority" : `Priority ${p}`,
    checked: repo.priority === p,
    onSelect: () => annotate.mutate({ priority: p }),
  }));

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 pb-20 pt-5 md:px-8">
      <button type="button" onClick={() => navigate(-1)} className="mb-3 inline-flex cursor-pointer items-center gap-1 text-xs text-fg-3 hover:text-fg">
        <ArrowLeft className="size-3" /> Back
      </button>

      <header className="border-b border-line pb-5 animate-rise">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h1 className="flex min-w-0 flex-wrap items-baseline text-3xl font-semibold tracking-[-0.02em]">
              <span className="font-normal text-fg-3">{repo.owner}</span>
              <span className="px-1.5 font-light text-fg-4">/</span>
              <span className="break-all text-fg">{repo.name}</span>
            </h1>
            {repo.description && <p className="mt-2 max-w-[86ch] text-lg leading-relaxed text-fg-2">{repo.description}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <IconButton
              label={repo.favorite ? "Remove from favorites" : "Mark as favorite"}
              active={repo.favorite}
              onClick={() => annotate.mutate({ favorite: !repo.favorite })}
            >
              <Heart className={cn("size-4", repo.favorite && "fill-fg text-fg")} />
            </IconButton>
            <Menu
              align="end"
              width={170}
              items={priorityItems}
              trigger={(p) => (
                <Button size="md" {...p}>
                  {repo.priority ? `Priority ${repo.priority}` : "Priority"}
                </Button>
              )}
            />
            <a
              href={repo.html_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay"
            >
              <ExternalLink className="size-3.5" aria-hidden /> GitHub
            </a>
          </div>
        </div>

        <RepoFacts repo={repo} className="mt-3 !text-sm" showStarred />

        {(repo.star_state === "unstarred" || repo.access_state !== "ok") && (
          <p className="mt-3 flex items-start gap-2 text-sm text-fg-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
            {repo.star_state === "unstarred"
              ? `You unstarred this repository${repo.unstarred_at ? ` on ${formatDate(repo.unstarred_at)}` : ""}. Your notes, collections and research are retained.`
              : "GitHub no longer serves this repository (deleted, made private or blocked). The indexed copy and your notes are still here."}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button variant="primary" icon={<MessagesSquare className="size-3.5" />} onClick={() => navigate(`/ask?repo=${repo.id}`)}>
            Ask AI about this repository
          </Button>
          <Button icon={<Lightbulb className="size-3.5" />} onClick={() => setTab("ideas")}>
            How could I use this?
          </Button>
          <Button icon={<Network className="size-3.5" />} onClick={() => setTab("related")}>
            Explore related projects
          </Button>
          <Button icon={<ArrowLeftRight className="size-3.5" />} onClick={() => setComparing(true)}>
            Compare with…
          </Button>
          <CollectionMenu
            repoIds={[repo.id]}
            memberOf={repo.collections.map((c) => c.id)}
            trigger={(p) => (
              <Button icon={<FolderPlus className="size-3.5" />} {...p}>
                Add to collection
              </Button>
            )}
          />
          {repo.homepage && (
            <a href={repo.homepage} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 min-w-0 items-center gap-1.5 px-2 text-sm text-fg-3 hover:text-fg">
              <Globe className="size-3.5 shrink-0" aria-hidden />
              <span className="max-w-[240px] truncate">{repo.homepage.replace(/^https?:\/\//, "")}</span>
            </a>
          )}
        </div>
      </header>

      <div className="mt-1 grid gap-x-10 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          <Tabs
            label="Repository sections"
            value={tab}
            onChange={setTab}
            tabs={[
              { value: "overview", label: "Overview" },
              { value: "files", label: "Indexed files", count: repo.doc_count },
              { value: "dependencies", label: "Dependencies" },
              { value: "related", label: "Related" },
              { value: "ideas", label: "Usage ideas" },
            ]}
          />
          {tab === "overview" && (
            <div className="mt-5 flex flex-col gap-8">
              <AiText repo={repo} kind="summary" />
              <Readme repo={repo} />
            </div>
          )}
          {tab === "files" && <FilesTab repo={repo} />}
          {tab === "dependencies" && <DependenciesTab repo={repo} />}
          {tab === "related" && <RelatedTab repo={repo} />}
          {tab === "ideas" && (
            <div className="mt-5 max-w-[860px]">
              <AiText repo={repo} kind="usage_ideas" />
            </div>
          )}
        </div>

        <aside className="mt-6 flex min-w-0 flex-col gap-6 lg:mt-5" aria-label="Repository details">
          <IndexPanel repo={repo} />
          <Notes repo={repo} />
          <TagEditor repo={repo} />
          <section>
            <SectionHeader title="Collections">
              <CollectionMenu
                repoIds={[repo.id]}
                memberOf={repo.collections.map((c) => c.id)}
                trigger={(p) => (
                  <IconButton label="Add to a collection" size="sm" noTooltip {...p}>
                    <Plus className="size-3.5" />
                  </IconButton>
                )}
              />
            </SectionHeader>
            {repo.collections.length === 0 ? (
              <p className="mt-1 text-sm text-fg-4">Not in any collection.</p>
            ) : (
              <ul className="mt-1 flex flex-col">
                {repo.collections.map((c) => (
                  <li key={c.id}>
                    <Link to={`/collections/${c.id}`} className="block truncate py-0.5 text-sm text-fg-2 hover:text-fg hover:underline">
                      {c.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <CategoryEditor repo={repo} />
          <section>
            <SectionHeader title="Topics" />
            {repo.topics.length ? (
              <TopicList topics={repo.topics} max={30} className="mt-1.5" onPick={(t) => navigate(`/library?topic=${encodeURIComponent(t)}`)} />
            ) : (
              <p className="mt-1 text-sm text-fg-4">No topics.</p>
            )}
          </section>
          <section>
            <SectionHeader title="Languages" />
            <LanguageBar languages={repo.languages} className="mt-2" />
          </section>
          <section>
            <SectionHeader title="Activity" />
            <dl className="mt-1">
              <MetaRow label="Maintenance">
                <MaintenanceLabel value={repo.maintenance} />
              </MetaRow>
              <MetaRow label="Last push">{repo.pushed_at ? `${formatDate(repo.pushed_at)} · ${timeAgo(repo.pushed_at)}` : "—"}</MetaRow>
              <MetaRow label="Created">{formatDate(repo.created_at)}</MetaRow>
              <MetaRow label="Stars">{formatNumber(repo.stars)}</MetaRow>
              <MetaRow label="Forks">{formatCount(repo.forks)}</MetaRow>
              <MetaRow label="Watchers">{formatCount(repo.watchers)}</MetaRow>
              <MetaRow label="Open issues">{formatNumber(repo.open_issues)}</MetaRow>
            </dl>
          </section>
          <section>
            <SectionHeader title="Details" />
            <dl className="mt-1">
              <MetaRow label="License">{repo.license_name ?? repo.license_spdx ?? "None declared"}</MetaRow>
              <MetaRow label="Default branch" mono>
                {repo.default_branch ?? "—"}
              </MetaRow>
              <MetaRow label="Size">{formatKb(repo.size_kb)}</MetaRow>
              <MetaRow label="Starred">{formatDate(repo.starred_at)}</MetaRow>
              <MetaRow label="Opened here">{repo.open_count ? `${repo.open_count}× · ${timeAgo(repo.last_opened_at)}` : "First visit"}</MetaRow>
              {repo.parent_full_name && <MetaRow label="Fork of">{repo.parent_full_name}</MetaRow>}
              <MetaRow label="Repository id" mono>
                {repo.id}
              </MetaRow>
            </dl>
          </section>
          {repo.name_history.length > 0 && (
            <section>
              <SectionHeader title="Rename history" />
              <ul className="mt-1 flex flex-col gap-1.5">
                {repo.name_history.map((h, i) => (
                  <li key={i} className="text-xs">
                    <span className="mono block break-all text-fg-3">
                      {h.old_full_name} → <span className="text-fg-2">{h.new_full_name}</span>
                    </span>
                    <span className="text-fg-4">{formatDate(h.changed_at)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>
      <CompareWithDialog repo={repo} open={comparing} onClose={() => setComparing(false)} />
    </div>
  );
}
