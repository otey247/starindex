import { expect, test } from "@playwright/test";
import { blur, rows, search, tick, watchForErrors } from "./helpers";

test.describe("search", () => {
  test("keyword search finds repositories and shows cited passages", async ({ page }) => {
    const problems = watchForErrors(page);
    await page.goto("/search");
    await expect(page.getByText("Operators")).toBeVisible();
    await page.getByRole("radio", { name: "Keyword" }).click();
    await search(page, "vector database");
    await expect(page).toHaveURL(/q=vector\+database/);
    await expect(page).toHaveURL(/mode=keyword/);
    await expect(page.getByTestId("result-summary")).toContainText("keyword");
    await expect(rows(page).filter({ hasText: "chroma-core" }).first()).toBeVisible();
    // highlighted hits and a citation with path and line range
    await expect(page.locator("mark.hit").first()).toBeVisible();
    await expect(page.getByTitle("Open the cited passage").first()).toContainText(/\.md|\.toml|\.json/);
    expect(problems).toEqual([]);
  });

  test("hybrid search works without any model call", async ({ page }) => {
    let modelCalls = 0;
    page.on("request", (r) => /\/api\/(ask|research)$/.test(r.url()) && modelCalls++);
    await page.goto("/search?q=agent+framework+orchestration&mode=hybrid");
    await expect(page.getByTestId("result-summary")).toContainText("hybrid");
    expect(await rows(page).count()).toBeGreaterThan(3);
    expect(modelCalls).toBe(0);
    // explain mode exposes the ranking signals
    await page.getByRole("button", { name: "Explain ranking" }).click();
    await expect(page.getByText(/score \d/).first()).toBeVisible();
  });

  test("filters narrow results and are reflected in the URL", async ({ page }) => {
    await page.goto("/search?q=mcp&mode=keyword");
    await expect(page.getByTestId("result-summary")).toBeVisible();
    const before = await page.getByTestId("result-summary").innerText();
    await tick(page.getByRole("complementary", { name: "Filters" }), "TypeScript");
    await expect(page).toHaveURL(/lang=TypeScript/);
    await expect(page.getByText("lang: TypeScript")).toBeVisible();
    await expect(page.getByTestId("result-summary")).not.toHaveText(before);
    for (const row of await rows(page).all()) await expect(row).toContainText("TypeScript");
    // removing the chip clears the filter
    await page.getByRole("button", { name: "Remove lang: TypeScript" }).click();
    await expect(page).not.toHaveURL(/lang=/);
  });

  test("operators are parsed server-side", async ({ page }) => {
    await page.goto("/search");
    await page.getByRole("radio", { name: "Keyword" }).click();
    await search(page, "lang:rust stars:>10000");
    const count = await rows(page).count();
    expect(count).toBeGreaterThan(0);
    for (const row of await rows(page).all()) await expect(row).toContainText("Rust");
  });

  test("typos are corrected, with a way back to the literal query", async ({ page }) => {
    await page.goto("/search");
    await page.getByRole("radio", { name: "Keyword" }).click();
    await search(page, "agentic orchestraton");
    const notices = page.getByTestId("search-notices");
    await expect(notices).toContainText("Showing results for agentic orchestration");
    await notices.getByRole("button", { name: /Search instead for orchestraton/ }).click();
    await expect(page).toHaveURL(/orchestraton%22|orchestraton"/);
  });

  test("documentation scope returns passages", async ({ page }) => {
    await page.goto("/search?q=install&mode=keyword&scope=docs");
    await expect(page.getByTestId("result-summary")).toContainText(/passage/);
    await expect(page.getByTitle("Open the cited passage").first()).toBeVisible();
  });

  test("keyboard: j/k move through results, Enter opens the repository", async ({ page }) => {
    await page.goto("/search?q=mcp&mode=keyword");
    await expect(rows(page).first()).toBeVisible();
    await blur(page);
    await page.keyboard.press("j");
    await expect(rows(page).nth(0)).toHaveAttribute("data-focused", "true");
    await page.keyboard.press("j");
    await expect(rows(page).nth(1)).toHaveAttribute("data-focused", "true");
    await page.keyboard.press("k");
    await expect(rows(page).nth(0)).toHaveAttribute("data-focused", "true");
    const name = await rows(page).nth(0).locator("a").first().innerText();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/repo\/\d+/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(name.split("/").pop()!.trim());
  });

  test("a search can be saved and reopened; history is kept", async ({ page }) => {
    await page.goto("/search?q=workflow+automation&mode=keyword");
    await expect(page.getByTestId("result-summary")).toBeVisible();
    await page.getByRole("button", { name: "Save this search" }).click();
    const dialog = page.getByRole("dialog", { name: "Save this search" });
    await dialog.getByLabel("Name").fill("Automation tools");
    await dialog.getByRole("button", { name: "Save search" }).click();
    await expect(page.getByText("Search saved.")).toBeVisible();

    await page.goto("/search");
    await expect(page.getByRole("button", { name: /Automation tools/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /workflow automation/ }).first()).toBeVisible(); // recent
    await page.getByRole("button", { name: /Automation tools/ }).click();
    await expect(page).toHaveURL(/q=workflow\+automation/);
  });

  test("an empty result explains itself", async ({ page }) => {
    await page.goto("/search?q=zzzqqqxxyy&mode=keyword");
    await expect(page.getByText("No repositories match")).toBeVisible();
    await expect(page.getByRole("button", { name: "Try hybrid search" })).toBeVisible();
  });
});
