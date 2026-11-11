/**
 * Capture UI screenshots against `npm run dev:mock` (http://127.0.0.1:5174).
 *   node scripts/screenshots.cjs [--scale 2] [--only name1,name2] [--out screenshots] [--mobile]
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};
const BASE = arg("base", "http://127.0.0.1:5174");
const SCALE = Number(arg("scale", "1"));
const OUT = path.resolve(arg("out", "screenshots"));
const ONLY = arg("only", "") ? arg("only", "").split(",") : null;
const MOBILE_ONLY = args.includes("--mobile");
const DESKTOP_ONLY = args.includes("--desktop");

const mock = (body) => fetch(`${BASE}/api/__mock`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const REPO = 100000000 + 16 * 7919; // qdrant/qdrant
const R3F = 100000000 + 52 * 7919; // pmndrs/react-three-fiber

/** name, path, optional prepare(page) run after load, optional scenario posted before load */
const SHOTS = [
  { name: "search-start", url: "/search" },
  { name: "search-results", url: "/search?q=agentic+orchestration+with+MCP+support" },
  { name: "search-explain", url: "/search?q=semantic+search+without+requiring+a+cloud+service&explain=1" },
  { name: "search-typo", url: "/search?q=api+gatway+kubernetes&mode=keyword" },
  { name: "search-table", url: "/search?q=3d+rendering+physics&view=table" },
  { name: "search-grid", url: "/search?q=animation+from+code&view=grid" },
  { name: "search-filters", url: "/search?q=vector+database&lang=Rust&stars=5000..&license=Apache-2.0" },
  { name: "search-empty", url: "/search?q=quantum+blockchain+toaster&mode=keyword" },
  { name: "search-suggest", url: "/search", prepare: async (p) => { await p.fill("[data-search-input]", "mcp to"); await p.waitForTimeout(500); } },
  { name: "library", url: "/library" },
  { name: "library-list", url: "/library?view=list&category=3d" },
  { name: "library-selected", url: "/library", prepare: async (p) => { const boxes = p.locator("[data-result-row] label"); for (let i = 0; i < 3; i++) await boxes.nth(i).click(); await p.waitForTimeout(200); } },
  { name: "library-duplicates", url: "/library?view=duplicates" },
  { name: "repo-overview", url: `/repo/${REPO}` },
  { name: "repo-files", url: `/repo/${REPO}?tab=files` },
  { name: "repo-dependencies", url: `/repo/${REPO}?tab=dependencies` },
  { name: "repo-related", url: `/repo/${R3F}?tab=related` },
  { name: "repo-ideas", url: `/repo/${R3F}?tab=ideas`, prepare: async (p) => { await p.getByRole("button", { name: "Generate ideas" }).click(); await p.waitForTimeout(4500); } },
  { name: "ask-empty", url: "/ask" },
  { name: "ask-answer", url: "/ask?q=Which+of+my+starred+repositories+are+relevant+to+AI+governance+and+observability%3F", wait: 9000 },
  { name: "ask-no-model", url: "/ask?q=Find+Python+libraries+that+can+process+PDFs+locally", scenario: { llm: "none" }, wait: 2500, after: { llm: "local" } },
  { name: "ask-cloud-confirm", url: "/ask?q=Which+repos+generate+cinematic+animations+from+code%3F", scenario: { llm: "cloud" }, wait: 1200, after: { llm: "local" } },
  { name: "ask-usage-limit", url: "/ask", scenario: { llm: "chatgpt", chatgpt: "signed_in", fail: "usage_limit" }, prepare: async (p) => { await p.fill("textarea", "What have I starred for 3D house building?"); await p.keyboard.press("Enter"); await p.waitForTimeout(600); await p.getByRole("button", { name: /^Send to/ }).click(); await p.waitForTimeout(1500); }, after: { llm: "local", fail: null } },
  { name: "research-start", url: "/research" },
  { name: "research-running", url: "/research?run=1&q=Using+only+repositories+I+have+starred%2C+design+a+local-first+AI+search+platform+with+agentic+capabilities.", wait: 4200, live: true },
  { name: "research-done", url: "/research?run=1&q=Using+only+repositories+I+have+starred%2C+design+a+local-first+AI+search+platform+with+agentic+capabilities.", wait: 16000 },
  { name: "research-trace", url: "/research?run=1&q=Which+agent+frameworks+support+MCP%3F", wait: 15000, prepare: async (p) => { await p.getByRole("tab", { name: /Research trace/ }).click(); await p.waitForTimeout(300); } },
  { name: "compare", url: `/compare?ids=${REPO},${100000000 + 17 * 7919},${100000000 + 23 * 7919}` },
  { name: "compare-empty", url: "/compare" },
  { name: "collections", url: "/collections" },
  { name: "collection-detail", url: "/collections/2" },
  { name: "reports", url: "/collections?tab=reports" },
  { name: "categories", url: "/collections?tab=categories" },
  { name: "sync", url: "/sync" },
  { name: "sync-running", url: "/sync", scenario: { running_sync: true }, wait: 1500, live: true },
  { name: "settings-github", url: "/settings/github" },
  { name: "settings-indexing", url: "/settings/indexing" },
  { name: "settings-search", url: "/settings/search" },
  { name: "settings-models", url: "/settings/models", prepare: async (p) => { await p.getByRole("button", { name: "Configure Ollama" }).click(); await p.waitForTimeout(500); } },
  { name: "settings-add-provider", url: "/settings/models", prepare: async (p) => { await p.getByRole("button", { name: "Add provider" }).first().click(); await p.waitForTimeout(400); } },
  { name: "settings-chatgpt", url: "/settings/chatgpt" },
  { name: "settings-chatgpt-signed-in", url: "/settings/chatgpt", scenario: { chatgpt: "signed_in" } },
  { name: "settings-chatgpt-welcome", url: "/settings/chatgpt", scenario: { chatgpt: "signed_in", welcome: true } },
  { name: "settings-chatgpt-no-consent", url: "/settings/chatgpt", scenario: { chatgpt: "no_consent" } },
  { name: "settings-privacy", url: "/settings/privacy" },
  { name: "settings-storage", url: "/settings/storage" },
  { name: "settings-diagnostics", url: "/settings/diagnostics" },
  { name: "palette", url: "/search", prepare: async (p) => { await p.keyboard.press("Control+k"); await p.keyboard.type("three"); await p.waitForTimeout(600); } },
  { name: "shortcuts", url: "/library", desktopOnly: true, prepare: async (p) => { await p.locator("body").click({ position: { x: 700, y: 400 } }); await p.keyboard.press("?"); await p.waitForTimeout(300); } },
  { name: "evidence-viewer", url: "/search?q=agentic+orchestration+with+MCP+support", prepare: async (p) => { await p.locator("[title='Open the cited passage']").first().click(); await p.waitForTimeout(600); } },
  { name: "onboarding", url: "/", scenario: { first_run: true }, wait: 700 },
  { name: "onboarding-sync", url: "/", scenario: { first_run: true }, wait: 500, prepare: async (p) => { await p.getByRole("button", { name: "Connect" }).click(); await p.waitForTimeout(700); await p.getByRole("button", { name: "Start the first sync" }).click(); await p.waitForTimeout(4200); }, after: { reset: true } },
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const viewports = [];
  if (!MOBILE_ONLY) viewports.push({ tag: "desktop", width: 1440, height: 900 });
  if (!DESKTOP_ONLY) viewports.push({ tag: "mobile", width: 390, height: 844 });
  const problems = [];
  for (const vp of viewports) {
    for (const shot of SHOTS) {
      if (ONLY && !ONLY.includes(shot.name)) continue;
      if (shot.desktopOnly && vp.tag === "mobile") continue;
      await mock({ reset: true });
      if (shot.scenario) await mock(shot.scenario);
      const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: SCALE, isMobile: vp.tag === "mobile", hasTouch: vp.tag === "mobile" });
      const page = await context.newPage();
      page.on("pageerror", (e) => problems.push(`${shot.name}/${vp.tag}: pageerror ${e.message}`));
      page.on("console", (m) => {
        if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push(`${shot.name}/${vp.tag}: console ${m.text().slice(0, 200)}`);
      });
      try {
        await page.goto(BASE + shot.url, { waitUntil: shot.live ? "load" : "networkidle", timeout: 20000 }).catch(() => page.waitForTimeout(800));
        await page.waitForTimeout(shot.wait ?? 450);
        if (shot.prepare) await shot.prepare(page);
        // horizontal overflow check: a page must never scroll sideways
        const overflow = await page.evaluate(() => {
          const main = document.querySelector("main");
          return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
        });
        if (overflow > 1) problems.push(`${shot.name}/${vp.tag}: horizontal overflow ${overflow}px`);
        await page.screenshot({ path: path.join(OUT, `${shot.name}.${vp.tag}.png`) });
        process.stdout.write(`ok   ${shot.name}.${vp.tag}\n`);
      } catch (err) {
        problems.push(`${shot.name}/${vp.tag}: ${String(err.message).split("\n")[0]}`);
        await page.screenshot({ path: path.join(OUT, `${shot.name}.${vp.tag}.png`) }).catch(() => {});
        process.stdout.write(`FAIL ${shot.name}.${vp.tag}\n`);
      }
      await context.close();
      if (shot.after) await mock(shot.after);
    }
  }
  await mock({ reset: true });
  await browser.close();
  if (problems.length) {
    console.log("\nProblems:\n" + problems.map((p) => "  - " + p).join("\n"));
    process.exitCode = 1;
  } else console.log("\nNo console errors, page errors or horizontal overflow.");
})();
