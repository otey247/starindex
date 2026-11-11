/**
 * Shared configuration for the e2e suite.
 *
 * Two isolated StarIndex instances are started by global-setup (see scripts/demo_instance.py):
 *   MAIN   connected to the fake GitHub, fully synced, fake LLM configured — most tests
 *   FRESH  untouched data directory — the first-run / onboarding test
 * Both use hashing embeddings and a trimmed corpus so they are ready in seconds and deterministic.
 * Set STARINDEX_E2E_MAIN_URL / STARINDEX_E2E_FRESH_URL to reuse instances you started yourself.
 */
import path from "node:path";

const num = (name: string, fallback: number) => Number(process.env[name] ?? fallback);

export const ROOT = path.resolve(__dirname, "..");
export const STATE_FILE = path.join(__dirname, ".e2e-state.json");

export const MAIN = {
  port: num("STARINDEX_E2E_PORT", 8785),
  githubPort: num("STARINDEX_E2E_GITHUB_PORT", 8786),
  llmPort: num("STARINDEX_E2E_LLM_PORT", 8787),
  limit: 60,
};
export const FRESH = {
  port: num("STARINDEX_E2E_FRESH_PORT", 8795),
  githubPort: num("STARINDEX_E2E_FRESH_GITHUB_PORT", 8796),
  llmPort: num("STARINDEX_E2E_FRESH_LLM_PORT", 8797),
  limit: 40,
};

export const MAIN_URL = process.env.STARINDEX_E2E_MAIN_URL ?? `http://127.0.0.1:${MAIN.port}`;
export const FRESH_URL = process.env.STARINDEX_E2E_FRESH_URL ?? `http://127.0.0.1:${FRESH.port}`;
/** The scripted OpenAI-compatible model server that belongs to the MAIN instance. */
export const LLM_URL = process.env.STARINDEX_E2E_LLM_URL ?? `http://127.0.0.1:${MAIN.llmPort}/v1`;
export const GITHUB_TOKEN = "demo-token";
/** Login of the account the fake GitHub serves stars for. */
export const GITHUB_LOGIN = "octo";
