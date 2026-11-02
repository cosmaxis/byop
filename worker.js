const NO_STORE = { "Cache-Control": "no-store" };
const IMMUTABLE = { "Cache-Control": "public, max-age=31536000, immutable" };
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const SHA256 = /^[a-f0-9]{64}$/;

function error(status, message) {
  return new Response(message, {
    status,
    headers: { "Content-Type": "text/plain;charset=utf-8", ...NO_STORE },
  });
}

async function parseManifest(object) {
  if (!object) return null;
  try {
    const manifest = JSON.parse(await object.text());
    if (
      !manifest ||
      typeof manifest.version !== "string" ||
      typeof manifest.sha256 !== "string" ||
      typeof manifest.wheel !== "string" ||
      !VERSION.test(manifest.version) ||
      !SHA256.test(manifest.sha256) ||
      manifest.wheel !== `byop-${manifest.version}-py3-none-any.whl`
    ) return null;
    return {
      version: manifest.version,
      sha256: manifest.sha256,
      wheel: manifest.wheel,
    };
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD", ...NO_STORE },
      });
    }

    const head = request.method === "HEAD";
    if (path === "/") {
      const body = `byop — Bring Your Own Provider

Install (macOS):
  curl -sfS https://byop.cosmaxis.com/install | sh

Then run:
  byop
`;
      return new Response(head ? null : body, {
        headers: { "Content-Type": "text/plain;charset=utf-8", ...NO_STORE },
      });
    }

    const alias = path === "/install" || path === "/install.sh";
    const manifestRoute = path === "/latest.json";
    const release = /^\/releases\/([0-9]+\.[0-9]+\.[0-9]+)\/(install\.sh|manifest\.json|byop-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl)$/.exec(path);
    if (!alias && !manifestRoute && !release) return error(404, "Not found");
    if (release && release[2] !== "install.sh" && release[2] !== "manifest.json" &&
      release[2] !== `byop-${release[1]}-py3-none-any.whl`) {
      return error(404, "Not found");
    }
    if (!env?.RELEASES) return error(503, "Release storage unavailable");

    // Versioned objects never change, so cache them at the Cloudflare edge.
    const edgeCache = release && !head && ctx ? globalThis.caches?.default : null;
    const cacheKey = edgeCache ? new Request(new URL(path, request.url).toString()) : null;
    if (edgeCache) {
      try {
        const cached = await edgeCache.match(cacheKey);
        if (cached) return cached;
      } catch {
        // An unavailable cache must not block a release stored in R2.
      }
    }
    const respond = (response) => {
      if (edgeCache) {
        try {
          ctx.waitUntil(edgeCache.put(cacheKey, response.clone()).catch(() => {}));
        } catch {
          // Serving a release does not depend on populating the cache.
        }
      }
      return response;
    };

    try {
      let key;
      let cache;
      let contentType;
      if (alias || manifestRoute) {
        const manifest = await parseManifest(await env.RELEASES.get("latest.json"));
        if (!manifest) return error(503, "Release metadata unavailable");
        if (manifestRoute) {
          const body = JSON.stringify(manifest);
          return new Response(head ? null : body, {
            headers: { "Content-Type": "application/json;charset=utf-8", ...NO_STORE },
          });
        }
        key = `releases/${manifest.version}/install.sh`;
        cache = NO_STORE;
        contentType = "text/plain;charset=utf-8";
      } else {
        key = `releases/${release[1]}/${release[2]}`;
        cache = IMMUTABLE;
        contentType = release[2] === "install.sh"
          ? "text/plain;charset=utf-8"
          : release[2] === "manifest.json"
            ? "application/json;charset=utf-8"
            : "application/octet-stream";
      }

      const object = await env.RELEASES.get(key);
      if (!object) {
        return error(alias ? 503 : 404, alias ? "Release installer unavailable" : "Not found");
      }
      if (release?.[2] === "manifest.json") {
        const manifest = await parseManifest(object);
        if (!manifest || manifest.version !== release[1]) {
          return error(503, "Release metadata unavailable");
        }
        return respond(new Response(head ? null : JSON.stringify(manifest), {
          headers: { "Content-Type": contentType, ...cache },
        }));
      }
      return respond(new Response(head ? null : object.body, {
        headers: { "Content-Type": contentType, ...cache },
      }));
    } catch {
      return error(503, "Release storage unavailable");
    }
  },
};
