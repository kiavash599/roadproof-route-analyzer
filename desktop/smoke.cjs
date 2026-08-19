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

    const routeResponse = await fetch(`${localServer.origin}/api/resolve-route`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        url: "https://www.google.com/maps/dir/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/55.0012777,11.9878823/55.3824597,11.3319055/55.6248884,12.0624072/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/data=!3e0?skid=desktop-smoke",
      }),
    });
    const route = await routeResponse.json();
    assert.equal(routeResponse.status, 200);
    assert.equal(route.route.matchesStoredPilotRequest, true);
    assert.equal(route.evidence.exactSelectedGeometry, "unresolved");
    console.log(`Desktop smoke test passed at ${localServer.origin}`);
  } finally {
    await localServer.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
