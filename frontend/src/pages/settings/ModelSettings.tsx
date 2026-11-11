import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, Cloud, HardDrive, KeyRound, Plus, ShieldOff, Trash2, TriangleAlert, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { invalidateModels, keys, useProviders, useSettings } from "@/api/queries";
import type { ProviderInfo, ProviderTemplate, ProviderTestResult, Route, TaskType } from "@/api/types";
import { KIND_LABEL } from "@/components/ai/ModelControls";
import { Button, Dialog, ErrorState, Field, Input, Select, SettingRow, Skeleton, Switch } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCost, formatMs, formatNumber, TASK_LABEL } from "@/lib/format";
import { toast } from "@/state/stores";
import { Group, NumberSetting, PageTitle, useSettingsPatch } from "./GeneralSettings";

const TASKS: TaskType[] = ["classify", "summarize", "expand_query", "answer", "reasoning", "compare"];

// ------------------------------------------------------------------ add provider

function AddProviderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const templates = useQuery({ queryKey: ["provider-templates"], queryFn: api.providerTemplates, enabled: open, staleTime: 300_000 });
  const settings = useSettings();
  const [template, setTemplate] = useState<ProviderTemplate | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [options, setOptions] = useState<Record<string, string>>({});
  useEffect(() => {
    if (open) {
      setTemplate(null);
      setApiKey("");
      setOptions({});
    }
  }, [open]);
  const pick = (t: ProviderTemplate) => {
    setTemplate(t);
    setName(t.label);
    setBaseUrl(t.default_base_url ?? "");
    setOptions({});
  };
  const create = useMutation({
    mutationFn: () =>
      api.createProvider({
        kind: template!.kind,
        name: name.trim(),
        base_url: baseUrl.trim() || null,
        api_key: apiKey || null,
        options: Object.fromEntries(Object.entries(options).filter(([, v]) => v.trim() !== "")),
      }),
    onSuccess: (p) => {
      invalidateModels(qc);
      toast.success(`${p.name} added. Test it to discover its models.`);
      onClose();
    },
  });
  const cloudAllowed = settings.data?.privacy.allow_cloud_models ?? false;
  const groups: { key: ProviderTemplate["category"]; label: string; note: string }[] = [
    { key: "local", label: "Local models", note: "Run on this machine. Nothing leaves it." },
    { key: "cloud", label: "Cloud APIs", note: "Paid providers. Requires your own API key and explicit opt-in." },
  ];
  const missingRequired = (template?.option_fields ?? []).some((f) => f.required && !(options[String(f.key)] ?? "").trim());
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={template ? `Add ${template.label}` : "Add a model provider"}
      description={template ? template.description : "Providers are interchangeable. Pick where generation should run; routing decides which tasks use it."}
      footer={
        template ? (
          <>
            <Button variant="ghost" onClick={() => setTemplate(null)}>
              Back
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={!name.trim() || missingRequired || (template.needs_api_key && !template.api_key_optional && !apiKey)}
              onClick={() => create.mutate()}
            >
              Add provider
            </Button>
          </>
        ) : undefined
      }
    >
      {!template ? (
        templates.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          <div className="flex flex-col gap-5">
            {groups.map((g) => {
              const list = (templates.data ?? []).filter((t) => t.category === g.key);
              if (!list.length) return null;
              return (
                <section key={g.key}>
                  <div className="flex items-baseline justify-between gap-3">
                    <h3 className="label">{g.label}</h3>
                    <span className="text-xs text-fg-4">{g.note}</span>
                  </div>
                  <ul className="mt-1.5">
                    {list.map((t) => (
                      <li key={t.kind}>
                        <button type="button" onClick={() => pick(t)} className="group flex w-full cursor-pointer items-center gap-3 border-b border-line px-1 py-2.5 text-left hover:bg-raised">
                          <span className="text-fg-3">{t.category === "local" ? <HardDrive className="size-4" /> : <Cloud className="size-4" />}</span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-base font-medium text-fg">{t.label}</span>
                            <span className="block truncate text-sm text-fg-3">{t.description}</span>
                          </span>
                          <ChevronRight className="size-4 text-fg-4 group-hover:text-fg" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
            <p className="text-sm text-fg-3">
              To use a ChatGPT subscription instead of an API key, set it up under{" "}
              <Link to="/settings/chatgpt" className="link" onClick={onClose}>
                ChatGPT plan
              </Link>
              .
            </p>
          </div>
        )
      ) : (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          {template.category === "cloud" && !cloudAllowed && (
            <p className="flex items-start gap-2 text-sm text-fg-2">
              <ShieldOff className="mt-0.5 size-4 shrink-0 text-fg-3" aria-hidden />
              Cloud models are currently blocked in Privacy settings. You can add this provider now, but it stays unusable until you allow cloud models.
            </p>
          )}
          <Field label="Name" htmlFor="prov-name">
            <Input id="prov-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          </Field>
          {(template.base_url_editable || !template.default_base_url) && (
            <Field label="Base URL" htmlFor="prov-url" hint={template.category === "local" ? "Where the server listens on this machine." : undefined}>
              <Input id="prov-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={template.default_base_url ?? "https://…"} className="mono text-xs" spellCheck={false} />
            </Field>
          )}
          {template.option_fields.map((f) => {
            const key = String(f.key);
            return (
              <Field key={key} label={`${String(f.label ?? key)}${f.required ? "" : " (optional)"}`} htmlFor={`prov-opt-${key}`}>
                <Input id={`prov-opt-${key}`} value={options[key] ?? ""} onChange={(e) => setOptions((o) => ({ ...o, [key]: e.target.value }))} placeholder={String(f.placeholder ?? "")} className="mono text-xs" spellCheck={false} />
              </Field>
            );
          })}
          {(template.needs_api_key || template.api_key_optional) && (
            <Field
              label={`API key${template.api_key_optional || !template.needs_api_key ? " (optional)" : ""}`}
              htmlFor="prov-key"
              hint="Stored in your operating system's keyring. It is never shown again, sent to the browser or written to logs."
            >
              <Input id="prov-key" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mono text-xs" spellCheck={false} />
            </Field>
          )}
          {create.isError && (
            <p className="flex items-start gap-2 text-sm text-fg-2" role="alert">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" /> {errorMessage(create.error)}
            </p>
          )}
          {template.docs_url && (
            <a href={template.docs_url} target="_blank" rel="noopener noreferrer" className="link text-xs">
              Setup documentation
            </a>
          )}
        </form>
      )}
    </Dialog>
  );
}

// ------------------------------------------------------------------ provider row

function ProviderRow({ provider }: { provider: ProviderInfo }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [testModel, setTestModel] = useState("");
  const isPlan = provider.category === "chatgpt_plan";

  const models = useQuery({ queryKey: ["provider-models", provider.id], queryFn: () => api.providerModels(provider.id), enabled: open && provider.enabled && !provider.blocked_by_privacy, staleTime: 60_000 });
  const runTest = useMutation({
    mutationFn: () => api.testProvider(provider.id, testModel || null),
    onSuccess: (r) => {
      setTest(r);
      qc.invalidateQueries({ queryKey: ["provider-models", provider.id] });
    },
    onError: (e) => setTest({ ok: false, latency_ms: null, detail: errorMessage(e), model_count: null, error: null, sample: null }),
  });
  const patch = useMutation({
    mutationFn: (body: { name?: string; base_url?: string | null; enabled?: boolean }) => api.patchProvider(provider.id, body),
    onSuccess: () => invalidateModels(qc),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveKey = useMutation({
    mutationFn: () => api.setProviderSecret(provider.id, key),
    onSuccess: () => {
      setKey("");
      invalidateModels(qc);
      toast.success("API key stored in the keyring.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const removeKey = useMutation({
    mutationFn: () => api.deleteProviderSecret(provider.id),
    onSuccess: () => {
      invalidateModels(qc);
      toast.success("API key removed.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProvider(provider.id),
    onSuccess: () => {
      invalidateModels(qc);
      toast.success(`${provider.name} removed.`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const caps = provider.capabilities;
  const chat = (models.data?.models ?? []).filter((m) => m.kind === "chat" || m.kind === "unknown");

  return (
    <li className="border-b border-line" data-testid={`provider-${provider.id}`}>
      <div className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 py-3">
        <button type="button" aria-expanded={open} aria-label={`${open ? "Collapse" : "Configure"} ${provider.name}`} onClick={() => setOpen((o) => !o)} className="inline-flex size-6 cursor-pointer items-center justify-center text-fg-4 hover:text-fg">
          <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        </button>
        <button type="button" className="min-w-0 cursor-pointer text-left" onClick={() => setOpen((o) => !o)}>
          <span className="flex items-center gap-2">
            <span className="text-fg-3">{caps.is_local ? <HardDrive className="size-4" /> : <Cloud className="size-4" />}</span>
            <span className={cn("truncate text-md font-semibold", provider.enabled ? "text-fg" : "text-fg-3")}>{provider.name}</span>
            <span className="label shrink-0">{KIND_LABEL[provider.kind] ?? provider.kind}</span>
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-6 text-xs text-fg-3">
            <span>{caps.is_local ? "Local" : caps.is_paid ? "Cloud · paid" : "Cloud"}</span>
            {provider.base_url && <span className="mono truncate">{provider.base_url}</span>}
            {!caps.is_local && !isPlan && (
              <span className="inline-flex items-center gap-1">
                <KeyRound className="size-3" />
                {provider.has_secret ? "key stored" : "no key"}
              </span>
            )}
            {provider.blocked_by_privacy && (
              <span className="inline-flex items-center gap-1 text-fg-2">
                <ShieldOff className="size-3" /> blocked by privacy settings
              </span>
            )}
            {!provider.enabled && <span className="text-fg-2">disabled</span>}
            {isPlan && (
              <span className="text-fg-2">
                {provider.options?.available
                  ? `signed in${provider.options.account ? ` as ${String((provider.options.account as { email?: string; label?: string }).email ?? (provider.options.account as { label?: string }).label ?? "")}` : ""}`
                  : provider.options?.plan_usage_enabled === false && provider.options?.account
                    ? "plan usage not granted"
                    : "not signed in"}
              </span>
            )}
          </span>
        </button>
        <div className="flex items-center gap-2">
          {!isPlan && (
            <Button size="sm" icon={<Zap className="size-3.5" />} loading={runTest.isPending} disabled={provider.blocked_by_privacy} onClick={() => runTest.mutate()}>
              Test
            </Button>
          )}
          <Switch label={`Enable ${provider.name}`} checked={provider.enabled} onChange={(enabled) => patch.mutate({ enabled })} />
        </div>
      </div>

      {test && (
        <div className="mb-3 ml-9 flex items-start gap-2 text-sm" role="status" data-testid="provider-test-result">
          {test.ok ? <Check className="mt-0.5 size-4 shrink-0 text-fg-2" /> : <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" />}
          <div className="min-w-0">
            <p className={test.ok ? "text-fg-2" : "text-fg"}>
              {test.ok ? "Connection works" : "Connection failed"}
              {test.latency_ms !== null && <span className="mono ml-2 text-xs text-fg-3">{formatMs(test.latency_ms)}</span>}
              {test.model_count !== null && <span className="ml-2 text-xs text-fg-3">{test.model_count} models</span>}
            </p>
            {(test.error?.message || test.detail) && <p className="break-words text-xs text-fg-3">{test.error?.message ?? test.detail}</p>}
            {test.sample && <p className="mono mt-1 line-clamp-2 text-xs text-fg-3">“{test.sample}”</p>}
          </div>
        </div>
      )}

      {open && (
        <div className="mb-4 ml-9 grid gap-x-10 gap-y-5 border-l border-line pl-5 md:grid-cols-2 animate-rise">
          {isPlan ? (
            <p className="text-sm text-fg-3 md:col-span-2">
              {typeof provider.options?.reason === "string" && provider.options.reason ? <span className="mb-1 block text-fg-2">{provider.options.reason}</span> : null}
              Sign-in, accounts and plan usage are managed on the{" "}
              <Link to="/settings/chatgpt" className="link">
                ChatGPT plan
              </Link>{" "}
              page.
            </p>
          ) : (
            <>
              <div className="flex flex-col gap-3">
                <Field label="Name" htmlFor={`name-${provider.id}`}>
                  <Input id={`name-${provider.id}`} value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== provider.name && patch.mutate({ name: name.trim() })} />
                </Field>
                <Field label="Base URL" htmlFor={`url-${provider.id}`}>
                  <Input
                    id={`url-${provider.id}`}
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    onBlur={() => baseUrl !== (provider.base_url ?? "") && patch.mutate({ base_url: baseUrl.trim() || null })}
                    className="mono text-xs"
                    spellCheck={false}
                    placeholder="provider default"
                  />
                </Field>
                <Field
                  label="API key"
                  htmlFor={`key-${provider.id}`}
                  hint={provider.has_secret ? "A key is stored in the keyring. It cannot be read back; paste a new one to replace it." : caps.is_local ? "Most local servers need no key." : "Required. Stored in the operating system keyring only."}
                >
                  <div className="flex gap-2">
                    <Input id={`key-${provider.id}`} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={provider.has_secret ? "••••••••  (stored)" : ""} className="mono text-xs" />
                    <Button disabled={!key} loading={saveKey.isPending} onClick={() => saveKey.mutate()}>
                      Save key
                    </Button>
                    {provider.has_secret && (
                      <Button variant="ghost" loading={removeKey.isPending} onClick={() => removeKey.mutate()}>
                        Remove
                      </Button>
                    )}
                  </div>
                </Field>
              </div>
              <div className="flex flex-col gap-3">
                <div>
                  <p className="text-sm font-medium text-fg">Models</p>
                  {provider.blocked_by_privacy ? (
                    <p className="mt-1 text-sm text-fg-3">
                      Blocked while cloud models are off. Allow them in{" "}
                      <Link to="/settings/privacy" className="link">
                        Privacy
                      </Link>
                      .
                    </p>
                  ) : !provider.enabled ? (
                    <p className="mt-1 text-sm text-fg-3">Enable the provider to discover its models.</p>
                  ) : models.isLoading ? (
                    <Skeleton className="mt-2 h-16 w-full" />
                  ) : models.data?.error ? (
                    <p className="mt-1 flex items-start gap-1.5 text-sm text-fg-2">
                      <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-danger" /> {models.data.error.message}
                    </p>
                  ) : (models.data?.models.length ?? 0) === 0 ? (
                    <p className="mt-1 text-sm text-fg-3">No models reported. {caps.is_local ? "Pull or load a model in the server first." : ""}</p>
                  ) : (
                    <ul className="mt-1 max-h-[168px] overflow-y-auto rounded-sm border border-line">
                      {models.data!.models.map((m) => (
                        <li key={m.id} className="flex items-baseline justify-between gap-3 border-b border-line px-2.5 py-1 last:border-b-0">
                          <span className="mono min-w-0 truncate text-xs text-fg">{m.display_name ?? m.id}</span>
                          <span className="shrink-0 text-2xs text-fg-4">
                            {m.kind !== "unknown" ? m.kind : ""}
                            {m.context_window ? ` · ${Math.round(m.context_window / 1000)}k ctx` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {chat.length > 0 && (
                  <div className="flex items-center gap-2">
                    <Select value={testModel} onChange={(e) => setTestModel(e.target.value)} aria-label="Model for a generation test" className="min-w-0 flex-1">
                      <option value="">Connection test only</option>
                      {chat.map((m) => (
                        <option key={m.id} value={m.id}>
                          Generate with {m.display_name ?? m.id}
                        </option>
                      ))}
                    </Select>
                    <Button loading={runTest.isPending} onClick={() => runTest.mutate()}>
                      Run test
                    </Button>
                  </div>
                )}
                <p className="text-xs leading-relaxed text-fg-4">
                  Structured output: {caps.structured_output.replace("_", " ")} · streaming {caps.streaming ? "yes" : "no"} · embeddings {caps.embeddings ? "yes" : "no"}
                  {caps.notes.length ? ` · ${caps.notes.join(" ")}` : ""}
                </p>
              </div>
            </>
          )}
          {!isPlan && (
            <div className="md:col-span-2">
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)}>
                Remove provider
              </Button>
            </div>
          )}
        </div>
      )}
      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        size="sm"
        title={`Remove ${provider.name}?`}
        description="Its stored API key is deleted and any task routed to it falls back to the default. Past reports keep their record of which model wrote them."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Remove provider
            </Button>
          </>
        }
      />
    </li>
  );
}

// ------------------------------------------------------------------ routing

function RoutePicker({ value, onChange, providers, allowDefault, label }: { value: Route | null; onChange: (next: Route | null) => void; providers: ProviderInfo[]; allowDefault?: boolean; label: string }) {
  const providerId = value?.provider_id ?? "";
  const models = useQuery({ queryKey: ["provider-models", providerId], queryFn: () => api.providerModels(providerId), enabled: providerId !== "", staleTime: 60_000 });
  const chat = (models.data?.models ?? []).filter((m) => m.kind === "chat" || m.kind === "unknown");
  const [draft, setDraft] = useState(value?.model ?? "");
  useEffect(() => setDraft(value?.model ?? ""), [value?.model]);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label={`${label} provider`}
        value={providerId}
        onChange={(e) => {
          const id = e.target.value;
          if (!id) return onChange(null);
          onChange({ provider_id: id, model: "" });
        }}
        className="w-[190px]"
      >
        <option value="">{allowDefault ? "Use default" : "Not set"}</option>
        {providers.map((p) => (
          <option key={p.id} value={p.id} disabled={!p.enabled || p.blocked_by_privacy}>
            {p.name}
            {!p.enabled ? " (disabled)" : p.blocked_by_privacy ? " (blocked)" : p.capabilities.is_local ? "" : " · cloud"}
          </option>
        ))}
      </Select>
      {value &&
        (chat.length > 0 ? (
          <Select aria-label={`${label} model`} value={value.model} onChange={(e) => onChange({ provider_id: value.provider_id, model: e.target.value })} className="w-[230px]">
            {!chat.some((m) => m.id === value.model) && <option value={value.model}>{value.model || "Choose a model…"}</option>}
            {chat.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name ?? m.id}
              </option>
            ))}
          </Select>
        ) : (
          <Input
            aria-label={`${label} model`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => draft !== value.model && onChange({ provider_id: value.provider_id, model: draft.trim() })}
            placeholder={models.isLoading ? "Loading models…" : "model id"}
            className="mono w-[230px] text-xs"
          />
        ))}
    </div>
  );
}

function RoutingSection({ providers }: { providers: ProviderInfo[] }) {
  const qc = useQueryClient();
  const routing = useQuery({ queryKey: keys.routing, queryFn: api.routing });
  const save = useMutation({
    mutationFn: (body: { default?: Route | null; tasks?: Partial<Record<TaskType, Route | null>> }) => api.setRouting(body),
    onSuccess: (data) => {
      qc.setQueryData(keys.routing, data);
      invalidateModels(qc);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (routing.isLoading) return <Skeleton className="mt-3 h-40 w-full" />;
  if (routing.isError || !routing.data) return <ErrorState className="mt-3" message={errorMessage(routing.error)} onRetry={() => routing.refetch()} />;
  const r = routing.data;
  const TASK_HINT: Record<TaskType, string> = {
    classify: "Fast, high-volume. A small local model is ideal.",
    summarize: "One per repository, cached until its content changes.",
    expand_query: "Only used when query expansion is on.",
    answer: "Ask AI answers from retrieved evidence.",
    reasoning: "Research planning and multi-repository synthesis.",
    compare: "Comparison narratives.",
  };
  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-3">
        <div className="min-w-0">
          <p className="text-base font-semibold text-fg">Default model</p>
          <p className="text-sm text-fg-3">Used by every task that has no route of its own.</p>
        </div>
        <RoutePicker label="Default" value={r.default ?? null} providers={providers} onChange={(next) => save.mutate({ default: next })} />
      </div>
      {TASKS.map((task) => {
        const own = r.tasks[task] ?? null;
        const eff = r.effective[task] ?? null;
        return (
          <div key={task} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-2.5">
            <div className="min-w-0">
              <p className="text-sm font-medium text-fg">{r.task_labels[task] ?? TASK_LABEL[task]}</p>
              <p className="text-xs text-fg-3">
                {TASK_HINT[task]}
                {!own && eff ? <span className="mono ml-1.5 text-fg-4">→ {eff.provider_id} · {eff.model}</span> : null}
              </p>
            </div>
            <RoutePicker label={TASK_LABEL[task] ?? task} value={own} providers={providers} allowDefault onChange={(next) => save.mutate({ tasks: { [task]: next } })} />
          </div>
        );
      })}
      <p className="mt-3 max-w-[76ch] text-xs leading-relaxed text-fg-4">
        If a routed provider fails or hits a usage limit, the request stops and tells you. StarIndex never substitutes another provider, paid or otherwise, on its own.
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ usage

function UsageSection() {
  const [days, setDays] = useState(30);
  const usage = useQuery({ queryKey: ["usage", days], queryFn: () => api.usage(days) });
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-fg-3">Tokens are reported by the provider where available, otherwise estimated.</p>
        <Select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Usage period">
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </Select>
      </div>
      {usage.isLoading ? (
        <Skeleton className="mt-3 h-24 w-full" />
      ) : usage.isError || !usage.data ? (
        <ErrorState className="mt-3" message={errorMessage(usage.error)} onRetry={() => usage.refetch()} />
      ) : usage.data.rows.length === 0 ? (
        <p className="mt-3 text-sm text-fg-4">No model usage in this period.</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="label py-1.5 pr-4 font-medium">Provider</th>
                <th className="label py-1.5 pr-4 font-medium">Model</th>
                <th className="label py-1.5 pr-4 font-medium">Task</th>
                <th className="label py-1.5 pr-4 text-right font-medium">Calls</th>
                <th className="label py-1.5 pr-4 text-right font-medium">Input</th>
                <th className="label py-1.5 pr-4 text-right font-medium">Output</th>
                <th className="label py-1.5 text-right font-medium">Est. cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.data.rows.map((row, i) => (
                <tr key={i} className="border-b border-line">
                  <td className="py-1.5 pr-4 text-fg-2">{row.provider_id}</td>
                  <td className="mono py-1.5 pr-4 text-xs text-fg-2">{row.model}</td>
                  <td className="py-1.5 pr-4 text-fg-3">{TASK_LABEL[row.task] ?? row.task}</td>
                  <td className="py-1.5 pr-4 text-right text-fg-2 tnum">{formatNumber(row.calls)}</td>
                  <td className="py-1.5 pr-4 text-right text-fg-2 tnum">
                    {row.estimated ? "≈ " : ""}
                    {formatNumber(row.input_tokens)}
                  </td>
                  <td className="py-1.5 pr-4 text-right text-fg-2 tnum">{formatNumber(row.output_tokens)}</td>
                  <td className="py-1.5 text-right tnum">{formatCost(row.cost_usd) ?? <span className="text-fg-4">—</span>}</td>
                </tr>
              ))}
            </tbody>
            {usage.data.total_cost_usd !== null && usage.data.total_cost_usd !== undefined && (
              <tfoot>
                <tr>
                  <td colSpan={6} className="py-2 text-right text-fg-3">
                    Estimated total
                  </td>
                  <td className="py-2 text-right font-semibold text-fg tnum">{formatCost(usage.data.total_cost_usd)}</td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
      {usage.data?.pricing_note && <p className="mt-2 max-w-[76ch] text-xs leading-relaxed text-fg-4">{usage.data.pricing_note}</p>}
    </div>
  );
}

function PricingEditor() {
  const settings = useSettings();
  const save = useSettingsPatch();
  const prices = settings.data?.pricing ?? [];
  const [draft, setDraft] = useState({ provider_kind: "openai", model_prefix: "", input: "", output: "" });
  const add = () => {
    const input = Number(draft.input);
    const output = Number(draft.output);
    if (!draft.model_prefix.trim() || !Number.isFinite(input) || !Number.isFinite(output) || input < 0 || output < 0) return;
    save({ pricing: [...prices, { provider_kind: draft.provider_kind, model_prefix: draft.model_prefix.trim(), input_per_mtok: input, output_per_mtok: output, source: "user" }] }, "Price saved.");
    setDraft({ ...draft, model_prefix: "", input: "", output: "" });
  };
  return (
    <div className="mt-2 max-w-[760px]">
      <p className="text-sm text-fg-3">
        StarIndex only shows a cost when it has a price for the model. Enter the prices from your provider's pricing page (USD per million tokens); nothing is guessed.
      </p>
      {prices.length > 0 && (
        <table className="mt-3 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="label py-1.5 pr-4 font-medium">Provider</th>
              <th className="label py-1.5 pr-4 font-medium">Model prefix</th>
              <th className="label py-1.5 pr-4 text-right font-medium">Input / MTok</th>
              <th className="label py-1.5 pr-4 text-right font-medium">Output / MTok</th>
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {prices.map((p, i) => (
              <tr key={`${p.provider_kind}-${p.model_prefix}-${i}`} className="border-b border-line">
                <td className="py-1.5 pr-4 text-fg-2">{KIND_LABEL[p.provider_kind] ?? p.provider_kind}</td>
                <td className="mono py-1.5 pr-4 text-xs text-fg-2">{p.model_prefix}</td>
                <td className="py-1.5 pr-4 text-right text-fg-2 tnum">${p.input_per_mtok}</td>
                <td className="py-1.5 pr-4 text-right text-fg-2 tnum">${p.output_per_mtok}</td>
                <td className="py-1 text-right">
                  <button
                    type="button"
                    aria-label={`Remove price for ${p.model_prefix}`}
                    onClick={() => save({ pricing: prices.filter((_, j) => j !== i) })}
                    className="inline-flex size-6 cursor-pointer items-center justify-center rounded-sm text-fg-4 hover:bg-hover hover:text-fg"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select value={draft.provider_kind} onChange={(e) => setDraft({ ...draft, provider_kind: e.target.value })} aria-label="Provider for price">
          {["openai", "anthropic", "gemini", "azure_openai", "openai_compat"].map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k] ?? k}
            </option>
          ))}
        </Select>
        <Input value={draft.model_prefix} onChange={(e) => setDraft({ ...draft, model_prefix: e.target.value })} placeholder="model id or prefix" aria-label="Model prefix" className="mono w-[200px] text-xs" />
        <Input value={draft.input} onChange={(e) => setDraft({ ...draft, input: e.target.value })} placeholder="input $" inputMode="decimal" aria-label="Input price per million tokens" className="w-[92px] text-right" />
        <Input value={draft.output} onChange={(e) => setDraft({ ...draft, output: e.target.value })} placeholder="output $" inputMode="decimal" aria-label="Output price per million tokens" className="w-[92px] text-right" />
        <Button onClick={add} disabled={!draft.model_prefix.trim() || draft.input === "" || draft.output === ""}>
          Add price
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ page

export default function ModelSettings() {
  const providers = useProviders();
  const settings = useSettings();
  const save = useSettingsPatch();
  const [adding, setAdding] = useState(false);
  const list = providers.data ?? [];
  const limits = settings.data?.research;
  return (
    <div>
      <PageTitle title="Model providers">
        Search never needs a model. Ask AI, Research, summaries and comparison narratives use whichever provider you route them to. Local is the default; nothing is sent to a cloud service unless you enable and confirm it.
      </PageTitle>

      <Group title="Providers">
        <div className="mt-1 flex items-center justify-between gap-3">
          <p className="text-sm text-fg-3">{list.length ? `${list.filter((p) => p.enabled).length} of ${list.length} enabled` : "No providers configured yet."}</p>
          <Button variant="primary" size="sm" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
            Add provider
          </Button>
        </div>
        {providers.isLoading ? (
          <Skeleton className="mt-3 h-40 w-full" />
        ) : providers.isError ? (
          <ErrorState className="mt-3" message={errorMessage(providers.error)} onRetry={() => providers.refetch()} />
        ) : list.length === 0 ? (
          <div className="mt-3 rounded-md border border-line bg-surface p-5">
            <p className="text-base font-semibold text-fg">Start with a local model</p>
            <p className="mt-1 max-w-[68ch] text-sm leading-relaxed text-fg-3">
              Install Ollama or LM Studio, load a model, then add it here. StarIndex discovers its models and keeps everything on this machine. Cloud APIs and a ChatGPT plan are optional alternatives.
            </p>
            <Button className="mt-3" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
              Add provider
            </Button>
          </div>
        ) : (
          <ul className="mt-2 border-t border-line">
            {list.map((p) => (
              <ProviderRow key={p.id} provider={p} />
            ))}
          </ul>
        )}
      </Group>

      <Group title="Routing by task">
        <RoutingSection providers={list} />
      </Group>

      {limits && (
        <Group title="Research limits" description="Hard bounds enforced by the orchestrator on every research run.">
          <SettingRow title="Tool calls per run">
            <NumberSetting label="Max tool calls" value={limits.max_tool_calls} min={1} max={60} onCommit={(n) => save({ research: { max_tool_calls: n } })} />
          </SettingRow>
          <SettingRow title="Time limit">
            <NumberSetting label="Max seconds" value={limits.max_seconds} min={10} max={3600} step={10} unit="s" onCommit={(n) => save({ research: { max_seconds: n } })} />
          </SettingRow>
          <SettingRow title="Retrieved context" description="Characters of evidence handed to the model.">
            <NumberSetting label="Max context characters" value={limits.max_context_chars} min={2000} max={400000} step={1000} width={120} onCommit={(n) => save({ research: { max_context_chars: n } })} />
          </SettingRow>
          <SettingRow title="Total token budget">
            <NumberSetting label="Max total tokens" value={limits.max_total_tokens} min={2000} max={5_000_000} step={1000} width={120} onCommit={(n) => save({ research: { max_total_tokens: n } })} />
          </SettingRow>
          <SettingRow title="Answer length" description="Maximum output tokens per generation, where the provider accepts a limit.">
            <NumberSetting label="Max output tokens" value={limits.max_output_tokens} min={128} max={32000} step={64} onCommit={(n) => save({ research: { max_output_tokens: n } })} />
          </SettingRow>
        </Group>
      )}

      <Group title="Usage and estimated cost">
        <UsageSection />
      </Group>

      <Group title="Model prices">
        <PricingEditor />
      </Group>

      <AddProviderDialog open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}
