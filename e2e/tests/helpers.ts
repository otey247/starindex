import { expect, type Locator, type Page } from "@playwright/test";

/** Page errors and CSP violations fail the test that caused them. */
export function watchForErrors(page: Page): string[] {
  const problems: string[] = [];
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy|Refused to/i.test(msg.text())) problems.push(`csp: ${msg.text().slice(0, 200)}`);
  });
  return problems;
}

export async function api<T = unknown>(page: Page, path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await page.request.fetch(`/api${path}`, {
    method: init?.method ?? "GET",
    data: init?.body,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  expect(res.ok(), `${init?.method ?? "GET"} /api${path} -> ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function repoId(page: Page, fullName: string): Promise<number> {
  const repo = await api<{ id: number }>(page, `/repos/by-name/${fullName}`);
  return repo.id;
}

/** Run a search from the Search page's query bar and wait for results to settle. */
export async function search(page: Page, query: string): Promise<void> {
  const bar = page.getByRole("combobox", { name: "Search query" });
  await bar.fill(query);
  await bar.press("Enter");
  await expect(page.getByTestId("result-summary")).toBeVisible();
}

export const rows = (page: Page) => page.locator("[data-result-row]");

/** Move keyboard focus out of any field so single-key shortcuts apply. */
export async function blur(page: Page): Promise<void> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
}

export async function noHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const main = document.querySelector("main");
    return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });
  expect(overflow, "page must not scroll sideways").toBeLessThanOrEqual(1);
}

/**
 * Tick one of the app's checkboxes. The native input is visually hidden (the visible box is
 * a styled sibling), so interact through its label like a user would.
 */
export async function tick(scope: Locator | Page, name: string | RegExp): Promise<void> {
  const page = "page" in scope ? scope.page() : scope;
  await scope.locator("label").filter({ has: page.getByRole("checkbox", { name }) }).first().click();
}

/** Unique per run, so the suite can also be pointed at an instance that already has data. */
export const RUN = Date.now().toString(36);
