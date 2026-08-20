import { access, readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import path from "node:path";
import process from "node:process";

const projectRoot = process.env.SITES_PROJECT_ROOT || path.resolve(import.meta.dirname, "..");
const workerPath = path.join(projectRoot, "dist", "server", "index.js");
const hostingPath = path.join(projectRoot, "dist", ".openai", "hosting.json");
await access(workerPath).catch(() => { throw new Error("Missing Sites Worker entry: dist/server/index.js"); });
await access(hostingPath).catch(() => { throw new Error("Missing packaged Sites manifest: dist/.openai/hosting.json"); });
JSON.parse(await readFile(hostingPath, "utf8"));
const workerUrl = pathToFileURL(workerPath);
workerUrl.searchParams.set("sites-validation", `${process.pid}-${Date.now()}`);
const worker = await import(workerUrl.href);
if (!worker.default || typeof worker.default.fetch !== "function") {
  throw new Error("dist/server/index.js must have an ESM default export with fetch(request, env, ctx)");
}
console.log("Validated Sites artifact: ESM Worker default.fetch and hosting manifest are present.");
