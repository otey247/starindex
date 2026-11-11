import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProviderInfo } from "@/api/types";
import { renderApp } from "@/test/utils";
import ModelSettings from "./ModelSettings";

const api = vi.hoisted(() => ({
  providers: vi.fn(),
  settings: vi.fn(),
  routing: vi.fn(),
  usage: vi.fn(),
  providerTemplates: vi.fn(),
  providerModels: vi.fn(),
  testProvider: vi.fn(),
  patchProvider: vi.fn(),
  setProviderSecret: vi.fn(),
  deleteProviderSecret: vi.fn(),
  deleteProvider: vi.fn(),
  createProvider: vi.fn(),
  setRouting: vi.fn(),
  patchSettings: vi.fn(),
}));
vi.mock("@/api/client", () => ({ api }));

const caps = (local: boolean) => ({ is_local: local, is_paid: !local, streaming: true, model_discovery: true, embeddings: local, structured_output: local ? ("json_schema" as const) : ("tool" as const), accepts_temperature: true, accepts_max_output_tokens: true, notes: [] });
const ollama: ProviderInfo = { id: "ollama", kind: "ollama", name: "Ollama", category: "local", base_url: "http://127.0.0.1:11434", enabled: true, options: {}, has_secret: false, capabilities: caps(true), blocked_by_privacy: false, created_at: null };
const anthropic: ProviderInfo = { id: "anthropic", kind: "anthropic", name: "Anthropic", category: "cloud", base_url: null, enabled: true, options: {}, has_secret: true, capabilities: caps(false), blocked_by_privacy: true, created_at: null };
const routing = { default: { provider_id: "ollama", model: "qwen3:14b" }, tasks: {}, effective: { classify: { provider_id: "ollama", model: "qwen3:14b" }, summarize: null, expand_query: null, answer: null, reasoning: null, compare: null }, task_labels: {} };

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.providers.mockResolvedValue([ollama, anthropic]);
  api.settings.mockResolvedValue({ privacy: { allow_cloud_models: false, confirm_before_cloud: true, offline_mode: false }, research: { max_tool_calls: 12, max_seconds: 240, max_context_chars: 36000, max_total_tokens: 150000, max_output_tokens: 3000 } });
  api.routing.mockResolvedValue(routing);
  api.usage.mockResolvedValue({ since: "", rows: [{ provider_id: "ollama", provider_kind: "ollama", model: "qwen3:14b", task: "answer", calls: 4, input_tokens: 1000, output_tokens: 200, cost_usd: null, estimated: true }], total_cost_usd: null, pricing_note: "Cost is shown only when a price is configured." });
  api.providerModels.mockResolvedValue({ provider_id: "ollama", models: [{ id: "qwen3:14b", display_name: null, kind: "chat", context_window: 40960, size_bytes: null, details: {} }], error: null });
  api.providerTemplates.mockResolvedValue([
    { kind: "ollama", label: "Ollama", category: "local", description: "Local models through Ollama.", default_base_url: "http://127.0.0.1:11434", needs_api_key: false, api_key_optional: false, base_url_editable: true, option_fields: [], docs_url: null },
    { kind: "openai", label: "OpenAI API", category: "cloud", description: "OpenAI's paid API.", default_base_url: "https://api.openai.com/v1", needs_api_key: true, api_key_optional: false, base_url_editable: false, option_fields: [], docs_url: null },
  ]);
});

describe("Model provider settings", () => {
  it("lists providers with local/cloud status, key state and privacy blocks", async () => {
    renderApp(<ModelSettings />);
    const local = await screen.findByTestId("provider-ollama");
    expect(within(local).getByText("Local")).toBeInTheDocument();
    expect(within(local).getByText("http://127.0.0.1:11434")).toBeInTheDocument();
    const cloud = screen.getByTestId("provider-anthropic");
    expect(within(cloud).getByText("Cloud · paid")).toBeInTheDocument();
    expect(within(cloud).getByText("key stored")).toBeInTheDocument();
    expect(within(cloud).getByText(/blocked by privacy settings/)).toBeInTheDocument();
    expect(within(cloud).getByRole("button", { name: "Test" })).toBeDisabled();
    expect(screen.getByText("2 of 2 enabled")).toBeInTheDocument();
  });

  it("tests a connection and shows the outcome", async () => {
    api.testProvider.mockResolvedValue({ ok: true, latency_ms: 38, detail: "5 models available", model_count: 5, error: null, sample: null });
    renderApp(<ModelSettings />);
    const row = await screen.findByTestId("provider-ollama");
    await userEvent.click(within(row).getByRole("button", { name: "Test" }));
    const result = await within(row).findByTestId("provider-test-result");
    expect(result).toHaveTextContent("Connection works");
    expect(result).toHaveTextContent("38 ms");
    expect(result).toHaveTextContent("5 models");
    expect(api.testProvider).toHaveBeenCalledWith("ollama", null);
  });

  it("shows a failed connection test with the provider's message", async () => {
    api.testProvider.mockResolvedValue({ ok: false, latency_ms: null, detail: "", model_count: null, error: { kind: "unavailable", message: "Could not connect to http://127.0.0.1:11434.", provider_id: "ollama", provider_kind: "ollama", status: null, code: null, retryable: true, retry_after_s: null, action_url: null, action_label: null }, sample: null });
    renderApp(<ModelSettings />);
    const row = await screen.findByTestId("provider-ollama");
    await userEvent.click(within(row).getByRole("button", { name: "Test" }));
    expect(await within(row).findByTestId("provider-test-result")).toHaveTextContent("Connection failed");
    expect(within(row).getByText("Could not connect to http://127.0.0.1:11434.")).toBeInTheDocument();
  });

  it("enables and disables a provider", async () => {
    api.patchProvider.mockResolvedValue({ ...ollama, enabled: false });
    renderApp(<ModelSettings />);
    const row = await screen.findByTestId("provider-ollama");
    await userEvent.click(within(row).getByRole("switch", { name: "Enable Ollama" }));
    expect(api.patchProvider).toHaveBeenCalledWith("ollama", { enabled: false });
  });

  it("stores an API key write-only: the stored value is never rendered", async () => {
    api.setProviderSecret.mockResolvedValue({ ...anthropic });
    renderApp(<ModelSettings />);
    const row = await screen.findByTestId("provider-anthropic");
    await userEvent.click(within(row).getByRole("button", { name: "Configure Anthropic" }));
    const key = within(row).getByLabelText("API key") as HTMLInputElement;
    expect(key).toHaveAttribute("type", "password");
    expect(key.value).toBe("");
    expect(within(row).getByText(/cannot be read back/)).toBeInTheDocument();
    await userEvent.type(key, "sk-test-123");
    await userEvent.click(within(row).getByRole("button", { name: "Save key" }));
    expect(api.setProviderSecret).toHaveBeenCalledWith("anthropic", "sk-test-123");
    await waitFor(() => expect(key.value).toBe(""));
  });

  it("discovers models when a provider is expanded", async () => {
    renderApp(<ModelSettings />);
    const row = await screen.findByTestId("provider-ollama");
    await userEvent.click(within(row).getByRole("button", { name: "Configure Ollama" }));
    await waitFor(() => expect(api.providerModels).toHaveBeenCalledWith("ollama"));
    expect(await within(row).findByText("qwen3:14b")).toBeInTheDocument();
  });

  it("adds a provider from a template and warns that cloud is blocked", async () => {
    api.createProvider.mockResolvedValue({ ...anthropic, id: "openai-api", kind: "openai", name: "OpenAI API" });
    renderApp(<ModelSettings />);
    await screen.findByTestId("provider-ollama");
    await userEvent.click(screen.getAllByRole("button", { name: "Add provider" })[0]!);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: /OpenAI API/ }));
    expect(within(dialog).getByText(/Cloud models are currently blocked/)).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Add provider" });
    expect(submit).toBeDisabled(); // an API key is required for paid providers
    await userEvent.type(within(dialog).getByLabelText("API key"), "sk-abc");
    await userEvent.click(submit);
    expect(api.createProvider).toHaveBeenCalledWith({ kind: "openai", name: "OpenAI API", base_url: "https://api.openai.com/v1", api_key: "sk-abc", options: {} });
  });

  it("states the no-silent-fallback rule next to routing and shows usage without invented cost", async () => {
    renderApp(<ModelSettings />);
    expect(await screen.findByText(/never substitutes another provider/)).toBeInTheDocument();
    expect(await screen.findByText("Cost is shown only when a price is configured.")).toBeInTheDocument();
    const usageRow = (await screen.findByText("Answer synthesis", { selector: "td" })).closest("tr")!;
    expect(usageRow).toHaveTextContent("≈ 1,000");
    expect(usageRow).not.toHaveTextContent("$");
  });
});
