import { expect, test } from "@playwright/test";
import { LLM_URL } from "../env";
import { RUN, watchForErrors } from "./helpers";

const PROVIDER = `Second server ${RUN}`;
const PROVIDER_ID = `second-server-${RUN}`;

// These tests change provider configuration, so they run in order.
test.describe.configure({ mode: "serial" });

test.describe("settings and model switching", () => {
  test("the active provider and model are always visible", async ({ page }) => {
    await page.goto("/search");
    const active = page.getByTestId("active-model");
    await expect(active).toContainText("Fake LLM");
    await expect(active).toContainText("fake-good");
  });

  test("add a local provider, test it and discover its models", async ({ page }) => {
    const problems = watchForErrors(page);
    await page.goto("/settings/models");
    await expect(page.getByTestId("provider-fake-llm")).toBeVisible();
    await expect(page.getByTestId("provider-chatgpt-plan")).toContainText(/not signed in/i);

    await page.getByRole("button", { name: "Add provider" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: /^OpenAI-compatible server/ }).click();
    await dialog.getByLabel("Name").fill(PROVIDER);
    await dialog.getByLabel("Base URL").fill(LLM_URL);
    await dialog.getByRole("button", { name: "Add provider" }).click();
    await expect(dialog).toBeHidden();

    const row = page.getByTestId(`provider-${PROVIDER_ID}`);
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Test" }).click();
    await expect(row.getByTestId("provider-test-result")).toContainText("Connection works");
    await row.getByRole("button", { name: `Configure ${PROVIDER}` }).click();
    await expect(row.getByText("fake-good", { exact: true })).toBeVisible();
    await expect(row.getByText("fake-injected", { exact: true })).toBeVisible();
    // the API key field is write-only
    await expect(row.getByLabel("API key")).toHaveAttribute("type", "password");
    await expect(row.getByLabel("API key")).toHaveValue("");
    expect(problems).toEqual([]);
  });

  test("route a task to another provider and model", async ({ page }) => {
    await page.goto("/settings/models");
    await page.getByLabel("Comparison reports provider").selectOption({ label: PROVIDER });
    await page.getByLabel("Comparison reports model").selectOption("fake-good");
    await expect
      .poll(async () => (await (await page.request.get("/api/routing")).json()).tasks.compare)
      .toEqual({ provider_id: PROVIDER_ID, model: "fake-good" });
    // back to the default
    await page.getByLabel("Comparison reports provider").selectOption({ label: "Use default" });
    await expect.poll(async () => (await (await page.request.get("/api/routing")).json()).tasks.compare ?? null).toBeNull();
  });

  test("switch provider and model for a single Ask request", async ({ page }) => {
    await page.goto("/ask");
    await page.getByLabel("Model provider for this request").selectOption({ label: `${PROVIDER} · local` });
    await page.getByLabel("Model for this request").selectOption("fake-good");
    await page.getByLabel("Question").fill("Which repositories provide vector search?");
    await page.getByLabel("Question").press("Enter");
    const turn = page.getByTestId("ask-turn").last();
    await expect(turn.getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 30_000 });
    await expect(turn).toContainText(PROVIDER);
    const report = (await (await page.request.get("/api/reports?kind=ask&limit=1")).json())[0];
    expect(report.provider_id).toBe(PROVIDER_ID);
    // the global default is unchanged
    await expect(page.getByTestId("active-model")).toContainText("Fake LLM");
  });

  test("a provider failure offers explicit alternatives and never switches by itself", async ({ page }) => {
    let askRequests = 0;
    page.on("request", (r) => r.url().endsWith("/api/ask") && askRequests++);
    await page.goto("/ask");
    await page.getByLabel("Model provider for this request").selectOption({ label: "Fake LLM · local" });
    await page.getByLabel("Model for this request").selectOption("fake-500");
    await page.getByLabel("Question").fill("Which repositories help with observability?");
    await page.getByLabel("Question").press("Enter");
    const panel = page.getByRole("alert").filter({ hasText: "The provider is unavailable" });
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel).toContainText("HTTP 500");
    await expect(panel.getByText("Switch provider for this request")).toBeVisible();
    await page.waitForTimeout(1500);
    expect(askRequests, "no automatic retry or fallback").toBe(1);

    // an explicit click runs it again on the chosen provider
    await panel.getByRole("button", { name: new RegExp(PROVIDER) }).click();
    await expect(page.getByTestId("ask-turn").last().getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 30_000 });
    expect(askRequests).toBe(2);
  });

  test("a stream that dies is reported as incomplete, not as an answer", async ({ page }) => {
    await page.goto("/ask");
    await page.getByLabel("Model provider for this request").selectOption({ label: "Fake LLM · local" });
    await page.getByLabel("Model for this request").selectOption("fake-cutoff");
    await page.getByLabel("Question").fill("Which repositories do tracing?");
    await page.getByLabel("Question").press("Enter");
    await expect(page.getByText("The response ended before it was complete")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("ask-turn").last().getByRole("button", { name: "Save report" })).toHaveCount(0);
  });

  test("privacy: cloud models are off by default and a cloud provider is blocked", async ({ page }) => {
    await page.goto("/settings/privacy");
    const cloud = page.getByRole("switch", { name: "Allow cloud models" });
    await expect(cloud).toHaveAttribute("aria-checked", "false");
    await page.goto("/settings/chatgpt");
    await expect(page.getByText(/Cloud models are turned off/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue with ChatGPT" })).toBeDisabled();

    // allowing cloud models enables the ChatGPT sign-in entry point (the flow itself needs a real browser session)
    await page.goto("/settings/privacy");
    await cloud.click();
    await expect(cloud).toHaveAttribute("aria-checked", "true");
    await page.goto("/settings/chatgpt");
    await expect(page.getByRole("button", { name: "Continue with ChatGPT" })).toBeEnabled();
    await expect(page.getByRole("link", { name: /Manage usage/ }).first()).toHaveAttribute("href", "https://chatgpt.com/settings/usage");
    await page.goto("/settings/privacy");
    await cloud.click();
    await expect(cloud).toHaveAttribute("aria-checked", "false");
  });

  test("settings sections load: indexing, search, storage, schedules, diagnostics", async ({ page }) => {
    await page.goto("/settings/indexing");
    await expect(page.getByRole("radiogroup", { name: "Default index level" })).toBeVisible();
    await page.goto("/settings/search");
    await expect(page.getByLabel("Embedding model")).toHaveValue(/.+/);
    await expect(page.getByText(/CPU threads/)).toBeVisible();
    await page.goto("/settings/storage");
    await expect(page.getByText("Database (library, text, embeddings)")).toBeVisible();
    await page.getByRole("button", { name: "Back up now" }).click();
    await expect(page.getByText("Backup started.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Restore…" }).first()).toBeVisible({ timeout: 30_000 });
    await page.goto("/settings/schedules");
    await expect(page.getByText("Check for new stars")).toBeVisible();
    await page.goto("/settings/diagnostics");
    await expect(page.getByText("Running checks…")).toBeHidden({ timeout: 30_000 });
    await expect(page.getByRole("listitem").first()).toBeVisible();
  });
});
