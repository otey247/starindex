import { expect, test } from "@playwright/test";
import { repoId, watchForErrors } from "./helpers";

test.describe("repository detail", () => {
  test("shows identity, README, files, dependencies and related projects", async ({ page }) => {
    const problems = watchForErrors(page);
    const id = await repoId(page, "chroma-core/chroma");
    await page.goto(`/repo/${id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("chroma");
    await expect(page.getByRole("link", { name: "GitHub", exact: true })).toHaveAttribute("href", "https://github.com/chroma-core/chroma");
    await expect(page.getByRole("region", { name: "README" })).toContainText(/Chroma/i);
    // README is rendered as sanitised markdown: no remote images are requested
    expect(await page.getByRole("region", { name: "README" }).locator("img").count()).toBe(0);

    // indexed files with line numbers and a GitHub permalink
    await page.getByRole("tab", { name: /Indexed files/ }).click();
    await expect(page.getByRole("navigation", { name: "Indexed files" }).getByRole("button").first()).toBeVisible();
    await expect(page.locator("tr[data-line='1']")).toBeVisible();
    await expect(page.getByRole("link", { name: /^GitHub/ }).last()).toHaveAttribute("href", /github\.com\/chroma-core\/chroma\/blob\/[0-9a-f]{7,40}\//);

    // dependencies read from manifests
    await page.getByRole("tab", { name: "Dependencies" }).click();
    await expect(page.getByRole("columnheader", { name: "Package" }).first()).toBeVisible();

    // related projects with reasons
    await page.getByRole("tab", { name: "Related" }).click();
    await expect(page.getByText(/Similar content|Shared topics|Same category/).first()).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("a line anchor in the files view highlights that line", async ({ page }) => {
    const id = await repoId(page, "chroma-core/chroma");
    const docs = await (await page.request.get(`/api/repos/${id}/documents`)).json();
    const readme = docs.find((d: { kind: string }) => d.kind === "readme");
    await page.goto(`/repo/${id}?tab=files&doc=${readme.id}&line=5`);
    await expect(page.locator("tr[data-line='5']")).toBeVisible();
    await expect(page.locator("tr[data-line='5'] td").first()).toHaveClass(/text-accent/);
  });

  test("notes, tags, favorite and collections are personal and persist", async ({ page }) => {
    const id = await repoId(page, "duckdb/duckdb");
    await page.goto(`/repo/${id}`);
    await page.getByLabel("Personal notes").fill("Use for the analytics side of the search platform.");
    await expect(page.getByText("Saved", { exact: true })).toBeVisible();
    await page.getByLabel("Add tag").fill("e2e-tag");
    await page.getByLabel("Add tag").press("Enter");
    await expect(page.getByText("e2e-tag", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Mark as favorite" }).click();
    await expect(page.getByRole("button", { name: "Remove from favorites" })).toBeVisible();

    await page.reload();
    await expect(page.getByLabel("Personal notes")).toHaveValue("Use for the analytics side of the search platform.");
    await expect(page.getByText("e2e-tag", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove from favorites" })).toBeVisible();

    // the library can be filtered by the new tag
    await page.goto("/library?tag=e2e-tag");
    await expect(page.locator("[data-result-row]")).toHaveCount(1);
    await expect(page.locator("[data-result-row]").first()).toContainText("duckdb");
  });

  test("changing the index level queues a job that appears in Sync", async ({ page }) => {
    const id = await repoId(page, "dimforge/rapier");
    await page.goto(`/repo/${id}`);
    await page.getByLabel("Index level").selectOption("deep");
    await page.getByRole("complementary", { name: "Repository details" }).getByRole("button", { name: "Index", exact: true }).click();
    await expect(page.getByText(/Queued deep indexing for rapier/)).toBeVisible();
    await page.goto("/sync");
    const jobs = page.getByRole("region", { name: "Jobs" });
    await expect(jobs.getByText("dimforge/rapier").first()).toBeVisible();
    // the job finishes and the repository reports its new level
    await expect
      .poll(async () => (await (await page.request.get(`/api/repos/${id}`)).json()).indexed_level, { timeout: 45_000 })
      .toBe("deep");
  });

  test("usage ideas are generated from evidence and cached", async ({ page }) => {
    const id = await repoId(page, "crewAIInc/crewAI");
    await page.goto(`/repo/${id}?tab=ideas`);
    await page.getByRole("button", { name: "Generate ideas" }).click();
    await expect(page.getByText(/Generated with/)).toBeVisible({ timeout: 30_000 });
    await page.reload();
    await expect(page.getByText(/Generated with/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible();
  });
});
