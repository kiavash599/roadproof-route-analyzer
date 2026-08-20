import { access } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";

const projectRoot = process.env.SITES_PROJECT_ROOT || path.resolve(import.meta.dirname, "..");
const vinextCli = path.join(projectRoot, "node_modules", "vinext", "dist", "cli.js");
await access(vinextCli).catch(() => {
  throw new Error("vinext is unavailable. Run npm run install:ci and wait for it to finish before building.");
});
console.log("Running bounded vinext build...");
const bounded = spawnSync(
  process.execPath,
  [
    path.join(import.meta.dirname, "run-bounded.mjs"),
    process.env.SITES_BUILD_TIMEOUT || "3m",
    process.env.SITES_BUILD_KILL_AFTER || "10s",
    process.execPath,
    vinextCli,
    "build",
  ],
  { cwd: projectRoot, env: process.env, stdio: "inherit" },
);
if (bounded.status !== 0) process.exit(bounded.status ?? 1);
const validation = spawnSync(process.execPath, [path.join(import.meta.dirname, "validate-artifact.mjs")], {
  cwd: projectRoot,
  env: process.env,
  stdio: "inherit",
});
process.exit(validation.status ?? 1);
