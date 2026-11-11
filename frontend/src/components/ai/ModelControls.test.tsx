import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ModelOptions, ProviderErrorInfo, ProviderOption, RouteOverride } from "@/api/types";
import { renderApp } from "@/test/utils";
import { CHATGPT_USAGE_URL, CloudConfirmDialog, effectiveRoute, ProviderErrorPanel, UsageLine, useCloudGate } from "./ModelControls";

const { modelOptions } = vi.hoisted(() => ({ modelOptions: vi.fn() }));
vi.mock("@/api/client", () => ({ api: { modelOptions, providerModels: vi.fn().mockResolvedValue({ provider_id: "x", models: [], error: null }) } }));

const ollama: ProviderOption = { provider_id: "ollama", provider_kind: "ollama", name: "Ollama", is_local: true, is_paid: false, default_model: "qwen3:14b" };
const anthropic: ProviderOption = { provider_id: "anthropic", provider_kind: "anthropic", name: "Anthropic", is_local: false, is_paid: true, default_model: "claude-sonnet-5-5" };
const options = (overrides: Partial<ModelOptions> = {}): ModelOptions => ({
  active: { configured: true, provider_id: "ollama", provider_kind: "ollama", provider_name: "Ollama", model: "qwen3:14b", is_cloud: false },
  options: [ollama, anthropic],
  cloud_allowed: true,
  confirm_before_cloud: true,
  ...overrides,
});
const error = (overrides: Partial<ProviderErrorInfo> = {}): ProviderErrorInfo => ({
  kind: "unavailable",
  message: "Ollama did not respond.",
  provider_id: "ollama",
  provider_kind: "ollama",
  status: 503,
  code: null,
  retryable: true,
  retry_after_s: null,
  action_url: null,
  action_label: null,
  ...overrides,
});

describe("effectiveRoute", () => {
  it("uses the routing default when there is no override", () => {
    expect(effectiveRoute(options(), null)).toMatchObject({ providerId: "ollama", model: "qwen3:14b", isCloud: false, configured: true });
  });
  it("uses the override and knows whether it is a cloud provider", () => {
    expect(effectiveRoute(options(), { provider_id: "anthropic", model: "claude-sonnet-5-5" })).toMatchObject({ providerName: "Anthropic", isCloud: true });
  });
  it("reports an unconfigured state", () => {
    expect(effectiveRoute(options({ active: { configured: false, provider_id: null, provider_kind: null, provider_name: null, model: null, is_cloud: false } }), null).configured).toBe(false);
  });
});

describe("CloudConfirmDialog", () => {
  it("names exactly which provider and model receive the context", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    renderApp(<CloudConfirmDialog open providerName="Anthropic" providerKind="anthropic" model="claude-sonnet-5-5" onConfirm={onConfirm} onCancel={onCancel} />);
    const dialog = screen.getByRole("dialog", { name: "Send repository context to a cloud model?" });
    expect(within(dialog).getByTestId("cloud-provider")).toHaveTextContent("Anthropic (Anthropic)");
    expect(within(dialog).getByText("claude-sonnet-5-5")).toBeInTheDocument();
    expect(within(dialog).getByText(/retrieved repository passages/)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Send to Anthropic" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders nothing while closed", () => {
    renderApp(<CloudConfirmDialog open={false} providerName="Anthropic" providerKind="anthropic" model="m" onConfirm={() => undefined} onCancel={() => undefined} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

function Gate({ route, onRun }: { route: RouteOverride | null; onRun: (confirmCloud: boolean) => void }) {
  const gate = useCloudGate(route);
  return (
    <>
      <button onClick={() => gate.run(onRun)}>go</button>
      {gate.dialog}
    </>
  );
}

describe("useCloudGate", () => {
  beforeEach(() => modelOptions.mockReset());

  it("runs local requests immediately, without confirmation", async () => {
    modelOptions.mockResolvedValue(options());
    const onRun = vi.fn();
    renderApp(<Gate route={null} onRun={onRun} />);
    await waitFor(() => expect(modelOptions).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(onRun).toHaveBeenCalledWith(false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("holds a cloud request until the user confirms, then passes confirm_cloud=true", async () => {
    modelOptions.mockResolvedValue(options());
    const onRun = vi.fn();
    const { client } = renderApp(<Gate route={{ provider_id: "anthropic", model: "claude-sonnet-5-5" }} onRun={onRun} />);
    await waitFor(() => expect(client.getQueryData(["model-options"])).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(onRun).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: "Send to Anthropic" }));
    expect(onRun).toHaveBeenCalledWith(true);
  });

  it("does nothing when the user cancels", async () => {
    modelOptions.mockResolvedValue(options());
    const onRun = vi.fn();
    const { client } = renderApp(<Gate route={{ provider_id: "anthropic", model: "m" }} onRun={onRun} />);
    await waitFor(() => expect(client.getQueryData(["model-options"])).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(onRun).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("skips the dialog when the user turned confirmation off", async () => {
    modelOptions.mockResolvedValue(options({ confirm_before_cloud: false }));
    const onRun = vi.fn();
    const { client } = renderApp(<Gate route={{ provider_id: "anthropic", model: "m" }} onRun={onRun} />);
    await waitFor(() => expect(client.getQueryData(["model-options"])).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(onRun).toHaveBeenCalledWith(false);
  });
});

describe("ProviderErrorPanel", () => {
  it("never switches by itself: alternatives are explicit choices", async () => {
    const onSwitch = vi.fn();
    const onRetry = vi.fn();
    renderApp(<ProviderErrorPanel error={error()} alternatives={[anthropic]} onSwitch={onSwitch} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The provider is unavailable");
    expect(screen.getByText("Nothing is switched automatically. Choose one to run the request again.")).toBeInTheDocument();
    expect(onSwitch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Anthropic/ }));
    expect(onSwitch).toHaveBeenCalledWith(anthropic);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("makes Manage usage the primary action for a ChatGPT plan usage limit", () => {
    renderApp(
      <ProviderErrorPanel
        error={error({ kind: "usage_limit", provider_id: "chatgpt-plan", provider_kind: "chatgpt_plan", status: 429, code: "subscription_sharing_usage_limit_exceeded", retryable: false, message: "Your ChatGPT plan usage limit was reached for this app." })}
        alternatives={[ollama]}
        onSwitch={() => undefined}
      />,
    );
    expect(screen.getByText("Usage limit reached")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Manage usage/ })).toHaveAttribute("href", CHATGPT_USAGE_URL);
    expect(screen.getByText(/subscription_sharing_usage_limit_exceeded/)).toBeInTheDocument();
  });

  it("sends the user to setup when no model is configured, and offers no retry", () => {
    renderApp(<ProviderErrorPanel error={error({ kind: "not_configured", message: "No model is configured.", provider_id: null, provider_kind: null, status: null })} onRetry={() => undefined} />);
    expect(screen.getByRole("link", { name: "Configure a model" })).toHaveAttribute("href", "/settings/models");
    expect(screen.queryByRole("button", { name: /Try again|Retry/ })).toBeNull();
  });

  it("points to privacy settings when cloud models are disabled", () => {
    renderApp(<ProviderErrorPanel error={error({ kind: "cloud_disabled", message: "Cloud is off." })} />);
    expect(screen.getByRole("link", { name: "Privacy settings" })).toHaveAttribute("href", "/settings/privacy");
  });
});

describe("UsageLine", () => {
  it("shows tokens, and cost only when the server priced it", () => {
    const { rerender, container } = renderApp(<UsageLine usage={{ input_tokens: 1200, output_tokens: 340, estimated: true, cost_usd: null, calls: 3 }} />);
    expect(container).toHaveTextContent("≈ 1,200 in · 340 out · 3 calls");
    expect(container).not.toHaveTextContent("$");
    rerender(<UsageLine usage={{ input_tokens: 1200, output_tokens: 340, estimated: false, cost_usd: 0.0123, calls: 1 }} />);
    expect(container).toHaveTextContent("1,200 in · 340 out · $0.01");
    expect(container).not.toHaveTextContent("≈");
  });
});
