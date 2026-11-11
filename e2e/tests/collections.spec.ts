import { expect, test } from "@playwright/test";
import { repoId, RUN } from "./helpers";

test.describe("collections", () => {
  test("create a collection, add repositories, remove one", async ({ page }) => {
    await page.goto("/collections");
    await page.getByRole("button", { name: "New collection" }).first().click();
    const dialog = page.getByRole("dialog", { name: "New collection" });
    await dialog.getByLabel("Name").fill(`E2E search stack ${RUN}`);
    await dialog.getByLabel("Description").fill("Created by the end-to-end suite.");
    await dialog.getByRole("button", { name: "Create collection" }).click();
    await page.getByRole("link", { name: new RegExp(`E2E search stack ${RUN}`) }).click();
    await expect(page.getByRole("heading", { level: 1, name: `E2E search stack ${RUN}` })).toBeVisible();

    const picker = page.getByRole("combobox", { name: /Add a repository to this collection/ });
    for (const [query, name] of [["chroma", "chroma-core/chroma"], ["haystack", "deepset-ai/haystack"]] as const) {
      await picker.fill(query);
      await page.getByRole("option", { name: new RegExp(name) }).click();
      await expect(page.locator("[data-result-row]").filter({ hasText: name.split("/")[1]! })).toBeVisible();
    }
    await expect(page.locator("[data-result-row]")).toHaveCount(2);
    await page.getByRole("button", { name: "Remove deepset-ai/haystack from this collection" }).click();
    await expect(page.locator("[data-result-row]")).toHaveCount(1);

    // membership shows on the repository page too
    const id = await repoId(page, "chroma-core/chroma");
    await page.goto(`/repo/${id}`);
    await expect(page.getByRole("complementary", { name: "Repository details" }).getByRole("link", { name: `E2E search stack ${RUN}` })).toBeVisible();
  });

  test("a repository can be added from its page", async ({ page }) => {
    const id = await repoId(page, "BerriAI/litellm");
    await page.goto(`/repo/${id}`);
    await page.getByRole("button", { name: "Add to collection" }).click();
    await page.getByRole("menuitem", { name: "New collection…" }).click();
    const dialog = page.getByRole("dialog", { name: "New collection" });
    await dialog.getByLabel("Name").fill(`E2E gateways ${RUN}`);
    await dialog.getByRole("button", { name: "Create collection" }).click();
    await expect(page.getByRole("complementary", { name: "Repository details" }).getByRole("link", { name: `E2E gateways ${RUN}` })).toBeVisible();
  });

  test("a smart collection follows its search", async ({ page }) => {
    await page.goto("/collections");
    await page.getByRole("button", { name: "Smart collection" }).click();
    const dialog = page.getByRole("dialog", { name: "New smart collection" });
    await dialog.getByLabel("Name").fill(`E2E Rust projects ${RUN}`);
    await dialog.getByLabel("Query").fill("lang:rust");
    await dialog.getByRole("radio", { name: "Keyword" }).click();
    await dialog.getByRole("button", { name: "Create smart collection" }).click();
    await expect(page).toHaveURL(/\/collections\/\d+/);
    await expect(page.getByText("Smart collection", { exact: true })).toBeVisible();
    const rows = page.locator("[data-result-row]");
    await expect(rows.first()).toBeVisible();
    for (const row of await rows.all()) await expect(row).toContainText("Rust");
    // smart collections have no manual add/remove
    await expect(page.getByRole("combobox", { name: /Add a repository to this collection/ })).toHaveCount(0);
  });

  test("tags can be renamed and deleted", async ({ page }) => {
    const id = await repoId(page, "apache/airflow");
    await page.request.put(`/api/repos/${id}/tags`, { data: { tags: [`e2e-old-${RUN}`] } });
    await page.goto("/collections?tab=tags");
    const row = page.getByTestId(`tag-e2e-old-${RUN}`);
    await row.getByRole("button", { name: `Rename e2e-old-${RUN}` }).click();
    await row.getByLabel(`New name for e2e-old-${RUN}`).fill(`e2e-new-${RUN}`);
    await row.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(page.getByTestId(`tag-e2e-new-${RUN}`)).toBeVisible();
    await page.getByTestId(`tag-e2e-new-${RUN}`).getByRole("button", { name: `Delete e2e-new-${RUN}` }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete tag" }).click();
    await expect(page.getByTestId(`tag-e2e-new-${RUN}`)).toHaveCount(0);
  });

  test("categories are listed and editable", async ({ page }) => {
    await page.goto("/collections?tab=categories");
    await expect(page.getByRole("link", { name: "AI agents and orchestration" })).toBeVisible();
    await page.getByRole("button", { name: "New category" }).click();
    const dialog = page.getByRole("dialog", { name: "New category" });
    await dialog.getByLabel("Name").fill(`E2E category ${RUN}`);
    await dialog.getByLabel("Keywords").fill("e2e, testing");
    await dialog.getByRole("button", { name: "Create category" }).click();
    await expect(page.getByRole("link", { name: `E2E category ${RUN}` })).toBeVisible();
  });
});
