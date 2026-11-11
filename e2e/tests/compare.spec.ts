import { expect, test } from "@playwright/test";
import { rows } from "./helpers";

test.describe("compare", () => {
  test("pick repositories, read the table, save the comparison", async ({ page }) => {
    await page.goto("/compare");
    await expect(page.getByText("Pick repositories to compare")).toBeVisible();

    const picker = page.getByRole("combobox", { name: /Add the first repository/ });
    await picker.fill("chroma");
    await page.getByRole("option", { name: /chroma-core\/chroma/ }).click();
    const second = page.getByRole("combobox", { name: /Add another repository/ });
    await second.fill("duckdb");
    await page.getByRole("option", { name: /duckdb\/duckdb/ }).click();
    await expect(page).toHaveURL(/ids=\d+,\d+/);

    const table = page.getByTestId("compare-table");
    await expect(table).toBeVisible();
    for (const group of ["Identity", "Capabilities", "Architecture", "Maturity", "Dependencies", "Licensing"]) {
      await expect(table.getByText(group, { exact: true })).toBeVisible();
    }
    await expect(table.getByRole("row", { name: /^License / }).first()).toContainText(/Apache-2\.0|MIT/);
    await expect(page.getByText("License notes")).toBeVisible();

    // evidence behind a cell opens in the viewer
    await table.getByRole("button", { name: /^E\d+$/ }).first().click();
    await expect(page.getByRole("dialog", { name: /Evidence E\d+/ })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Save comparison" }).click();
    await expect(page.getByText("Comparison saved.")).toBeVisible();
    await page.goto("/collections?tab=reports");
    await page.getByRole("link", { name: /chroma/i }).first().click();
    await expect(page).toHaveURL(/\/reports\/\d+/);
    await expect(page.getByTestId("compare-table")).toBeVisible();
  });

  test("results can be queued for comparison from Search", async ({ page }) => {
    await page.goto("/search?q=mcp&mode=keyword");
    await expect(rows(page).first()).toBeVisible();
    await rows(page).nth(0).hover();
    await rows(page).nth(0).getByRole("button", { name: "Add to comparison" }).click();
    await rows(page).nth(1).hover();
    await rows(page).nth(1).getByRole("button", { name: "Add to comparison" }).click();
    await page.getByRole("button", { name: "Compare 2" }).click();
    await expect(page).toHaveURL(/\/compare\?ids=/);
    await expect(page.getByTestId("compare-table")).toBeVisible();
  });

  test("the AI narrative streams and is saved as a comparison report", async ({ page }) => {
    const a = (await (await page.request.get("/api/repos/by-name/chroma-core/chroma")).json()).id;
    const b = (await (await page.request.get("/api/repos/by-name/asg017/sqlite-vec")).json()).id;
    await page.goto(`/compare?ids=${a},${b}`);
    await expect(page.getByTestId("compare-table")).toBeVisible();
    await page.getByRole("button", { name: "Generate narrative" }).click();
    await expect(page.getByRole("button", { name: "Save report" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("region", { name: "AI narrative" }).getByRole("button", { name: /^Evidence E\d+$/ }).first()).toBeVisible();
  });
});
