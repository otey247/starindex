import { expect, test } from "@playwright/test";

test.describe("sync and library", () => {
  test("Sync shows connection, indexing progress, jobs and schedules", async ({ page }) => {
    await page.goto("/sync");
    await expect(page.getByRole("region", { name: "GitHub connection" })).toContainText(/octo|Personal access token/);
    await expect(page.getByRole("region", { name: "Content indexing" })).toContainText("Indexed");
    await expect(page.getByRole("region", { name: "Jobs" }).getByRole("row").nth(1)).toBeVisible();
    await expect(page.getByRole("region", { name: "Schedules" })).toContainText("Check for new stars");

    // an incremental sync runs and is recorded
    await page.getByRole("button", { name: "Sync new stars" }).click();
    await expect(page.getByText("Checking for new stars.")).toBeVisible();
    await expect
      .poll(async () => (await (await page.request.get("/api/sync/runs?limit=1")).json())[0]?.kind, { timeout: 30_000 })
      .toBe("incremental");

    // schedules can be paused and resumed
    const toggle = page.getByRole("switch", { name: /Enable .*incremental/i }).first();
    const was = await toggle.getAttribute("aria-checked");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", was === "true" ? "false" : "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", was!);
  });

  test("Library lists every repository with sorting, filtering and bulk actions", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByText(/60 repositories starred/)).toBeVisible();
    await page.getByLabel("Quick filter").fill("airflow");
    await expect(page.locator("[data-result-row]")).toHaveCount(1);
    await page.getByLabel("Quick filter").fill("");
    await page.getByRole("button", { name: /^Stars$/ }).click(); // column header sort
    await expect(page).toHaveURL(/sort=stars/);

    // select two rows -> bulk bar -> compare
    const rows = page.locator("[data-result-row]");
    await rows.nth(0).locator("label").first().click();
    await rows.nth(1).locator("label").first().click();
    const bar = page.getByRole("toolbar", { name: "Bulk actions" });
    await expect(bar).toContainText("2 selected");
    await bar.getByRole("button", { name: "Tag" }).click();
    await page.getByRole("dialog").getByLabel("Tags").fill("e2e-bulk");
    await page.getByRole("dialog").getByRole("button", { name: "Add tags" }).click();
    await expect(page.getByText(/Tagged 2 repositories/)).toBeVisible();
    await bar.getByRole("button", { name: "Compare" }).click();
    await expect(page).toHaveURL(/\/compare\?ids=\d+,\d+/);
  });
});
