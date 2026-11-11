/**
 * Real-browser smoke test of keyboard interactions and core flows against `npm run dev:mock`.
 *   node scripts/smoke.cjs
 */
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://127.0.0.1:5174";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "pass" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};
const mock = (body) => fetch(`${BASE}/api/__mock`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

(async () => {
  await mock({ reset: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // --- search by keyboard
  await page.goto(`${BASE}/library`, { waitUntil: "networkidle" });
  await page.locator("h1").first().click();
  await page.keyboard.press("/");
  await page.waitForURL(/\/search/);
  await page.waitForSelector("[data-search-input]:focus");
  check("'/' jumps to the search bar", await page.evaluate(() => document.activeElement?.hasAttribute("data-search-input")));
  await page.keyboard.type("agentic orchestration with MCP support");
  await page.keyboard.press("Enter");
  await page.waitForSelector("[data-result-row]");
  check("Enter runs the search and syncs the URL", page.url().includes("q=agentic+orchestration+with+MCP+support"));
  const rowCount = await page.locator("[data-result-row]").count();
  check("results render", rowCount > 3, `${rowCount} rows`);

  await page.keyboard.press("Escape"); // leave the input
  await page.locator("h1, [data-testid=result-summary]").first().click();
  await page.keyboard.press("j");
  await page.keyboard.press("j");
  let focused = await page.locator("[data-result-row][data-focused]").count();
  const second = await page.locator("[data-result-row]").nth(1).getAttribute("data-focused");
  check("j moves focus down the result list", focused === 1 && second !== null);
  await page.keyboard.press("k");
  const first = await page.locator("[data-result-row]").nth(0).getAttribute("data-focused");
  check("k moves focus back up", first !== null);
  await page.keyboard.press("x");
  check("x adds the focused repository to the comparison tray", await page.getByText("Compare", { exact: true }).first().isVisible());
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/repo\/\d+/);
  check("Enter opens the focused repository", /\/repo\/\d+/.test(page.url()));

  // --- palette + navigation sequences
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog", { name: "Command palette" }).waitFor();
  await page.keyboard.type("three");
  await page.getByRole("option", { name: /pmndrs\/react-three-fiber/ }).waitFor();
  check("Ctrl+K palette finds repositories as you type", true);
  await page.keyboard.press("Escape");
  check("Esc closes the palette", (await page.getByRole("dialog", { name: "Command palette" }).count()) === 0);
  await page.locator("h1").first().click();
  await page.keyboard.press("g");
  await page.keyboard.press("l");
  await page.waitForURL(/\/library/);
  check("g l goes to the Library", page.url().includes("/library"));
  await page.keyboard.press("?");
  check("? opens shortcut help", await page.getByRole("dialog", { name: "Keyboard shortcuts" }).isVisible());
  await page.keyboard.press("Escape");

  // --- Enter on a focused button must still activate it (no shortcut hijack)
  await page.goto(`${BASE}/search?q=vector+database`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Save this search" }).focus();
  await page.keyboard.press("Enter");
  check("Enter activates a focused button", await page.getByRole("dialog", { name: "Save this search" }).isVisible());
  await page.keyboard.press("Escape");

  // --- evidence viewer from a matched passage
  await page.locator("[title='Open the cited passage']").first().click();
  await page.getByRole("dialog", { name: "Matched passage" }).waitFor();
  check("a citation opens the evidence viewer with a GitHub link", (await page.getByRole("link", { name: /Open on GitHub/ }).getAttribute("href")).startsWith("https://github.com/"));
  await page.keyboard.press("Escape");

  // --- typo correction
  await page.goto(`${BASE}/search?q=api+gatway+kubernetes&mode=keyword`, { waitUntil: "networkidle" });
  check("typo correction is shown", await page.getByText(/Showing results for/).isVisible());

  // --- ask: streamed, cited answer; save as report
  await page.goto(`${BASE}/ask`, { waitUntil: "networkidle" });
  await page.getByLabel("Question").fill("Which projects offer semantic search without requiring a cloud service?");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save report" }).waitFor({ timeout: 20000 });
  const chips = await page.getByRole("button", { name: /^Evidence E\d+$/ }).count();
  check("Ask streams an answer with citation chips", chips > 0, `${chips} citations`);
  await page.getByRole("button", { name: "Evidence E1" }).first().click();
  check("a citation chip opens its evidence", await page.getByRole("dialog", { name: "Evidence E1" }).isVisible());
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Save report" }).click();
  await page.getByRole("button", { name: "Saved", exact: true }).waitFor();
  check("an answer can be saved as a report", true);

  // --- cloud confirmation names the provider; nothing is sent before confirming
  await mock({ llm: "cloud" });
  await page.goto(`${BASE}/ask`, { waitUntil: "networkidle" });
  let askCalls = 0;
  page.on("request", (r) => r.url().endsWith("/api/ask") && askCalls++);
  await page.getByLabel("Question").fill("What generates animations from code?");
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Send repository context to a cloud model?" });
  await dialog.waitFor();
  check("cloud confirmation names the provider", (await dialog.getByTestId("cloud-provider").textContent()).includes("Anthropic"));
  check("no request is sent before confirmation", askCalls === 0);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  check("cancelling sends nothing", askCalls === 0);
  await mock({ llm: "local" });

  // --- provider failure offers explicit alternatives, never auto-switches
  await mock({ fail: "unavailable" });
  await page.goto(`${BASE}/ask`, { waitUntil: "networkidle" });
  askCalls = 0;
  await page.getByLabel("Question").fill("Which repos help with observability?");
  await page.keyboard.press("Enter");
  await page.getByText("The provider is unavailable").waitFor();
  check("a provider failure is shown with explicit alternatives", await page.getByText("Switch provider for this request").isVisible());
  await page.waitForTimeout(800);
  check("no automatic retry or provider switch happens", askCalls === 1, `${askCalls} request`);
  await mock({ fail: null });

  // --- research run with trace
  await page.goto(`${BASE}/research`, { waitUntil: "networkidle" });
  await page.getByLabel("Research question").fill("Which agent frameworks support MCP?");
  await page.getByRole("button", { name: "Run research" }).click();
  await page.getByRole("button", { name: "Save report" }).waitFor({ timeout: 30000 });
  await page.getByRole("tab", { name: /Research trace/ }).click();
  check("research finishes with a trace of tool calls", (await page.getByTestId("research-trace").locator("li").count()) >= 5);

  // --- compare + save
  const ids = await page.evaluate(async () => {
    const r = await fetch("/api/repos/list", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ limit: 3 }) });
    return (await r.json()).items.map((x) => x.id);
  });
  await page.goto(`${BASE}/compare?ids=${ids.join(",")}`, { waitUntil: "networkidle" });
  check("comparison table renders without a model", await page.getByTestId("compare-table").isVisible());
  await page.getByRole("button", { name: "Save comparison" }).click();
  await page.getByText("Comparison saved.").waitFor();
  check("a comparison can be saved", true);

  // --- collections: create + add
  await page.goto(`${BASE}/collections`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "New collection" }).click();
  await page.getByLabel("Name").fill("Smoke test collection");
  await page.getByRole("button", { name: "Create collection" }).click();
  await page.getByRole("link", { name: /Smoke test collection/ }).waitFor();
  check("a collection can be created", true);

  // --- notes autosave on a repository
  await page.goto(`${BASE}/repo/${ids[0]}`, { waitUntil: "networkidle" });
  await page.getByLabel("Personal notes").fill("Checked by the smoke test.");
  await page.getByText("Saved", { exact: true }).waitFor({ timeout: 5000 });
  await page.reload({ waitUntil: "networkidle" });
  check("notes autosave and survive a reload", (await page.getByLabel("Personal notes").inputValue()) === "Checked by the smoke test.");

  // --- settings: provider test + privacy switch
  await page.goto(`${BASE}/settings/models`, { waitUntil: "networkidle" });
  await page.getByTestId("provider-ollama").getByRole("button", { name: "Test" }).click();
  await page.getByTestId("provider-test-result").waitFor();
  check("a provider connection test reports its result", (await page.getByTestId("provider-test-result").textContent()).includes("Connection works"));
  await page.goto(`${BASE}/settings/privacy`, { waitUntil: "networkidle" });
  await page.getByRole("switch", { name: "Allow cloud models" }).click();
  await page.goto(`${BASE}/settings/models`, { waitUntil: "networkidle" });
  check("turning cloud models off blocks cloud providers", await page.getByTestId("provider-anthropic").getByText(/blocked by privacy settings/).isVisible());

  // --- first run
  await mock({ first_run: true });
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForURL(/\/welcome/);
  await page.getByRole("button", { name: "Connect" }).click();
  await page.getByRole("button", { name: "Start the first sync" }).click();
  await page.getByText("All starred repositories imported").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Open Search" }).click();
  await page.waitForURL(/\/search/);
  check("first run: connect, sync, land in Search", true);

  await mock({ reset: true });
  check("no uncaught page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exitCode = failed ? 1 : 0;
})().catch((err) => {
  console.error("FAIL  smoke run aborted:", err.message.split("\n")[0]);
  process.exit(1);
});
