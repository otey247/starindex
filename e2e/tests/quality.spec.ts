import { expect, test, type Page } from "@playwright/test";
import { api, blur, rows, watchForErrors } from "./helpers";

interface Feedback {
  id: number;
  repo_id: number;
  repo_full_name: string;
  verdict: string;
  rank: number | null;
  mode: string;
}

const feedbackFor = (page: Page, query: string) => api<Feedback[]>(page, `/search/feedback?query=${encodeURIComponent(query)}`);
async function clearFeedback(page: Page, query: string): Promise<void> {
  for (const f of await feedbackFor(page, query)) await api(page, `/search/feedback/${f.id}`, { method: "DELETE" });
}
const relevant = (row: ReturnType<typeof rows>) => row.getByRole("button", { name: "Relevant to this query" });
const notRelevant = (row: ReturnType<typeof rows>) => row.getByRole("button", { name: "Not relevant" });

test.describe("relevance feedback", () => {
  test("marking results relevant / not relevant persists across a reload and toggles off", async ({ page }) => {
    const problems = watchForErrors(page);
    const query = "vector database";
    await page.goto("/");
    await clearFeedback(page, query);
    await page.goto(`/search?q=${encodeURIComponent(query)}`);
    await expect(rows(page).first()).toBeVisible();
    const first = rows(page).nth(0);
    const second = rows(page).nth(1);
    const firstId = Number(await first.getAttribute("data-repo-id"));

    await relevant(first).click();
    await expect(relevant(first)).toHaveAttribute("aria-pressed", "true");
    await notRelevant(second).click();
    await expect(notRelevant(second)).toHaveAttribute("aria-pressed", "true");
    await expect(first).toHaveAttribute("data-verdict", "relevant");
    await expect(second).toHaveAttribute("data-verdict", "not_relevant");

    // the server recorded the 1-based rank and the executed mode
    const stored = await feedbackFor(page, query);
    expect(stored.map((f) => [f.verdict, f.rank]).sort()).toEqual([
      ["not_relevant", 2],
      ["relevant", 1],
    ]);
    expect(stored.every((f) => ["hybrid", "keyword", "semantic"].includes(f.mode))).toBeTruthy();
    expect(stored.find((f) => f.verdict === "relevant")?.repo_id).toBe(firstId);

    await page.reload();
    await expect(relevant(rows(page).nth(0))).toHaveAttribute("aria-pressed", "true");
    await expect(notRelevant(rows(page).nth(0))).toHaveAttribute("aria-pressed", "false");
    await expect(notRelevant(rows(page).nth(1))).toHaveAttribute("aria-pressed", "true");

    // switching the verdict replaces it; clicking the active one clears it
    await notRelevant(rows(page).nth(0)).click();
    await expect(notRelevant(rows(page).nth(0))).toHaveAttribute("aria-pressed", "true");
    await expect(relevant(rows(page).nth(0))).toHaveAttribute("aria-pressed", "false");
    await notRelevant(rows(page).nth(0)).click();
    await expect(notRelevant(rows(page).nth(0))).toHaveAttribute("aria-pressed", "false");
    await expect.poll(async () => (await feedbackFor(page, query)).length).toBe(1);
    expect(problems).toEqual([]);
  });

  test("'+' and '-' judge the focused result", async ({ page }) => {
    const query = "command line";
    await page.goto("/");
    await clearFeedback(page, query);
    await page.goto(`/search?q=${encodeURIComponent(query)}`);
    await expect(rows(page).first()).toBeVisible();
    await blur(page);
    await page.keyboard.press("j");
    await expect(rows(page).nth(0)).toHaveAttribute("data-focused", "true");
    await page.keyboard.press("+");
    await expect(rows(page).nth(0)).toHaveAttribute("data-verdict", "relevant");
    await page.keyboard.press("j");
    await page.keyboard.press("-");
    await expect(rows(page).nth(1)).toHaveAttribute("data-verdict", "not_relevant");
    // pressing the same key again clears the verdict
    await page.keyboard.press("-");
    await expect(rows(page).nth(1)).not.toHaveAttribute("data-verdict", "not_relevant");
    await expect.poll(async () => (await feedbackFor(page, query)).map((f) => f.verdict)).toEqual(["relevant"]);

    await page.keyboard.press("?");
    const help = page.getByRole("dialog", { name: /Keyboard shortcuts/i });
    await expect(help).toBeVisible();
    await expect(help).toContainText("Mark the focused result relevant");
  });

  test("a missing repository can be recorded and removed", async ({ page }) => {
    const query = "embedded analytics engine";
    await page.goto("/");
    await clearFeedback(page, query);
    await page.goto(`/search?q=${encodeURIComponent(query)}&mode=keyword`);
    await expect(page.getByTestId("feedback-footer")).toBeVisible();
    await page.getByRole("button", { name: "Missing a repository?" }).click();
    const dialog = page.getByRole("dialog", { name: "Which repository is missing?" });
    await dialog.getByRole("combobox", { name: "Search your library" }).fill("chroma");
    await dialog.getByRole("option", { name: /chroma-core\/chroma/ }).click();
    await expect(dialog).toBeHidden();
    const list = page.getByTestId("missing-list");
    await expect(list).toContainText("You said these should match:");
    await expect(list).toContainText("chroma-core/chroma");
    expect((await feedbackFor(page, query)).map((f) => [f.repo_full_name, f.verdict])).toEqual([["chroma-core/chroma", "missing"]]);

    await page.reload();
    await expect(page.getByTestId("missing-list")).toContainText("chroma-core/chroma");
    await page.getByTestId("missing-list").getByRole("button", { name: /Remove/ }).click();
    await expect(page.getByTestId("missing-list")).toBeHidden();
    await expect.poll(async () => (await feedbackFor(page, query)).length).toBe(0);
  });

  test("no feedback controls without a free-text query", async ({ page }) => {
    await page.goto("/search?lang=Python");
    await expect(rows(page).first()).toBeVisible();
    await expect(page.getByTestId("feedback-footer")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Relevant to this query" })).toHaveCount(0);
  });
});

test.describe("search quality", () => {
  test("shows counts, runs an evaluation, renders the table and offers no Apply without a suggestion", async ({ page }) => {
    test.setTimeout(180_000);
    const problems = watchForErrors(page);
    const query = "workflow orchestration";
    await page.goto("/");
    await clearFeedback(page, query);
    const found = await api<{ results: { repo: { id: number; full_name: string } }[] }>(page, "/search", { method: "POST", body: { query, mode: "keyword" } });
    const target = found.results[0].repo;
    await api(page, "/search/feedback", { method: "POST", body: { query, repo_id: target.id, verdict: "relevant", mode: "keyword", rank: 1 } });
    const status = await api<{ queries: number; judgments: number; min_queries_for_recommendation: number }>(page, "/eval/status");
    expect(status.queries).toBeLessThan(status.min_queries_for_recommendation); // too few for a suggestion

    await page.goto("/search?q=" + encodeURIComponent(query));
    await page.getByTestId("feedback-footer").getByRole("link", { name: "Search quality" }).click();
    await expect(page).toHaveURL(/\/settings\/quality/);
    await expect(page.getByRole("heading", { level: 2, name: "Search quality" })).toBeVisible();
    await expect(page.getByTestId("eval-queries")).toHaveText(String(status.queries));
    await expect(page.getByTestId("eval-judgments")).toHaveText(String(status.judgments));
    await expect(page.getByText(new RegExp(`${status.queries} of ${status.min_queries_for_recommendation} judged queries`))).toBeVisible();

    await page.getByRole("button", { name: "Run evaluation" }).click();
    await expect(page.getByRole("button", { name: "Evaluating…" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Run evaluation" })).toBeEnabled({ timeout: 150_000 });
    const table = page.getByTestId("eval-table");
    await expect(table).toBeVisible();
    await expect(table.locator("tbody tr").first()).toBeVisible();
    expect(await table.locator("tbody tr").count()).toBeGreaterThanOrEqual(3);
    await expect(table.getByText("current", { exact: true })).toHaveCount(1);
    await expect(table.locator('tr[data-config="keyword"]')).toContainText(/\d\.\d{3}/);
    await expect(page.getByTestId("eval-recommendation")).not.toBeEmpty();
    await expect(page.getByRole("button", { name: "Apply suggested settings" })).toHaveCount(0);

    // judged queries: listed with their repositories, removable, and clickable to re-run
    const judged = page.getByTestId("judged-queries").getByRole("listitem").filter({ has: page.getByRole("link", { name: query, exact: true }) });
    await expect(judged).toContainText(target.full_name);
    await expect(page.getByRole("link", { name: "Export judgments" })).toHaveAttribute("href", "/api/search/feedback/export");
    const exported = await page.request.get("/api/search/feedback/export");
    expect(exported.ok()).toBeTruthy();
    expect(await exported.text()).toContain(query);

    await judged.getByRole("link", { name: query, exact: true }).click();
    await expect(page).toHaveURL(/\/search\?q=workflow/);
    await expect(rows(page).first()).toBeVisible();
    await page.goBack();
    await judged.getByRole("button", { name: `Remove judgment for ${target.full_name}` }).click();
    await expect(page.getByTestId("judged-queries").getByRole("link", { name: query, exact: true })).toHaveCount(0);
    expect(problems).toEqual([]);
  });
});

test.describe("agent access (MCP)", () => {
  test("shows the connection commands and tools, and the tester runs search_repositories", async ({ page }) => {
    const problems = watchForErrors(page);
    const info = await (await page.request.get("/api/mcp/info")).json();
    await page.goto("/settings/agents");
    await expect(page.getByRole("heading", { level: 2, name: "Agent access (MCP)" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    await expect(nav.getByRole("link", { name: "Agent access (MCP)" })).toHaveAttribute("aria-current", "page");
    await nav.getByRole("link", { name: "Search quality" }).click();
    await expect(page).toHaveURL(/\/settings\/quality$/);
    await nav.getByRole("link", { name: "Agent access (MCP)" }).click();
    await expect(page).toHaveURL(/\/settings\/agents$/);
    await expect(page.getByTestId("copy-claude-code-http-")).toHaveText(info.claude_code_http);
    await expect(page.getByTestId("copy-claude-code-http-")).toContainText("claude mcp add --transport http starindex");
    await expect(page.getByTestId("copy-claude-code-stdio-")).toHaveText(info.claude_code_stdio);
    await expect(page.getByTestId("copy-claude-desktop-config")).toContainText('"mcpServers"');
    await expect(page.getByText(/keyword search only/)).toBeVisible();

    const list = page.getByTestId("tool-list");
    await expect(list.getByRole("listitem")).toHaveCount(info.tools.length);
    await expect(list).toContainText("search_repositories");
    await expect(list).toContainText("get_repository_details");

    await expect(page.getByRole("combobox", { name: "Tool to try" })).toHaveValue("search_repositories");
    const args = page.getByRole("textbox", { name: "Tool arguments (JSON)" });
    await expect(args).toHaveValue(/"query"/);
    await args.fill('{"query": "vector database", "limit": 5}');
    await page.getByRole("button", { name: "Run tool" }).click();
    const result = page.getByTestId("tool-result");
    await expect(result).toContainText("repositories for 'vector database'");
    await expect(result).toContainText(/\d+ evidence items?/);

    // invalid JSON is reported, not sent
    await args.fill("{oops");
    await page.getByRole("button", { name: "Run tool" }).click();
    await expect(page.getByRole("alert")).toContainText("not valid JSON");
    expect(problems).toEqual([]);
  });
});
