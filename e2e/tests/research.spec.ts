import { expect, test } from "@playwright/test";
import { watchForErrors } from "./helpers";

test.describe("research", () => {
  test("run research, inspect the trace, open a citation, save the report", async ({ page }) => {
    const problems = watchForErrors(page);
    await page.goto("/research");
    await expect(page.getByText(/≤ \d+ tool calls/)).toBeVisible();
    await page.getByLabel("Research question").fill("Which agent frameworks in my stars support MCP, and how do they differ?");
    await page.getByRole("button", { name: "Run research" }).click();

    // provider and model are shown while it runs
    const report = page.getByRole("region", { name: "Research report" });
    await expect(report).toContainText("Fake LLM", { timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 60_000 });
    await expect(report).toContainText("fake-good");
    await expect(report).toContainText(/\d+ \/ \d+ tool calls/);

    // research trace: tools and searches, no model reasoning
    await page.getByRole("tab", { name: /Research trace/ }).click();
    const trace = page.getByTestId("research-trace");
    expect(await trace.getByRole("listitem").count()).toBeGreaterThanOrEqual(3);
    await expect(trace).toContainText("search_repositories");

    // citations open the underlying evidence with a GitHub link
    await page.getByRole("tab", { name: /^Evidence/ }).click();
    await report.getByRole("button", { name: /^Evidence E\d+$/ }).first().click();
    const viewer = page.getByRole("dialog", { name: /Evidence E\d+/ });
    await expect(viewer).toBeVisible();
    await expect(viewer.getByText("Last indexed")).toBeVisible();
    await expect(viewer.getByRole("link", { name: /Open on GitHub/ })).toHaveAttribute("href", /^https:\/\/github\.com\//);
    await page.keyboard.press("Escape");
    await expect(viewer).toBeHidden();

    // deterministic facts derived by the orchestrator
    await expect(page.getByTestId("research-facts")).toContainText(/tool calls/);

    // save, then find it in the reports library
    await page.getByRole("button", { name: "Save report" }).click();
    await expect(page.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
    await page.goto("/collections?tab=reports");
    await page.getByRole("radio", { name: "Saved only" }).click();
    const link = page.getByRole("link", { name: /Which agent frameworks in my stars support MCP/ }).first();
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/reports\/\d+/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Which agent frameworks");
    await page.getByRole("tab", { name: /Research trace/ }).click();
    await expect(page.getByTestId("research-trace")).toBeVisible();
    await expect(page.getByRole("link", { name: /Markdown/ })).toHaveAttribute("href", /\/api\/reports\/\d+\/export/);
    expect(problems).toEqual([]);
  });

  test("unsupported claims and invented repositories are flagged, not presented as fact", async ({ page }) => {
    await page.goto("/research");
    await page.getByLabel("Model provider for this request").selectOption({ label: "Fake LLM · local" });
    await page.getByLabel("Model for this request").selectOption("fake-injected");
    await page.getByLabel("Research question").fill("Which agent frameworks support MCP?");
    await page.getByRole("button", { name: "Run research" }).click();
    await expect(page.getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 60_000 });
    const notes = page.getByRole("region", { name: "Verification notes" });
    await expect(notes).toContainText("evil/backdoor");
    await expect(notes).toContainText(/not in your starred library|does not match any retrieved evidence/);
    await expect(page.getByTestId("unverified-mark").first()).toBeVisible();
    // the invented repository never becomes a link into the library
    await expect(page.getByRole("link", { name: /evil\/backdoor/ })).toHaveCount(0);
  });

  test("a running research can be stopped", async ({ page }) => {
    await page.goto("/research");
    await page.getByLabel("Model provider for this request").selectOption({ label: "Fake LLM · local" });
    await page.getByLabel("Model for this request").selectOption("fake-slow");
    await page.getByLabel("Research question").fill("Compare every vector database I starred.");
    await page.getByRole("button", { name: "Run research" }).click();
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByRole("button", { name: "Run research" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Research report" })).toContainText("Stopped");
  });

  test("Ask AI answers with citations that open raw source lines", async ({ page }) => {
    await page.goto("/ask");
    await page.getByLabel("Question").fill("Which repositories provide vector search?");
    await page.getByLabel("Question").press("Enter");
    const turn = page.getByTestId("ask-turn").last();
    await expect(turn.getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 30_000 });
    await expect(turn).toContainText("Fake LLM");
    const chip = turn.getByRole("button", { name: /^Evidence E\d+$/ }).first();
    await chip.click();
    const viewer = page.getByRole("dialog", { name: /Evidence E\d+/ });
    await expect(viewer).toBeVisible();
    await expect(viewer.getByTestId("excerpt")).toBeVisible();
    await page.keyboard.press("Escape");
    // evidence panel groups sources by repository with their class
    await expect(page.getByRole("complementary", { name: "Evidence" }).first()).toContainText(/Observed|Documented/i);
  });
});
