const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { Readable } = require("node:stream");
const { pathToFileURL } = require("node:url");

const MIME_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml; charset=utf-8"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function contentType(filePath) {
  return MIME_TYPES.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
}

function safeAssetPath(clientRoot, requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl).pathname);
  const relativePath = pathname.replace(/^\/+/, "");
  if (!relativePath) return null;
  const candidate = path.resolve(clientRoot, relativePath);
  const rootWithSeparator = `${path.resolve(clientRoot)}${path.sep}`;
  return candidate.startsWith(rootWithSeparator) ? candidate : null;
}

function createAssetFetcher(clientRoot) {
  return async function fetchAsset(input) {
    const request = input instanceof Request ? input : new Request(input);
    const filePath = safeAssetPath(clientRoot, request.url);
    if (!filePath) return new Response("Not found", { status: 404 });

    try {
      const stat = await fs.stat(filePath);
      if (!stat.isFile()) return new Response("Not found", { status: 404 });
      return new Response(await fs.readFile(filePath), {
        status: 200,
        headers: {
          "cache-control": "public, max-age=31536000, immutable",
          "content-length": String(stat.size),
          "content-type": contentType(filePath),
        },
      });
    } catch (error) {
      if (error && error.code === "ENOENT") return new Response("Not found", { status: 404 });
      throw error;
    }
  };
}

async function requestBody(request) {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return Buffer.concat(chunks);
}

async function sendWebResponse(nodeResponse, webResponse) {
  nodeResponse.statusCode = webResponse.status;
  webResponse.headers.forEach((value, name) => nodeResponse.setHeader(name, value));
  if (!webResponse.body) {
    nodeResponse.end();
    return;
  }
  Readable.fromWeb(webResponse.body).pipe(nodeResponse);
}

async function startLocalServer(appRoot) {
  const clientRoot = path.join(appRoot, "dist", "client");
  const workerPath = path.join(appRoot, "dist", "server", "index.js");
  const workerUrl = pathToFileURL(workerPath);
  workerUrl.searchParams.set("desktop", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  if (!worker || typeof worker.fetch !== "function") {
    throw new Error("The packaged RoadProof worker has no fetch handler.");
  }

  const fetchAsset = createAssetFetcher(clientRoot);
  const server = http.createServer(async (nodeRequest, nodeResponse) => {
    try {
      const origin = `http://127.0.0.1:${server.address().port}`;
      const requestUrl = new URL(nodeRequest.url || "/", origin);
      const directAsset = await fetchAsset(requestUrl);
      if (directAsset.status !== 404) {
        await sendWebResponse(nodeResponse, directAsset);
        return;
      }

      const body = await requestBody(nodeRequest);
      const webRequest = new Request(requestUrl, {
        method: nodeRequest.method,
        headers: nodeRequest.headers,
        body,
        ...(body ? { duplex: "half" } : {}),
      });
      const pendingTasks = [];
      const response = await worker.fetch(
        webRequest,
        { ASSETS: { fetch: fetchAsset } },
        {
          waitUntil(promise) { pendingTasks.push(Promise.resolve(promise)); },
          passThroughOnException() {},
        },
      );
      await sendWebResponse(nodeResponse, response);
      void Promise.allSettled(pendingTasks);
    } catch (error) {
      console.error("RoadProof local server error", error);
      nodeResponse.statusCode = 500;
      nodeResponse.setHeader("content-type", "text/plain; charset=utf-8");
      nodeResponse.end("RoadProof could not render this page.");
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  return {
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

module.exports = { startLocalServer };
