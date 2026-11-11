import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, repoId, rows, tick } from "./helpers";

test.describe("mobile layout", () => {
  test("search, filters drawer and navigation work on a phone", async ({ page }) => {
    await page.goto("/search?q=mcp&mode=keyword");
    await expect(rows(page).first()).toBeVisible();
    await noHorizontalOverflow(page);

    // bottom tab bar instead of the side rail
    const tabs = page.getByRole("navigation", { name: "Primary" }).last();
    await expect(tabs.getByRole("link", { name: "Library" })).toBeVisible();

    // filters live in a drawer
    await page.getByRole("button", { name: /^Filters/ }).click();
    const drawer = page.getByRole("dialog", { name: "Filters" });
    await tick(drawer, "Python");
    await expect(page).toHaveURL(/lang=Python/);
    await drawer.getByRole("button", { name: "Close panel" }).click();
    await expect(drawer).toBeHidden();
    await noHorizontalOverflow(page);

    // the rest of the sections are one tap away
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("dialog", { name: "Navigation" }).getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings/);
    await noHorizontalOverflow(page);
  });

  test("library, repository, compare, research and sync fit the viewport", async ({ page }) => {
    await page.goto("/library");
    await expect(rows(page).first()).toBeVisible();
    await noHorizontalOverflow(page);

    const id = await repoId(page, "chroma-core/chroma");
    await page.goto(`/repo/${id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("chroma");
    await noHorizontalOverflow(page);

    const other = await repoId(page, "duckdb/duckdb");
    await page.goto(`/compare?ids=${id},${other}`);
    await expect(page.getByTestId("compare-table")).toBeVisible();
    await noHorizontalOverflow(page);

    for (const path of ["/research", "/ask", "/sync", "/collections", "/settings/models"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await noHorizontalOverflow(page);
    }
  });
});
