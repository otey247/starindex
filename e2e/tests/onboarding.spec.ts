import { expect, test } from "@playwright/test";
import { GITHUB_LOGIN, GITHUB_TOKEN } from "../env";
import { rows, watchForErrors } from "./helpers";

test("first run: connect GitHub, import every star, land in Search", async ({ page }) => {
  const problems = watchForErrors(page);

  await page.goto("/");
  await expect(page).toHaveURL(/\/welcome/);
  await expect(page.getByRole("heading", { name: "Turn your stars into a searchable index." })).toBeVisible();

  // 1. connect. An implausible token is rejected with a clear message and nothing is stored…
  await page.getByRole("radio", { name: /Personal access token/ }).click();
  await page.getByLabel("Token", { exact: true }).fill("short");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(/does not look like a GitHub access token/);

  // …then connect for real with the fake GitHub's access token (stored server-side, never shown again).
  await page.getByLabel("Token", { exact: true }).fill(GITHUB_TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText(new RegExp(`Connected as ${GITHUB_LOGIN}`)).first()).toBeVisible();
  const auth = await (await page.request.get("/api/github/auth")).json();
  expect(auth.method).toBe("token");
  expect(auth.ok).toBe(true);
  expect(JSON.stringify(auth)).not.toContain(GITHUB_TOKEN);

  // 2. first full sync with live progress
  await page.getByRole("button", { name: "Start the first sync" }).click();
  await expect(page.getByText(/Discovering starred repositories|All starred repositories imported/)).toBeVisible();
  await expect(page.getByText("All starred repositories imported")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Indexing documentation")).toBeVisible();

  // 3. search is usable straight away
  await page.getByRole("button", { name: "Open Search" }).click();
  await expect(page).toHaveURL(/\/search/);
  const bar = page.getByRole("combobox", { name: "Search query" });
  await bar.fill("mcp");
  await page.getByRole("radio", { name: "Keyword" }).click();
  await bar.press("Enter");
  await expect(rows(page).first()).toBeVisible();

  // every star was imported, not just the first page
  const status = await (await page.request.get("/api/sync/status")).json();
  expect(status.index.total).toBe(40);
  expect(status.last_full?.status).toBe("succeeded");

  // onboarding does not come back
  await page.goto("/");
  await expect(page).toHaveURL(/\/search/);
  expect(problems).toEqual([]);
});
