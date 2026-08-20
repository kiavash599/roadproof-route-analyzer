import { spawn } from "node:child_process";

const python = process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python";
const api = spawn(python, ["-m", "roadproof.webapi"], { stdio: "inherit" });
const vite = spawn(process.execPath, ["scripts/sites-env.mjs", "--", process.execPath, "node_modules/vite/bin/vite.js"], { stdio: "inherit" });

let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  api.kill();
  vite.kill();
  setTimeout(() => process.exit(code), 200);
}

api.on("exit", (code) => { if (!stopping) stop(code ?? 1); });
vite.on("exit", (code) => { if (!stopping) stop(code ?? 1); });
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
