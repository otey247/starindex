import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { FRESH, FRESH_URL, MAIN, MAIN_URL, ROOT, STATE_FILE } from "./env";

interface Started {
  pid: number;
  dataDir: string;
  log: string;
}

function pythonPath(): string {
  if (process.env.STARINDEX_E2E_PYTHON) return process.env.STARINDEX_E2E_PYTHON;
  const candidates = [path.join(ROOT, ".venv", "bin", "python"), path.join(ROOT, ".venv", "Scripts", "python.exe")];
  const found = candidates.find((p) => fs.existsSync(p));
  if (!found) throw new Error(`No Python virtualenv found at ${ROOT}/.venv. Run the install script first, or set STARINDEX_E2E_PYTHON.`);
  return found;
}

function ensureFrontendBuilt(): void {
  const dist = path.join(ROOT, "frontend", "dist", "index.html");
  if (fs.existsSync(dist) && !process.env.STARINDEX_E2E_REBUILD) return;
  const cwd = path.join(ROOT, "frontend");
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  if (!fs.existsSync(path.join(cwd, "node_modules"))) {
    console.log("[e2e] installing frontend dependencies…");
    const install = spawnSync(npm, ["install", "--no-audit", "--no-fund"], { cwd, stdio: "inherit" });
    if (install.status !== 0) throw new Error("npm install failed in frontend/");
  }
  console.log("[e2e] building the frontend…");
  const build = spawnSync(npm, ["run", "build"], { cwd, stdio: "inherit" });
  if (build.status !== 0) throw new Error("frontend build failed");
}

function start(name: string, cfg: typeof MAIN, extra: string[]): Started {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), `starindex-e2e-${name}-`));
  const log = path.join(dataDir, "instance.log");
  const out = fs.openSync(log, "a");
  const args = [
    path.join(ROOT, "scripts", "demo_instance.py"),
    "--data-dir", path.join(dataDir, "data"),
    "--port", String(cfg.port),
    "--github-port", String(cfg.githubPort),
    "--llm-port", String(cfg.llmPort),
    "--embedding", "hashing",
    "--limit", String(cfg.limit),
    ...extra,
  ];
  const child = spawn(pythonPath(), args, { cwd: ROOT, stdio: ["ignore", out, out], detached: true, env: { ...process.env, PYTHONUNBUFFERED: "1" } });
  child.unref();
  if (!child.pid) throw new Error(`could not start the ${name} StarIndex instance`);
  return { pid: child.pid, dataDir, log };
}

async function waitFor(what: string, check: () => Promise<boolean>, timeoutMs: number, started?: Started): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch (err) {
      lastError = String((err as Error).message ?? err);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  const tail = started && fs.existsSync(started.log) ? fs.readFileSync(started.log, "utf8").split("\n").slice(-25).join("\n") : "";
  throw new Error(`[e2e] timed out waiting for ${what}. ${lastError}\n${tail}\nIf the corpus is missing, run: python eval/build_corpus.py`);
}

const json = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
};

export default async function globalSetup(): Promise<void> {
  ensureFrontendBuilt();
  const state: { main?: Started; fresh?: Started } = {};
  if (!process.env.STARINDEX_E2E_MAIN_URL) state.main = start("main", MAIN, ["--connect", "--sync", "--with-llm"]);
  if (!process.env.STARINDEX_E2E_FRESH_URL) state.fresh = start("fresh", FRESH, []);
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));

  await waitFor("the main instance to answer", async () => (await json(`${MAIN_URL}/api/health`)).status === "ok", 90_000, state.main);
  await waitFor("the first-run instance to answer", async () => (await json(`${FRESH_URL}/api/health`)).status === "ok", 90_000, state.fresh);
  // The main instance must be fully synced, indexed and embedded before tests run.
  await waitFor(
    "the main instance to finish syncing and indexing",
    async () => {
      const s = await json(`${MAIN_URL}/api/sync/status`);
      const idx = s.index;
      return !s.current_run && idx.total > 0 && idx.indexed + idx.skipped + idx.failed === idx.total && idx.chunks > 0 && idx.chunks_embedded === idx.chunks && s.queue.running === 0 && s.queue.queued === 0;
    },
    180_000,
    state.main,
  );
  console.log(`[e2e] instances ready: main ${MAIN_URL}, first-run ${FRESH_URL}`);
}
