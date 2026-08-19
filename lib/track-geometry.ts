import { sha256Hex } from "./route-intelligence.ts";

export type TrackPoint = { lat: number; lon: number };
export type ImportedTrack = {
  format: "GPX" | "GeoJSON" | "KML";
  points: TrackPoint[];
  distanceKm: number;
  geometryHash: string;
};

function checkedPoint(latValue: unknown, lonValue: unknown): TrackPoint {
  const lat = Number(latValue);
  const lon = Number(lonValue);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    throw new Error("The track contains an invalid latitude or longitude.");
  }
  return { lat, lon };
}

function pointsFromGeoJson(value: unknown): TrackPoint[] {
  if (!value || typeof value !== "object") return [];
  const item = value as Record<string, unknown>;
  if (item.type === "Feature") return pointsFromGeoJson(item.geometry);
  if (item.type === "FeatureCollection" && Array.isArray(item.features)) return item.features.flatMap(pointsFromGeoJson);
  if (item.type === "GeometryCollection" && Array.isArray(item.geometries)) return item.geometries.flatMap(pointsFromGeoJson);
  if (item.type === "LineString" && Array.isArray(item.coordinates)) {
    return item.coordinates.map((coordinate) => {
      if (!Array.isArray(coordinate) || coordinate.length < 2) throw new Error("A GeoJSON coordinate is incomplete.");
      return checkedPoint(coordinate[1], coordinate[0]);
    });
  }
  if (item.type === "MultiLineString" && Array.isArray(item.coordinates)) {
    return item.coordinates.flatMap((line) => pointsFromGeoJson({ type: "LineString", coordinates: line }));
  }
  return [];
}

function pointsFromGpx(text: string): TrackPoint[] {
  const points: TrackPoint[] = [];
  const tagPattern = /<(?:[\w-]+:)?(?:trkpt|rtept)\b([^>]*)>/gi;
  for (const match of text.matchAll(tagPattern)) {
    const attrs = match[1];
    const lat = attrs.match(/\blat\s*=\s*["']([^"']+)["']/i)?.[1];
    const lon = attrs.match(/\blon\s*=\s*["']([^"']+)["']/i)?.[1];
    if (lat === undefined || lon === undefined) throw new Error("A GPX track point is missing latitude or longitude.");
    points.push(checkedPoint(lat, lon));
  }
  return points;
}

function pointsFromKml(text: string): TrackPoint[] {
  const points: TrackPoint[] = [];
  const blocks = text.matchAll(/<(?:[\w-]+:)?coordinates\b[^>]*>([\s\S]*?)<\/(?:[\w-]+:)?coordinates>/gi);
  for (const block of blocks) {
    for (const tuple of block[1].trim().split(/\s+/)) {
      if (!tuple) continue;
      const [lon, lat] = tuple.split(",");
      if (lat === undefined) throw new Error("A KML coordinate is incomplete.");
      points.push(checkedPoint(lat, lon));
    }
  }
  return points;
}

function radians(value: number): number { return value * Math.PI / 180; }

export function trackDistanceKm(points: TrackPoint[]): number {
  let metres = 0;
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const dLat = radians(current.lat - previous.lat);
    const dLon = radians(current.lon - previous.lon);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(radians(previous.lat)) * Math.cos(radians(current.lat)) * Math.sin(dLon / 2) ** 2;
    metres += 6_371_008.8 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }
  return metres / 1000;
}

export async function importTrackFile(name: string, text: string): Promise<ImportedTrack> {
  if (text.length > 5_000_000) throw new Error("Track files are limited to 5 MB.");
  const lowerName = name.toLowerCase();
  let format: ImportedTrack["format"];
  let points: TrackPoint[];

  if (lowerName.endsWith(".gpx") || /<\s*gpx[\s>]/i.test(text)) {
    format = "GPX";
    points = pointsFromGpx(text);
  } else if (lowerName.endsWith(".kml") || /<\s*kml[\s>]/i.test(text)) {
    format = "KML";
    points = pointsFromKml(text);
  } else {
    format = "GeoJSON";
    let parsed: unknown;
    try { parsed = JSON.parse(text); }
    catch { throw new Error("The file is not valid GPX, KML, or GeoJSON."); }
    points = pointsFromGeoJson(parsed);
  }

  if (points.length < 2) throw new Error("The file must contain at least two ordered track points.");
  const canonical = points.map((point) => `${point.lat.toFixed(7)},${point.lon.toFixed(7)}`).join("\n");
  return {
    format,
    points,
    distanceKm: trackDistanceKm(points),
    geometryHash: await sha256Hex(`roadproof-track-geometry-v1\n${canonical}`),
  };
}
