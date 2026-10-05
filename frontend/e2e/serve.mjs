// Build a fresh demo data root (scripts/demo_data.py) and run the real server on it.
// Started by playwright.config.ts (webServer); stops when Playwright kills it.
import { spawn, spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const python = process.env.PYTHON ?? "python3";
const root = process.env.E2E_DATA_ROOT;
const port = process.env.E2E_PORT ?? "8799";
if (!root) throw new Error("E2E_DATA_ROOT is not set");

const built = spawnSync(python, [join(repo, "scripts", "demo_data.py"), root, "--force", "--quiet"], {
  cwd: repo,
  encoding: "utf8",
});
if (built.status !== 0) {
  process.stderr.write(built.stdout + built.stderr);
  process.exit(built.status ?? 1);
}
const env = { ...process.env };
for (const line of built.stdout.split("\n")) {
  const m = /^export (\w+)=(.*)$/.exec(line.trim());
  if (m) env[m[1]] = m[2];
}
const server = spawn(python, ["-m", "cover5", "serve", "--port", port, "--no-scheduler"], { cwd: repo, env, stdio: "inherit" });
const stop = () => server.kill("SIGTERM");
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
server.on("exit", (code) => process.exit(code ?? 0));
