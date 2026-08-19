import assert from "node:assert/strict";
import test from "node:test";
import { parseGoogleRouteUrl } from "../lib/route-intelligence.ts";
import { importTrackFile } from "../lib/track-geometry.ts";

const firstSelection = "https://www.google.com/maps/dir/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/55.0012777,11.9878823/55.3824597,11.3319055/55.6248884,12.0624072/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/@55.297674,11.829347,85339m/data=!3m1!1e3!4m17!4m16!3e0?skid=6c588494-613c-4a60-9034-240edfe85349";
const secondSelection = "https://www.google.com/maps/dir/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/55.0012777,11.9878823/55.3824597,11.3319055/55.6248884,12.0624072/Arne+Jacobsens+All%C3%A9+2,+2300+K%C3%B8benhavn/@55.5344492,11.1238313,191754m/data=!3m1!1e3!4m17!4m16!3e0?skid=5f117654-6066-4ebe-bb45-db99db4fd5a9";

test("same ordered stops produce one request fingerprint without claiming one selection", async () => {
  const first = await parseGoogleRouteUrl("https://maps.app.goo.gl/first", firstSelection);
  const second = await parseGoogleRouteUrl("https://maps.app.goo.gl/second", secondSelection);

  assert.equal(first.requestFingerprint, second.requestFingerprint);
  assert.notEqual(first.opaqueSelectionId, second.opaqueSelectionId);
  assert.equal(first.matchesStoredPilotRequest, true);
  assert.equal(second.matchesStoredPilotRequest, true);
  assert.equal(first.waypoints.length, 3);
  assert.equal(first.travelMode, "driving");
});

test("equivalent GPX and GeoJSON tracks produce one geometry hash", async () => {
  const gpx = await importTrackFile("route.gpx", `<?xml version="1.0"?><gpx><trk><trkseg><trkpt lat="55.0" lon="11.0"/><trkpt lat="55.1" lon="11.2"/></trkseg></trk></gpx>`);
  const geojson = await importTrackFile("route.geojson", JSON.stringify({ type: "LineString", coordinates: [[11, 55], [11.2, 55.1]] }));

  assert.equal(gpx.geometryHash, geojson.geometryHash);
  assert.equal(gpx.points.length, 2);
  assert.ok(gpx.distanceKm > 0);
});

test("invalid track coordinates are rejected", async () => {
  await assert.rejects(
    importTrackFile("bad.geojson", JSON.stringify({ type: "LineString", coordinates: [[11, 95], [12, 55]] })),
    /invalid latitude or longitude/i,
  );
});
