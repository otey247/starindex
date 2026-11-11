import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Cpu, Download, Info, RefreshCw, TriangleAlert, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { keys, usePatchSettings, useProviders, useSettings } from "@/api/queries";
import type { AppSettings, BackupInfo } from "@/api/types";
import { GitHubConnect } from "@/components/GitHubConnect";
import { Button, Dialog, ErrorState, Input, Segmented, Select, SettingRow, Skeleton, Switch, Textarea } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatBytes, formatDateTime, formatNumber, timeAgo } from "@/lib/format";
import { ScheduleEditor } from "@/components/sync/SyncBits";
import { toast } from "@/state/stores";

export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-2 border-b border-line pb-4">
      <h2 className="text-2xl font-semibold text-fg">{title}</h2>
      {children && <p className="mt-1 max-w-[72ch] text-sm leading-relaxed text-fg-3">{children}</p>}
    </header>
  );
}

export function Group({ title, children, description }: { title: string; children: ReactNode; description?: ReactNode }) {
  return (
    <section className="mt-8 first:mt-4">
      <h3 className="label">{title}</h3>
      {description && <p className="mt-1 max-w-[72ch] text-sm text-fg-3">{description}</p>}
      <div className="mt-1">{children}</div>
    </section>
  );
}

/** Settings are saved as each control changes; failures are reported and the UI reverts. */
export function useSettingsPatch() {
  const patch = usePatchSettings();
  return (body: Record<string, unknown>, successMessage?: string) =>
    patch.mutate(body, {
      onSuccess: () => successMessage && toast.success(successMessage),
      onError: (err) => toast.error(`Not saved: ${errorMessage(err)}`),
    });
}

/** Number field that commits on blur/Enter, with optional unit conversion. */
export function NumberSetting({
  value,
  onCommit,
  min,
  max,
  step,
  unit,
  label,
  width = 96,
}: {
  value: number;
  onCommit: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  label: string;
  width?: number;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    let n = Number(draft);
    if (!Number.isFinite(n)) return setDraft(String(value));
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    setDraft(String(n));
    if (n !== value) onCommit(n);
  };
  return (
    <span className="inline-flex items-center gap-2">
      <Input
        type="number"
        value={draft}
        min={min}
        max={max}
        step={step}
        aria-label={label}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        style={{ width }}
        className="text-right tnum"
      />
      {unit && <span className="text-sm text-fg-3">{unit}</span>}
    </span>
  );
}

function ListSetting({ value, onCommit, label, rows = 4 }: { value: string[]; onCommit: (list: string[]) => void; label: string; rows?: number }) {
  const [draft, setDraft] = useState(value.join("\n"));
  useEffect(() => setDraft(value.join("\n")), [value]);
  const commit = () => {
    const list = [...new Set(draft.split(/[\n,]/).map((s) => s.trim()).filter(Boolean))];
    if (list.join("\n") !== value.join("\n")) onCommit(list);
  };
  return <Textarea aria-label={label} rows={rows} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} className="mono text-xs" spellCheck={false} />;
}

function useLoaded(): { settings: AppSettings | undefined; loading: ReactNode | null } {
  const q = useSettings();
  if (q.isLoading) return { settings: undefined, loading: <Skeleton className="mt-6 h-64 w-full" /> };
  if (q.isError || !q.data) return { settings: undefined, loading: <ErrorState className="mt-6" message={errorMessage(q.error)} onRetry={() => q.refetch()} /> };
  return { settings: q.data, loading: null };
}

// ------------------------------------------------------------------ GitHub

export function GitHubSettings() {
  const { settings, loading } = useLoaded();
  const save = useSettingsPatch();
  return (
    <div>
      <PageTitle title="GitHub access">Read-only access to your starred repositories. Credentials are kept in the operating system keyring, never in the browser or in settings files.</PageTitle>
      <Group title="Connection">
        <div className="mt-3 max-w-[760px]">
          <GitHubConnect />
        </div>
      </Group>
      {loading ??
        (settings && (
          <Group title="When a repository is unstarred">
            <SettingRow
              stacked
              title="Retention"
              description="Unstarring on GitHub never silently deletes your notes, collections or research. Choose what happens to the repository's indexed data."
            >
              <Segmented
                label="Unstarred retention"
                value={settings.sync.unstarred_retention}
                onChange={(v) => save({ sync: { unstarred_retention: v } })}
                options={[
                  { value: "keep", label: "Keep everything", title: "Mark as unstarred and hide from the default view" },
                  { value: "keep_annotated", label: "Keep if annotated", title: "Delete only repositories without notes, tags, collections or reports" },
                  { value: "delete", label: "Delete index data", title: "Remove the repository and its indexed content" },
                ]}
              />
            </SettingRow>
          </Group>
        ))}
    </div>
  );
}

// ------------------------------------------------------------------ Indexing

export function IndexingSettings() {
  const { settings, loading } = useLoaded();
  const save = useSettingsPatch();
  if (loading || !settings) return <div><PageTitle title="Indexing" />{loading}</div>;
  const ix = settings.indexing;
  const set = (patch: Record<string, unknown>) => save({ indexing: patch });
  return (
    <div>
      <PageTitle title="Indexing">What StarIndex reads from each repository. Content is fetched as text and never executed; archives are read in memory with size and path checks.</PageTitle>
      <Group title="Levels">
        <SettingRow
          stacked
          title="Default level for the whole library"
          description="Metadata: description, topics, activity. Documentation: README, Markdown docs, examples, manifests, selected configuration. Deep: source code, API definitions and architecture documents, from a downloaded archive."
        >
          <Segmented
            label="Default index level"
            value={ix.default_level}
            onChange={(v) => set({ default_level: v })}
            options={[
              { value: "metadata", label: "Metadata" },
              { value: "docs", label: "Documentation" },
              { value: "deep", label: "Deep" },
            ]}
          />
        </SettingRow>
        <SettingRow title="Index newly starred repositories automatically" description="New stars are queued at the default level right after a sync.">
          <Switch label="Auto-index new repositories" checked={ix.auto_index_new} onChange={(v) => set({ auto_index_new: v })} />
        </SettingRow>
      </Group>
      <Group title="Limits" description="Guards against huge repositories, binaries and generated assets.">
        <SettingRow title="Largest file to index" description="Files above this size are skipped.">
          <NumberSetting label="Max file size in KB" value={Math.round(ix.max_file_bytes / 1000)} min={1} max={20000} unit="KB" onCommit={(n) => set({ max_file_bytes: n * 1000 })} />
        </SettingRow>
        <SettingRow title="Characters kept per document" description="Longer documents are truncated and marked as such.">
          <NumberSetting label="Max document characters" value={ix.max_doc_chars} min={5000} max={5_000_000} step={5000} width={120} onCommit={(n) => set({ max_doc_chars: n })} />
        </SettingRow>
        <SettingRow title="Files per repository, documentation level">
          <NumberSetting label="Max documentation files" value={ix.max_docs_files} min={1} max={500} onCommit={(n) => set({ max_docs_files: n })} />
        </SettingRow>
        <SettingRow title="Files per repository, deep level">
          <NumberSetting label="Max deep files" value={ix.max_deep_files} min={10} max={50000} step={50} onCommit={(n) => set({ max_deep_files: n })} />
        </SettingRow>
        <SettingRow title="Largest archive to download" description="Deep indexing stops reading an archive once it exceeds this size.">
          <NumberSetting label="Max archive size in MB" value={ix.max_tarball_mb} min={1} max={5000} unit="MB" onCommit={(n) => set({ max_tarball_mb: n })} />
        </SettingRow>
        <SettingRow title="Skip deep indexing above repository size" description="Uses the size GitHub reports for the repository.">
          <NumberSetting label="Max repository size for deep indexing in MB" value={ix.max_repo_size_mb_for_deep} min={1} max={100000} step={50} unit="MB" onCommit={(n) => set({ max_repo_size_mb_for_deep: n })} />
        </SettingRow>
      </Group>
      <Group title="Exclusions" description="Vendored dependencies, build output and generated files are skipped. Git LFS pointers and submodules are recorded but not followed.">
        <SettingRow stacked title="Excluded directories" description="One per line. Matched against any path segment.">
          <ListSetting label="Excluded directories" value={ix.exclude_dirs} onCommit={(list) => set({ exclude_dirs: list })} rows={5} />
        </SettingRow>
        <SettingRow stacked title="Excluded file patterns" description="Glob patterns, one per line.">
          <ListSetting label="Excluded file patterns" value={ix.exclude_globs} onCommit={(list) => set({ exclude_globs: list })} />
        </SettingRow>
        <SettingRow stacked title="Source file extensions for deep indexing">
          <ListSetting label="Code extensions" value={ix.code_extensions} onCommit={(list) => set({ code_extensions: list })} />
        </SettingRow>
      </Group>
      <Group title="Resource use" description="Bounded concurrency keeps indexing from exhausting the machine.">
        <SettingRow title="Parallel downloads from GitHub">
          <NumberSetting label="Fetch concurrency" value={ix.fetch_concurrency} min={1} max={16} onCommit={(n) => set({ fetch_concurrency: n })} />
        </SettingRow>
        <SettingRow title="Worker threads" description="Applies after the next restart.">
          <NumberSetting label="Worker concurrency" value={ix.worker_concurrency} min={1} max={16} onCommit={(n) => set({ worker_concurrency: n })} />
        </SettingRow>
        <SettingRow title="Embedding batch size" description="Lower it if embedding uses too much memory.">
          <NumberSetting label="Embedding batch size" value={ix.embed_batch_size} min={1} max={512} onCommit={(n) => set({ embed_batch_size: n })} />
        </SettingRow>
      </Group>
    </div>
  );
}

// ------------------------------------------------------------------ Search & embeddings

function HardwarePanel() {
  const hw = useQuery({ queryKey: ["hardware"], queryFn: api.hardware, staleTime: 60_000 });
  if (hw.isLoading) return <Skeleton className="mt-3 h-24 w-full" />;
  if (hw.isError || !hw.data) return <ErrorState className="mt-3" message={errorMessage(hw.error)} onRetry={() => hw.refetch()} />;
  const h = hw.data;
  return (
    <div className="mt-3 rounded-md border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <Cpu className="mt-0.5 size-4 shrink-0 text-fg-3" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fg">
            {h.gpus.length ? h.gpus.map((g) => g.name).join(", ") : "No NVIDIA GPU detected"}
            <span className="ml-2 font-normal text-fg-3">{h.cuda_available ? "CUDA available" : "CPU inference"}</span>
          </p>
          <p className="mono mt-0.5 text-xs text-fg-3">
            {h.os} · {h.cpu_count} CPU threads{h.memory_total_mb ? ` · ${formatBytes(h.memory_total_mb * 1024 * 1024)} RAM` : ""}
            {h.gpus[0]?.memory_total_mb ? ` · ${formatBytes(h.gpus[0].memory_total_mb * 1024 * 1024)} VRAM` : ""}
            {h.onnx_providers.length ? ` · ONNX: ${h.onnx_providers.map((p) => p.replace("ExecutionProvider", "")).join(", ")}` : ""}
          </p>
          {h.recommendations.length > 0 && (
            <ul className="mt-2.5 flex flex-col gap-1">
              {h.recommendations.map((r, i) => (
                <li key={i} className="flex items-baseline gap-2 text-sm leading-relaxed text-fg-2">
                  <span className="size-1 shrink-0 translate-y-[-2px] rounded-full bg-fg-4" aria-hidden />
                  {r}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export function SearchSettings() {
  const { settings, loading } = useLoaded();
  const save = useSettingsPatch();
  const providers = useProviders();
  const qc = useQueryClient();
  const rebuild = useMutation({
    mutationFn: () => api.enqueueJob("embed_pending"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.sync });
      toast.success("Embedding job queued.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const [model, setModel] = useState("");
  const [rerankModel, setRerankModel] = useState("");
  useEffect(() => {
    if (settings) {
      setModel(settings.embedding.model);
      setRerankModel(settings.rerank.model);
    }
  }, [settings?.embedding.model, settings?.rerank.model]); // eslint-disable-line react-hooks/exhaustive-deps
  if (loading || !settings) return <div><PageTitle title="Search" />{loading}</div>;
  const se = settings.search;
  const em = settings.embedding;
  const rr = settings.rerank;
  const needsProvider = em.backend === "ollama" || em.backend === "openai_compat";
  const embedProviders = (providers.data ?? []).filter((p) => p.capabilities.embeddings && p.capabilities.is_local);
  return (
    <div>
      <PageTitle title="Search">Retrieval runs entirely on local indexes. Relevance is the primary signal; popularity and freshness are small, bounded nudges.</PageTitle>
      <Group title="Behaviour">
        <SettingRow title="Default mode" description="Hybrid fuses lexical and vector rankings with reciprocal rank fusion.">
          <Segmented
            label="Default search mode"
            value={se.default_mode}
            onChange={(v) => save({ search: { default_mode: v } })}
            options={[
              { value: "keyword", label: "Keyword" },
              { value: "semantic", label: "Semantic" },
              { value: "hybrid", label: "Hybrid" },
            ]}
          />
        </SettingRow>
        <SettingRow title="Typo tolerance" description="Corrects misspelled terms against the library's own vocabulary.">
          <Switch label="Typo tolerance" checked={se.typo_tolerance} onChange={(v) => save({ search: { typo_tolerance: v } })} />
        </SettingRow>
        <SettingRow title="Query expansion" description="Adds related terms only when a query would otherwise return little. Off by default; it is not always an improvement.">
          <Segmented
            label="Query expansion"
            value={se.query_expansion}
            onChange={(v) => save({ search: { query_expansion: v } })}
            options={[
              { value: "off", label: "Off" },
              { value: "auto", label: "When needed" },
            ]}
          />
        </SettingRow>
        <SettingRow title="Keep search history" description="Stored locally. Clear it from the Search page at any time.">
          <Switch label="Save search history" checked={se.save_history} onChange={(v) => save({ search: { save_history: v } })} />
        </SettingRow>
      </Group>

      <Group title="Ranking">
        <SettingRow title="Lexical weight" description="Weight of the BM25 ranking in fusion.">
          <NumberSetting label="Lexical weight" value={se.lexical_weight} min={0} max={5} step={0.1} onCommit={(n) => save({ search: { lexical_weight: n } })} />
        </SettingRow>
        <SettingRow title="Semantic weight" description="Weight of the vector ranking in fusion.">
          <NumberSetting label="Semantic weight" value={se.semantic_weight} min={0} max={5} step={0.1} onCommit={(n) => save({ search: { semantic_weight: n } })} />
        </SettingRow>
        <SettingRow title="Popularity boost" description="Maximum relative boost from star count. Kept small so a strong match always wins.">
          <NumberSetting label="Popularity boost percent" value={Math.round(se.popularity_boost * 1000) / 10} min={0} max={25} step={0.5} unit="%" onCommit={(n) => save({ search: { popularity_boost: n / 100 } })} />
        </SettingRow>
        <SettingRow title="Freshness boost" description="Maximum relative boost from recent activity.">
          <NumberSetting label="Freshness boost percent" value={Math.round(se.freshness_boost * 1000) / 10} min={0} max={25} step={0.5} unit="%" onCommit={(n) => save({ search: { freshness_boost: n / 100 } })} />
        </SettingRow>
        <SettingRow title="Results per owner" description="Limit how many repositories from one owner appear in a result page. 0 means unlimited.">
          <NumberSetting label="Max results per owner" value={se.max_results_per_owner} min={0} max={50} onCommit={(n) => save({ search: { max_results_per_owner: n } })} />
        </SettingRow>
      </Group>

      <Group title="Reranking" description="A local cross-encoder re-scores the top results of hybrid search.">
        <SettingRow title="Rerank results">
          <Switch label="Reranking" checked={rr.enabled} onChange={(v) => save({ rerank: { enabled: v } })} />
        </SettingRow>
        <SettingRow title="Reranker model" description="Downloaded once, then used offline.">
          <Input value={rerankModel} onChange={(e) => setRerankModel(e.target.value)} onBlur={() => rerankModel.trim() && rerankModel !== rr.model && save({ rerank: { model: rerankModel.trim() } })} aria-label="Reranker model" className="mono w-[320px] max-w-full text-xs" spellCheck={false} />
        </SettingRow>
        <SettingRow title="Candidates to rerank">
          <NumberSetting label="Rerank candidates" value={rr.top_n} min={5} max={200} onCommit={(n) => save({ rerank: { top_n: n } })} />
        </SettingRow>
      </Group>

      <Group title="Embeddings" description="Used for semantic and hybrid search. Changing the model builds a new vector index in the background while the old one keeps serving.">
        <SettingRow title="Backend" description="fastembed runs ONNX models in-process; Ollama and OpenAI-compatible servers use one of your local providers.">
          <Select value={em.backend} onChange={(e) => save({ embedding: { backend: e.target.value } }, "Embedding backend changed. Re-embedding is queued.")} aria-label="Embedding backend">
            <option value="fastembed">fastembed (built in, ONNX)</option>
            <option value="ollama">Ollama</option>
            <option value="openai_compat">OpenAI-compatible server</option>
            <option value="sentence_transformers">Sentence Transformers</option>
            <option value="none">None (keyword search only)</option>
          </Select>
        </SettingRow>
        {needsProvider && (
          <SettingRow title="Provider" description="Which configured local provider serves the embedding model.">
            <Select value={em.provider_id ?? ""} onChange={(e) => save({ embedding: { provider_id: e.target.value || null } })} aria-label="Embedding provider">
              <option value="">Choose a provider…</option>
              {embedProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </SettingRow>
        )}
        {em.backend !== "none" && (
          <>
            <SettingRow title="Model">
              <Input value={model} onChange={(e) => setModel(e.target.value)} onBlur={() => model.trim() && model !== em.model && save({ embedding: { model: model.trim() } }, "Embedding model changed. A new index is being built.")} aria-label="Embedding model" className="mono w-[320px] max-w-full text-xs" spellCheck={false} />
            </SettingRow>
            <SettingRow title="Device" description="Auto uses CUDA when it is available and falls back to CPU.">
              <Segmented
                label="Embedding device"
                value={em.device}
                onChange={(v) => save({ embedding: { device: v } })}
                options={[
                  { value: "auto", label: "Auto" },
                  { value: "cpu", label: "CPU" },
                  { value: "cuda", label: "CUDA" },
                ]}
              />
            </SettingRow>
            <SettingRow title="Embed anything still pending" description="Runs the embedding job now instead of waiting for the next indexing pass.">
              <Button icon={<RefreshCw className="size-3.5" />} loading={rebuild.isPending} onClick={() => rebuild.mutate()}>
                Embed pending passages
              </Button>
            </SettingRow>
          </>
        )}
      </Group>

      <Group title="This machine">
        <HardwarePanel />
      </Group>
    </div>
  );
}

// ------------------------------------------------------------------ Privacy

export function PrivacySettings() {
  const { settings, loading } = useLoaded();
  const save = useSettingsPatch();
  if (loading || !settings) return <div><PageTitle title="Privacy" />{loading}</div>;
  const p = settings.privacy;
  return (
    <div>
      <PageTitle title="Privacy">Repository text and embeddings stay on this machine. These switches control the only two kinds of outbound traffic StarIndex can make.</PageTitle>
      <Group title="Model traffic">
        <SettingRow
          title="Allow cloud models"
          description="Master switch. While off, no repository content can be sent to OpenAI, Anthropic, Google, Azure, a ChatGPT plan or any remote endpoint, and those providers cannot be selected."
        >
          <Switch label="Allow cloud models" checked={p.allow_cloud_models} onChange={(v) => save({ privacy: { allow_cloud_models: v } }, v ? "Cloud models are now allowed." : "Cloud models are blocked.")} />
        </SettingRow>
        <SettingRow
          title="Ask before each cloud request"
          description="Shows which provider and model will receive the retrieved context and waits for your confirmation. Background tasks never use a cloud model while this is on."
        >
          <Switch label="Confirm before cloud requests" checked={p.confirm_before_cloud} disabled={!p.allow_cloud_models} onChange={(v) => save({ privacy: { confirm_before_cloud: v } })} />
        </SettingRow>
      </Group>
      <Group title="Network">
        <SettingRow title="Offline mode" description="Also stops GitHub synchronisation and content downloads. Search, local models and everything already indexed keep working.">
          <Switch label="Offline mode" checked={p.offline_mode} onChange={(v) => save({ privacy: { offline_mode: v } }, v ? "Offline mode is on." : "Offline mode is off.")} />
        </SettingRow>
      </Group>
      <Group title="What is stored where">
        <dl className="mt-2 grid max-w-[760px] grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="text-fg-3">Library, notes, reports</dt>
          <dd className="text-fg-2">Local SQLite database in your user data folder.</dd>
          <dt className="text-fg-3">Embeddings</dt>
          <dd className="text-fg-2">Local database and local vector index.</dd>
          <dt className="text-fg-3">Tokens and API keys</dt>
          <dd className="text-fg-2">Operating system keyring. Never sent to the browser, logged or written into reports.</dd>
          <dt className="text-fg-3">Network binding</dt>
          <dd className="text-fg-2">Loopback only. Access from another device requires explicit configuration and an access token.</dd>
        </dl>
      </Group>
    </div>
  );
}

// ------------------------------------------------------------------ Storage

export function StorageSettings() {
  const qc = useQueryClient();
  const { settings } = useLoaded();
  const save = useSettingsPatch();
  const storage = useQuery({ queryKey: ["storage"], queryFn: api.storage });
  const backups = useQuery({ queryKey: ["backups"], queryFn: api.backups, refetchInterval: 20_000 });
  const [restoring, setRestoring] = useState<BackupInfo | null>(null);
  const backup = useMutation({
    mutationFn: api.createBackup,
    onSuccess: () => {
      toast.success("Backup started.");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["backups"] }), 1500);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const restore = useMutation({
    mutationFn: (name: string) => api.restoreBackup(name),
    onSuccess: (res) => {
      setRestoring(null);
      toast.success(res.message || "Backup restored. Restart StarIndex to finish.");
      qc.invalidateQueries();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const st = storage.data;
  const rows: [string, number | undefined][] = st
    ? [
        ["Database (library, text, embeddings)", st.database_bytes],
        ["Vector index", st.vector_bytes],
        ["Downloaded models", st.models_bytes],
        ["Backups", st.backups_bytes],
      ]
    : [];
  const total = rows.reduce((sum, [, v]) => sum + (v ?? 0), 0);
  return (
    <div>
      <PageTitle title="Storage">Application data lives outside the program folder, so updates never touch it.</PageTitle>
      <Group title="Disk use">
        {storage.isLoading ? (
          <Skeleton className="mt-3 h-28 w-full" />
        ) : storage.isError || !st ? (
          <ErrorState className="mt-3" message={errorMessage(storage.error)} onRetry={() => storage.refetch()} />
        ) : (
          <div className="mt-2 max-w-[760px]">
            <p className="mono break-all text-xs text-fg-3">{st.data_dir}</p>
            <table className="mt-3 w-full border-collapse text-sm">
              <tbody>
                {rows.map(([label, bytes]) => (
                  <tr key={label} className="border-b border-line">
                    <td className="py-2 text-fg-2">{label}</td>
                    <td className="py-2 text-right text-fg tnum">{formatBytes(bytes)}</td>
                  </tr>
                ))}
                <tr>
                  <td className="py-2 font-medium text-fg">Total</td>
                  <td className="py-2 text-right font-semibold text-fg tnum">{formatBytes(total)}</td>
                </tr>
              </tbody>
            </table>
            <p className="mt-1 text-xs text-fg-3">
              {formatNumber(st.embeddings_rows)} stored embeddings
              {st.free_bytes !== null && st.free_bytes !== undefined ? ` · ${formatBytes(st.free_bytes)} free on this drive` : ""}
            </p>
          </div>
        )}
      </Group>

      <Group title="Backups" description="A backup is a consistent copy of the database. The vector index is rebuilt from it, so backups stay small.">
        {settings && (
          <>
            <SettingRow title="Scheduled backups">
              <Switch label="Scheduled backups" checked={settings.storage.backup_enabled} onChange={(v) => save({ storage: { backup_enabled: v } })} />
            </SettingRow>
            <SettingRow title="Backups to keep" description="Older ones are removed automatically.">
              <NumberSetting label="Backups to keep" value={settings.storage.backup_keep} min={1} max={100} onCommit={(n) => save({ storage: { backup_keep: n } })} />
            </SettingRow>
          </>
        )}
        <div className="mt-4 flex items-center gap-2">
          <Button variant="primary" loading={backup.isPending} onClick={() => backup.mutate()}>
            Back up now
          </Button>
        </div>
        {backups.data && backups.data.length > 0 ? (
          <ul className="mt-3 max-w-[760px]">
            {backups.data.map((b) => (
              <li key={b.name} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-4 border-b border-line py-2">
                <div className="min-w-0">
                  <p className="mono truncate text-xs text-fg">{b.name}</p>
                  <p className="text-xs text-fg-3">
                    {formatDateTime(b.created_at)} · {timeAgo(b.created_at)}
                  </p>
                </div>
                <span className="text-sm text-fg-2 tnum">{formatBytes(b.size_bytes)}</span>
                <Button size="sm" onClick={() => setRestoring(b)}>
                  Restore…
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-fg-4">No backups yet.</p>
        )}
      </Group>

      <Group title="Export" description="Your library with notes, tags, categories and collection membership, in an open format.">
        <div className="mt-3 flex flex-wrap gap-2">
          {(["json", "csv"] as const).map((format) => (
            <a
              key={format}
              href={api.exportUrl(format)}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay"
            >
              <Download className="size-3.5" aria-hidden /> Export {format.toUpperCase()}
            </a>
          ))}
        </div>
      </Group>

      <Dialog
        open={restoring !== null}
        onClose={() => setRestoring(null)}
        title="Restore this backup?"
        size="sm"
        description={
          <>
            The current database is replaced by <span className="mono text-xs text-fg-2">{restoring?.name}</span> from {formatDateTime(restoring?.created_at)}. Anything added since then — stars, notes, reports — is lost. A safety copy of the current database is written first.
          </>
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setRestoring(null)}>
              Cancel
            </Button>
            <Button variant="danger" loading={restore.isPending} onClick={() => restoring && restore.mutate(restoring.name)}>
              Replace current data
            </Button>
          </>
        }
      />
    </div>
  );
}

// ------------------------------------------------------------------ Schedules

export function ScheduleSettings() {
  const { settings, loading } = useLoaded();
  const save = useSettingsPatch();
  return (
    <div>
      <PageTitle title="Schedules">Recurring background work. Runs inside StarIndex on this computer; no cloud scheduler or GitHub Action is involved.</PageTitle>
      {loading ??
        (settings && (
          <Group title="Synchronisation">
            <SettingRow title="Scheduled sync" description="Pause all automatic syncs without changing the intervals below.">
              <Switch label="Scheduled sync" checked={settings.sync.scheduled_sync_enabled} onChange={(v) => save({ sync: { scheduled_sync_enabled: v } })} />
            </SettingRow>
          </Group>
        ))}
      <Group title="Jobs">
        <div className="max-w-[760px]">
          <ScheduleEditor />
        </div>
        <p className="mt-3 max-w-[72ch] text-xs leading-relaxed text-fg-4">
          Schedules fire while StarIndex is running and catch up on the next start if a run was missed. To have StarIndex start at sign-in on Windows, run <span className="mono">scripts\install-startup-task.ps1</span>.
        </p>
      </Group>
    </div>
  );
}

// ------------------------------------------------------------------ Diagnostics

export function DiagnosticsSettings() {
  const diag = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics });
  const ICON = {
    ok: <Check className="size-4 text-fg-2" aria-label="OK" />,
    info: <Info className="size-4 text-fg-3" aria-label="Info" />,
    warn: <CircleAlert className="size-4 text-warn" aria-label="Warning" />,
    fail: <X className="size-4 text-danger" aria-label="Failed" />,
  } as const;
  const problems = diag.data?.checks.filter((c) => c.status === "fail" || c.status === "warn").length ?? 0;
  return (
    <div>
      <PageTitle title="Diagnostics">Health of every dependency StarIndex relies on, with what to do when one is not right.</PageTitle>
      <div className="mt-4 flex items-center justify-between gap-3">
        <p className="text-sm text-fg-3">
          {diag.data ? (
            <>
              {problems === 0 ? "Everything checks out." : `${problems} item${problems === 1 ? " needs" : "s need"} attention.`} Checked {timeAgo(diag.data.generated_at)}.
            </>
          ) : (
            "Running checks…"
          )}
        </p>
        <Button size="sm" icon={<RefreshCw className={cn("size-3.5", diag.isFetching && "animate-spin")} />} onClick={() => diag.refetch()}>
          Run again
        </Button>
      </div>
      {diag.isLoading ? (
        <Skeleton className="mt-4 h-64 w-full" />
      ) : diag.isError || !diag.data ? (
        <ErrorState className="mt-4" message={errorMessage(diag.error)} onRetry={() => diag.refetch()} />
      ) : (
        <ul className="mt-3">
          {diag.data.checks.map((c) => (
            <li key={c.name} className="grid grid-cols-[auto_1fr] gap-x-3 border-b border-line py-3">
              <span className="mt-0.5">{ICON[c.status]}</span>
              <div className="min-w-0">
                <p className="text-base font-medium text-fg">{c.name}</p>
                <p className="mt-0.5 break-words text-sm text-fg-3">{c.detail}</p>
                {c.hint && (c.status === "warn" || c.status === "fail") && (
                  <p className="mt-1 flex items-start gap-1.5 text-sm text-fg-2">
                    <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
                    {c.hint}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
