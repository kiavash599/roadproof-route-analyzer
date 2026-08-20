import { mkdirSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";

const separator = process.argv.indexOf("--");
const args = separator >= 0 ? process.argv.slice(separator + 1) : process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/sites-env.mjs -- command [args...]");
  process.exit(64);
}

const projectRoot = path.resolve(import.meta.dirname, "..");
const runtimeRoot = process.env.SITES_RUNTIME_ROOT || path.join(projectRoot, ".sites-runtime");
const directories = {
  HOME: path.join(runtimeRoot, "home"),
  XDG_CONFIG_HOME: path.join(runtimeRoot, "xdg-config"),
  TMPDIR: path.join(runtimeRoot, "tmp"),
  npm_config_cache: path.join(runtimeRoot, "npm-cache"),
  WRANGLER_LOG_PATH: path.join(runtimeRoot, "wrangler", "logs"),
  MINIFLARE_REGISTRY_PATH: path.join(runtimeRoot, "wrangler", "registry"),
};
for (const directory of Object.values(directories)) mkdirSync(directory, { recursive: true });

const env = {
  ...process.env,
  ...directories,
  SITES_ENV_READY: "1",
  SITES_PROJECT_ROOT: projectRoot,
  WRANGLER_WRITE_LOGS: "false",
  npm_config_audit: "false",
  npm_config_fund: "false",
  npm_config_update_notifier: "false",
};
for (const name of [
  "npm_config_proxy", "npm_config_http_proxy", "npm_config_https_proxy",
  "NPM_CONFIG_PROXY", "NPM_CONFIG_HTTP_PROXY", "NPM_CONFIG_HTTPS_PROXY",
]) delete env[name];

const child = spawn(args[0], args.slice(1), {
  cwd: projectRoot,
  env,
  stdio: "inherit",
  shell: false,
});
child.on("error", (error) => {
  console.error(error.message);
  process.exit(69);
});
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
