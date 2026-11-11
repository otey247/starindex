import { expect, test } from "@playwright/test";
import { blur, rows } from "./helpers";

test.describe("keyboard shortcuts", () => {
  test("'/' focuses search from anywhere", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByRole("heading", { level: 1, name: "Library" })).toBeVisible();
    await blur(page);
    await page.keyboard.press("/");
    await expect(page).toHaveURL(/\/search/);
    await expect(page.getByRole("combobox", { name: "Search query" })).toBeFocused();
  });

  test("Ctrl+K opens the command palette, which finds repositories; Esc closes it", async ({ page }) => {
    await page.goto("/sync");
    await expect(page.getByRole("heading", { level: 1, name: "Sync" })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();
    await page.keyboard.type("chroma");
    await expect(palette.getByRole("option", { name: /chroma-core\/chroma/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();

    await page.keyboard.press("Control+k");
    await page.keyboard.type("chroma");
    await palette.getByRole("option", { name: /chroma-core\/chroma/ }).click();
    await expect(page).toHaveURL(/\/repo\/\d+/);
  });

  test("j / k / x / Esc work on result lists, g-sequences navigate, ? shows help", async ({ page }) => {
    await page.goto("/search?q=agent&mode=keyword");
    await expect(rows(page).first()).toBeVisible();
    await blur(page);
    await page.keyboard.press("j");
    await page.keyboard.press("j");
    await expect(rows(page).nth(1)).toHaveAttribute("data-focused", "true");
    await page.keyboard.press("k");
    await expect(rows(page).nth(0)).toHaveAttribute("data-focused", "true");
    await page.keyboard.press("x");
    await expect(page.getByRole("button", { name: /^Compare 1$/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("[data-result-row][data-focused]")).toHaveCount(0);

    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeHidden();

    await page.keyboard.press("g");
    await page.keyboard.press("l");
    await expect(page).toHaveURL(/\/library/);
  });

  test("Enter still activates a focused button (shortcuts never hijack it)", async ({ page }) => {
    await page.goto("/search?q=agent&mode=keyword");
    await expect(rows(page).first()).toBeVisible();
    await page.getByRole("button", { name: "Save this search" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Save this search" })).toBeVisible();
  });
});
