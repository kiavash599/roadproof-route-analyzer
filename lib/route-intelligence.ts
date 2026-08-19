export type RouteStop = {
  raw: string;
  normalized: string;
  kind: "coordinate" | "place";
};

export type RouteRequestEvidence = {
  sourceUrl: string;
  resolvedUrl: string;
  origin: RouteStop;
  destination: RouteStop;
  waypoints: RouteStop[];
  travelMode: "driving" | "walking" | "bicycling" | "transit" | "unknown";
  opaqueSelectionId: string | null;
  requestFingerprint: string;
  matchesStoredPilotRequest: boolean;
};

const STORED_PILOT_STOPS = [
  "arne jacobsens allé 2, 2300 københavn",
  "55.0012777,11.9878823",
  "55.3824597,11.3319055",
  "55.6248884,12.0624072",
  "arne jacobsens allé 2, 2300 københavn",
];

export const GOOGLE_ROUTE_HOSTS = new Set([
  "google.com", "www.google.com", "maps.google.com",
  "google.at", "www.google.at", "maps.google.at",
  "google.be", "www.google.be", "maps.google.be",
  "google.bg", "www.google.bg", "maps.google.bg",
  "google.ch", "www.google.ch", "maps.google.ch",
  "google.cz", "www.google.cz", "maps.google.cz",
  "google.de", "www.google.de", "maps.google.de",
  "google.dk", "www.google.dk", "maps.google.dk",
  "google.ee", "www.google.ee", "maps.google.ee",
  "google.es", "www.google.es", "maps.google.es",
  "google.fi", "www.google.fi", "maps.google.fi",
  "google.fr", "www.google.fr", "maps.google.fr",
  "google.gr", "www.google.gr", "maps.google.gr",
  "google.hr", "www.google.hr", "maps.google.hr",
  "google.hu", "www.google.hu", "maps.google.hu",
  "google.ie", "www.google.ie", "maps.google.ie",
  "google.it", "www.google.it", "maps.google.it",
  "google.lt", "www.google.lt", "maps.google.lt",
  "google.lu", "www.google.lu", "maps.google.lu",
  "google.lv", "www.google.lv", "maps.google.lv",
  "google.nl", "www.google.nl", "maps.google.nl",
  "google.no", "www.google.no", "maps.google.no",
  "google.pl", "www.google.pl", "maps.google.pl",
  "google.pt", "www.google.pt", "maps.google.pt",
  "google.ro", "www.google.ro", "maps.google.ro",
  "google.se", "www.google.se", "maps.google.se",
  "google.si", "www.google.si", "maps.google.si",
  "google.sk", "www.google.sk", "maps.google.sk",
  "google.co.uk", "www.google.co.uk", "maps.google.co.uk",
  "google.com.cy", "www.google.com.cy", "maps.google.com.cy",
  "google.com.mt", "www.google.com.mt", "maps.google.com.mt",
  "maps.app.goo.gl", "goo.gl",
]);

export function isAllowedGoogleRouteHost(hostname: string): boolean {
  return GOOGLE_ROUTE_HOSTS.has(hostname.toLowerCase().replace(/\.$/, ""));
}

function decodeStop(value: string): string {
  let decoded = value.replace(/\+/g, " ");
  try { decoded = decodeURIComponent(decoded); } catch { /* retain the visible value */ }
  return decoded.normalize("NFKC").replace(/\s+/g, " ").trim();
}

function normalizeStop(value: string): string {
  const decoded = decodeStop(value);
  const coordinate = decoded.match(/^(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)$/);
  if (coordinate) return `${Number(coordinate[1])},${Number(coordinate[2])}`;
  return decoded.toLocaleLowerCase("en-US");
}

function makeStop(value: string): RouteStop {
  const normalized = normalizeStop(value);
  return {
    raw: decodeStop(value),
    normalized,
    kind: /^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/.test(normalized) ? "coordinate" : "place",
  };
}

function modeFromUrl(url: URL): RouteRequestEvidence["travelMode"] {
  const data = `${url.pathname}${url.search}`;
  if (/!3e0(?:!|\?|&|$)/.test(data)) return "driving";
  if (/!3e2(?:!|\?|&|$)/.test(data)) return "walking";
  if (/!3e1(?:!|\?|&|$)/.test(data)) return "bicycling";
  if (/!3e3(?:!|\?|&|$)/.test(data)) return "transit";
  return "unknown";
}

export async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return sha256Fallback(bytes);
}

function sha256Fallback(input: Uint8Array): string {
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const paddedLength = Math.ceil((input.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLength);
  padded.set(input);
  padded[input.length] = 0x80;
  const bitLength = input.length * 8;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x1_0000_0000));
  view.setUint32(paddedLength - 4, bitLength >>> 0);
  const words = new Uint32Array(64);
  const rotate = (word: number, amount: number) => (word >>> amount) | (word << (32 - amount));

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4);
    for (let index = 16; index < 64; index += 1) {
      const s0 = rotate(words[index - 15], 7) ^ rotate(words[index - 15], 18) ^ (words[index - 15] >>> 3);
      const s1 = rotate(words[index - 2], 17) ^ rotate(words[index - 2], 19) ^ (words[index - 2] >>> 10);
      words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = state;
    for (let index = 0; index < 64; index += 1) {
      const upper = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temp1 = (h + upper + choice + constants[index] + words[index]) >>> 0;
      const lower = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (lower + majority) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    state[0] = (state[0] + a) >>> 0; state[1] = (state[1] + b) >>> 0;
    state[2] = (state[2] + c) >>> 0; state[3] = (state[3] + d) >>> 0;
    state[4] = (state[4] + e) >>> 0; state[5] = (state[5] + f) >>> 0;
    state[6] = (state[6] + g) >>> 0; state[7] = (state[7] + h) >>> 0;
  }
  return state.map((word) => word.toString(16).padStart(8, "0")).join("");
}

export async function parseGoogleRouteUrl(sourceUrl: string, resolvedUrl = sourceUrl): Promise<RouteRequestEvidence> {
  const url = new URL(resolvedUrl);
  if (url.protocol !== "https:" || !isAllowedGoogleRouteHost(url.hostname)) {
    throw new Error("The resolved URL is not an allowed Google Maps HTTPS host.");
  }

  const parts = url.pathname.split("/").filter(Boolean);
  const dirIndex = parts.indexOf("dir");
  if (dirIndex < 0) throw new Error("The Google URL is not a directions route.");

  const routeParts: string[] = [];
  for (const part of parts.slice(dirIndex + 1)) {
    if (part.startsWith("@") || part === "data=" || part.startsWith("data=")) break;
    routeParts.push(part);
  }
  if (routeParts.length < 2) throw new Error("The directions URL does not expose both an origin and a destination.");

  const stops = routeParts.map(makeStop);
  const travelMode = modeFromUrl(url);
  const fingerprintPayload = JSON.stringify({
    schema: "roadproof-route-request-v1",
    travelMode,
    stops: stops.map((stop) => stop.normalized),
  });

  return {
    sourceUrl,
    resolvedUrl: url.toString(),
    origin: stops[0],
    destination: stops[stops.length - 1],
    waypoints: stops.slice(1, -1),
    travelMode,
    opaqueSelectionId: url.searchParams.get("skid"),
    requestFingerprint: await sha256Hex(fingerprintPayload),
    matchesStoredPilotRequest: travelMode === "driving" && stops.map((stop) => stop.normalized).join("\n") === STORED_PILOT_STOPS.join("\n"),
  };
}
