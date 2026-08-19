/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { isAllowedGoogleRouteHost, parseGoogleRouteUrl } from "../lib/route-intelligence.ts";

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/api/resolve-route") {
      return resolveRouteRequest(request);
    }

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    return handler.fetch(request, env, ctx);
  },
};

export default worker;

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
}

async function resolveRouteRequest(request: Request): Promise<Response> {
  if (request.method !== "POST") return jsonResponse({ error: "Use POST." }, 405);
  const contentLength = Number(request.headers.get("content-length") || "0");
  if (contentLength > 8_192) return jsonResponse({ error: "Request body is too large." }, 413);

  let sourceUrl: string;
  try {
    const body = await request.json() as { url?: unknown };
    if (typeof body.url !== "string" || body.url.length > 4_096) throw new Error();
    sourceUrl = body.url.trim();
  } catch {
    return jsonResponse({ error: "Provide one Google Maps URL." }, 400);
  }

  let current: URL;
  try {
    current = new URL(sourceUrl);
    if (current.protocol !== "https:" || !isAllowedGoogleRouteHost(current.hostname)) throw new Error();
  } catch {
    return jsonResponse({ error: "Only HTTPS Google Maps route URLs are accepted." }, 400);
  }

  try {
    for (let hop = 0; hop < 6; hop += 1) {
      if (current.pathname.split("/").includes("dir")) {
        const route = await parseGoogleRouteUrl(sourceUrl, current.toString());
        return jsonResponse({
          route,
          evidence: {
            routeRequest: "confirmed",
            exactSelectedGeometry: "unresolved",
            roadClassification: "unresolved",
          },
        });
      }

      const response = await fetch(current.toString(), {
        method: "GET",
        redirect: "manual",
        headers: { "user-agent": "RoadProof/1.1 route-link resolver" },
      });
      const location = response.headers.get("location");
      if (!location) throw new Error("Google did not return a directions redirect for this link.");
      const next = new URL(location, current);
      if (next.protocol !== "https:" || !isAllowedGoogleRouteHost(next.hostname)) {
        throw new Error("Google redirected to a host that is not on the route allowlist.");
      }
      current = next;
    }
    throw new Error("The Google route link exceeded the redirect limit.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "The Google route could not be resolved.";
    return jsonResponse({ error: message }, 422);
  }
}
