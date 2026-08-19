const assert = require("node:assert/strict");
const path = require("node:path");
const { startLocalServer } = require("./server.cjs");

(async () => {
  const localServer = await startLocalServer(path.resolve(__dirname, ".."));
  try {
    const response = await fetch(localServer.origin, {
      headers: { accept: "text/html" },
    });
    const html = await response.text();
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type") || "", /^text\/html\b/i);
    assert.match(html, /RoadProof/);
    console.log(`Desktop smoke test passed at ${localServer.origin}`);
  } finally {
    await localServer.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
