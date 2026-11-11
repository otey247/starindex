import fs from "node:fs";
import { STATE_FILE } from "./env";

export default async function globalTeardown(): Promise<void> {
  if (!fs.existsSync(STATE_FILE)) return;
  const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) as Record<string, { pid: number; dataDir: string } | undefined>;
  for (const inst of Object.values(state)) {
    if (!inst) continue;
    try {
      // The instance was started detached, so signal its whole process group.
      process.kill(process.platform === "win32" ? inst.pid : -inst.pid, "SIGTERM");
    } catch {
      /* already gone */
    }
  }
  await new Promise((r) => setTimeout(r, 800));
  for (const inst of Object.values(state)) {
    if (!inst) continue;
    try {
      process.kill(process.platform === "win32" ? inst.pid : -inst.pid, "SIGKILL");
    } catch {
      /* already gone */
    }
    if (!process.env.STARINDEX_E2E_KEEP_DATA) fs.rmSync(inst.dataDir, { recursive: true, force: true });
  }
  fs.rmSync(STATE_FILE, { force: true });
}
