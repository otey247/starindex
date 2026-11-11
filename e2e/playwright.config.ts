import fs from "node:fs";
import { chromium, defineConfig } from "@playwright/test";
import { FRESH_URL, MAIN_URL } from "./env";

// Use the browser Playwright expects; fall back to a system Chromium when that lookup fails.
function executablePath(): string | undefined {
  if (process.env.STARINDEX_E2E_CHROMIUM) return process.env.STARINDEX_E2E_CHROMIUM;
  try {
    if (fs.existsSync(chromium.executablePath())) return undefined;
  } catch {
    /* fall through */
  }
  const fallback = "/opt/pw-browsers/chromium";
  return fs.existsSync(fallback) ? fallback : undefined;
}

const desktop = { viewport: { width: 1440, height: 900 } };

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  globalTeardown: "./global-teardown.ts",
  // Tests share one backend per project, so they run serially for determinism.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    trace: "off",
    video: "off",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    launchOptions: { executablePath: executablePath() },
  },
  projects: [
    { name: "first-run", testMatch: /onboarding\.spec\.ts/, use: { ...desktop, baseURL: FRESH_URL } },
    { name: "desktop", testIgnore: [/onboarding\.spec\.ts/, /mobile\.spec\.ts/], use: { ...desktop, baseURL: MAIN_URL } },
    { name: "mobile", testMatch: /mobile\.spec\.ts/, use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, baseURL: MAIN_URL } },
  ],
});
