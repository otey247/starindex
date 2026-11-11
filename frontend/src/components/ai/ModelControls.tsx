import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Cloud, Cpu, ExternalLink, HardDrive, Settings2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { useModelOptions } from "@/api/queries";
import type { ModelOptions, ProviderErrorInfo, ProviderOption, RouteOverride, UsageInfo } from "@/api/types";
import { Button, Dialog, Select } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatCost, formatNumber } from "@/lib/format";
import { toast } from "@/state/stores";

export const CHATGPT_USAGE_URL = "https://chatgpt.com/settings/usage";

export const KIND_LABEL: Record<string, string> = {
  ollama: "Ollama",
  lmstudio: "LM Studio",
  vllm: "vLLM",
  openai_compat: "OpenAI-compatible",
  openai: "OpenAI API",
  anthropic: "Anthropic",
  gemini: "Google Gemini",
  azure_openai: "Azure OpenAI",
  chatgpt_plan: "ChatGPT plan",
};

export interface EffectiveRoute {
  providerId: string | null;
  providerKind: string | null;
  providerName: string | null;
  model: string | null;
  isCloud: boolean;
  configured: boolean;
}

/** What will actually be used for a request given the routing default and an override. */
export function effectiveRoute(options: ModelOptions | undefined, override: RouteOverride | null): EffectiveRoute {
  if (override && options) {
    const opt = options.options.find((o) => o.provider_id === override.provider_id);
    return {
      providerId: override.provider_id,
      providerKind: opt?.provider_kind ?? null,
      providerName: opt?.name ?? override.provider_id,
      model: override.model,
      isCloud: opt ? !opt.is_local : false,
      configured: true,
    };
  }
  const a = options?.active;
  return {
    providerId: a?.provider_id ?? null,
    providerKind: a?.provider_kind ?? null,
    providerName: a?.provider_name ?? null,
    model: a?.model ?? null,
    isCloud: Boolean(a?.is_cloud),
    configured: Boolean(a?.configured),
  };
}

/** Display name for a provider id (falls back to the id itself). */
export function useProviderName(): (id: string | null | undefined) => string {
  const options = useModelOptions();
  return (id) => {
    if (!id) return "";
    const data = options.data;
    if (data?.active.provider_id === id && data.active.provider_name) return data.active.provider_name;
    return data?.options.find((o) => o.provider_id === id)?.name ?? id;
  };
}

/** "Using ChatGPT plan · Manage usage", shown near model selectors per OpenAI's UI guidelines. */
export function ChatGPTPlanNote({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-xs text-fg-2", className)}>
      <span className="font-medium">Using ChatGPT plan</span>
      <a href={CHATGPT_USAGE_URL} target="_blank" rel="noopener noreferrer" className="link inline-flex items-center gap-1">
        Manage usage
        <ExternalLink className="size-3" aria-hidden />
      </a>
    </span>
  );
}

/** Compact provider + model selection for a single request. `null` = routing default. */
export function ModelPicker({
  value,
  onChange,
  disabled,
  className,
}: {
  value: RouteOverride | null;
  onChange: (next: RouteOverride | null) => void;
  disabled?: boolean;
  className?: string;
}) {
  const options = useModelOptions();
  const data = options.data;
  const providerId = value?.provider_id ?? "";
  const models = useQuery({
    queryKey: ["provider-models", providerId],
    queryFn: () => api.providerModels(providerId),
    enabled: providerId !== "",
    staleTime: 60_000,
  });
  const eff = effectiveRoute(data, value);
  const chatModels = (models.data?.models ?? []).filter((m) => m.kind === "chat" || m.kind === "unknown");

  const pickProvider = (id: string) => {
    if (!id) return onChange(null);
    const opt = data?.options.find((o) => o.provider_id === id);
    onChange({ provider_id: id, model: opt?.default_model ?? "" });
  };

  if (data && data.options.length === 0 && !data.active.configured) {
    return (
      <Link to="/settings/models" className={cn("inline-flex items-center gap-1.5 text-sm text-fg-3 hover:text-fg", className)}>
        <Settings2 className="size-3.5" aria-hidden />
        No model configured — set one up
      </Link>
    );
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1.5", className)}>
      <span className="text-fg-3" aria-hidden>
        {eff.isCloud ? <Cloud className="size-3.5" /> : <HardDrive className="size-3.5" />}
      </span>
      <Select
        aria-label="Model provider for this request"
        value={providerId}
        disabled={disabled}
        onChange={(e) => pickProvider(e.target.value)}
        className="max-w-[220px]"
      >
        <option value="">
          {data?.active.configured
            ? `Default · ${data.active.provider_name ?? data.active.provider_id}`
            : "Default (not configured)"}
        </option>
        {(data?.options ?? []).map((o) => (
          <option key={o.provider_id} value={o.provider_id}>
            {o.name} {o.is_local ? "· local" : "· cloud"}
          </option>
        ))}
      </Select>
      {value ? (
        <Select
          aria-label="Model for this request"
          value={value.model}
          disabled={disabled || models.isLoading}
          onChange={(e) => onChange({ provider_id: value.provider_id, model: e.target.value })}
          className="max-w-[240px]"
        >
          {!chatModels.some((m) => m.id === value.model) && (
            <option value={value.model}>{value.model || (models.isLoading ? "Loading models…" : "Choose a model…")}</option>
          )}
          {chatModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.display_name ?? m.id}
            </option>
          ))}
        </Select>
      ) : (
        data?.active.model && <span className="mono text-xs text-fg-3">{data.active.model}</span>
      )}
      {eff.providerKind === "chatgpt_plan" && <ChatGPTPlanNote />}
      {models.data?.error && value && (
        <span className="inline-flex items-center gap-1 text-xs text-fg-3">
          <AlertTriangle className="size-3 text-warn" aria-hidden />
          {models.data.error.message}
        </span>
      )}
    </div>
  );
}

/**
 * Explicit consent before repository content leaves the machine. Names the exact
 * provider and model that will receive the context.
 */
export function CloudConfirmDialog({
  open,
  onCancel,
  onConfirm,
  providerName,
  providerKind,
  model,
  what = "The question and the retrieved repository passages (README sections, documentation, code excerpts and metadata)",
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  providerName: string | null;
  providerKind: string | null;
  model: string | null;
  what?: string;
}) {
  const kind = providerKind ? (KIND_LABEL[providerKind] ?? providerKind) : "a cloud provider";
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title="Send repository context to a cloud model?"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} data-autofocus>
            Send to {providerName ?? kind}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm leading-relaxed text-fg-2">
        <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-1.5 rounded-md border border-line bg-bg px-3.5 py-3">
          <dt className="text-fg-3">Provider</dt>
          <dd className="font-medium text-fg" data-testid="cloud-provider">
            {providerName ?? "Unknown"} <span className="font-normal text-fg-3">({kind})</span>
          </dd>
          <dt className="text-fg-3">Model</dt>
          <dd className="mono text-xs text-fg">{model || "provider default"}</dd>
          <dt className="text-fg-3">Leaves this machine</dt>
          <dd>{what}</dd>
        </dl>
        <p className="text-fg-3">
          Your library, notes and embeddings stay local. Nothing is sent until you confirm, and StarIndex never switches to
          another provider on its own.
        </p>
        <p className="text-xs text-fg-4">
          You can turn this prompt off, or block cloud models entirely, in{" "}
          <Link to="/settings/privacy" className="link" onClick={onCancel}>
            Settings → Privacy
          </Link>
          .
        </p>
      </div>
    </Dialog>
  );
}

const ERROR_TITLE: Record<string, string> = {
  not_configured: "No model is configured",
  cloud_disabled: "Cloud models are turned off",
  confirmation_required: "Confirmation needed",
  auth: "The provider rejected the credentials",
  consent_required: "ChatGPT plan usage isn't enabled",
  not_eligible: "This account can't use that capability",
  rate_limit: "The provider is rate limiting requests",
  usage_limit: "Usage limit reached",
  timeout: "The model took too long to respond",
  network: "The provider could not be reached",
  unavailable: "The provider is unavailable",
  unsupported: "The provider doesn't support this request",
  context_length: "The request exceeded the model's context window",
  bad_request: "The provider rejected the request",
  stream_incomplete: "The response ended before it was complete",
  invalid_response: "The model returned something unusable",
};

/**
 * A failed model request. Shows what happened and the explicit choices the user has;
 * switching provider is always a deliberate click.
 */
export function ProviderErrorPanel({
  error,
  alternatives = [],
  onRetry,
  onSwitch,
  className,
}: {
  error: ProviderErrorInfo;
  alternatives?: ProviderOption[];
  onRetry?: () => void;
  onSwitch?: (option: ProviderOption) => void;
  className?: string;
}) {
  const isUsageLimit = error.kind === "usage_limit";
  const isChatGPT = error.provider_kind === "chatgpt_plan";
  return (
    <div role="alert" className={cn("rounded-md border border-line-strong bg-surface p-4", className)}>
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-fg">{ERROR_TITLE[error.kind] ?? "The request failed"}</p>
          <p className="mt-1 text-sm leading-relaxed text-fg-2 break-words">{error.message}</p>
          {isUsageLimit && isChatGPT && (
            <p className="mt-1 text-sm leading-relaxed text-fg-3">
              The limit may apply to your ChatGPT plan or to StarIndex specifically. Review it in ChatGPT settings.
            </p>
          )}
          <p className="mono mt-2 text-2xs text-fg-4">
            {[error.provider_id, error.kind, error.status ? `HTTP ${error.status}` : null, error.code].filter(Boolean).join(" · ")}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {isUsageLimit && isChatGPT ? (
              <a
                href={CHATGPT_USAGE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-sm bg-accent-solid px-3 text-sm font-medium text-white hover:bg-accent-solid-hover"
              >
                Manage usage
                <ExternalLink className="size-3.5" aria-hidden />
              </a>
            ) : error.action_url ? (
              <a
                href={error.action_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-sm bg-accent-solid px-3 text-sm font-medium text-white hover:bg-accent-solid-hover"
              >
                {error.action_label ?? "Open"}
                <ExternalLink className="size-3.5" aria-hidden />
              </a>
            ) : null}
            {error.kind === "not_configured" && (
              <Link to="/settings/models" className="inline-flex h-8 items-center gap-1.5 rounded-sm bg-accent-solid px-3 text-sm font-medium text-white hover:bg-accent-solid-hover">
                Configure a model
              </Link>
            )}
            {error.kind === "cloud_disabled" && (
              <Link to="/settings/privacy" className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay">
                Privacy settings
              </Link>
            )}
            {(error.kind === "consent_required" || error.kind === "auth") && isChatGPT && (
              <Link to="/settings/chatgpt" className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-raised px-3 text-sm font-medium text-fg hover:bg-overlay">
                ChatGPT plan settings
              </Link>
            )}
            {onRetry && error.kind !== "not_configured" && error.kind !== "cloud_disabled" && (
              <Button onClick={onRetry}>{error.retryable ? "Try again" : "Retry with the same provider"}</Button>
            )}
          </div>
        </div>
      </div>

      {alternatives.length > 0 && onSwitch && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="label">Switch provider for this request</p>
          <p className="mt-1 text-xs text-fg-3">Nothing is switched automatically. Choose one to run the request again.</p>
          <ul className="mt-2 flex flex-col">
            {alternatives.map((opt) => (
              <li key={opt.provider_id}>
                <button
                  type="button"
                  onClick={() => onSwitch(opt)}
                  className="group flex w-full cursor-pointer items-center gap-3 rounded-sm px-2 py-1.5 text-left hover:bg-hover"
                >
                  <span className="text-fg-3">{opt.is_local ? <Cpu className="size-3.5" /> : <Cloud className="size-3.5" />}</span>
                  <span className="min-w-0 flex-1">
                    <span className="text-sm font-medium text-fg">{opt.name}</span>
                    <span className="ml-2 text-xs text-fg-3">
                      {opt.is_local ? "local" : "cloud"}
                      {opt.is_paid ? " · paid" : ""}
                      {opt.default_model ? ` · ${opt.default_model}` : ""}
                    </span>
                  </span>
                  <ArrowRight className="size-3.5 text-fg-4 group-hover:text-fg" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Tokens always; cost only when the server could price the model. */
export function UsageLine({ usage, className }: { usage: UsageInfo | null | undefined; className?: string }) {
  if (!usage || (usage.input_tokens === null && usage.output_tokens === null)) return null;
  const cost = formatCost(usage.cost_usd);
  return (
    <span className={cn("mono text-xs text-fg-3", className)}>
      {usage.estimated ? "≈ " : ""}
      {formatNumber(usage.input_tokens ?? 0)} in · {formatNumber(usage.output_tokens ?? 0)} out
      {usage.calls > 1 ? ` · ${usage.calls} calls` : ""}
      {cost ? ` · ${usage.estimated ? "≈ " : ""}${cost}` : ""}
    </span>
  );
}

/**
 * Hook: gate a request behind cloud confirmation when needed. Call `run(confirmFn)`;
 * `confirmFn(confirmCloud)` receives whether the user acknowledged the cloud send.
 */
export function useCloudGate(route: RouteOverride | null) {
  const options = useModelOptions();
  const [pending, setPending] = useState<((confirmCloud: boolean) => void) | null>(null);
  const eff = effectiveRoute(options.data, route);
  const needsConfirm = eff.configured && eff.isCloud && (options.data?.confirm_before_cloud ?? true);

  const run = (fn: (confirmCloud: boolean) => void, force = false) => {
    // A provider was picked for this request but no model yet: never send an empty model id.
    if (route && !route.model) {
      toast.info(`Choose a model for ${eff.providerName ?? "this provider"} first.`);
      return;
    }
    if (needsConfirm || force) setPending(() => fn);
    else fn(false);
  };

  const dialog = (
    <CloudConfirmDialog
      open={pending !== null}
      providerName={eff.providerName}
      providerKind={eff.providerKind}
      model={eff.model}
      onCancel={() => setPending(null)}
      onConfirm={() => {
        const fn = pending;
        setPending(null);
        fn?.(true);
      }}
    />
  );
  return { run, dialog, effective: eff, options: options.data };
}
