export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;
    const REPO = "Cosmaxis/byop";

    // Resolve the latest release tag so installs are pinned to immutable
    // content (no stale CDN cache); fall back to the default branch.
    let version = "latest";
    try {
      const release = await fetch(
        `https://api.github.com/repos/${REPO}/releases/latest`,
        {
          headers: { "User-Agent": "byop-worker" },
          cf: { cacheTtl: 60 },
        },
      );
      if (release.ok) {
        const data = await release.json();
        version = data.tag_name || "latest";
      }
    } catch {
      // fall through with 'latest'
    }

    const ref = version !== "latest" ? version : "master";
    const base = `https://raw.githubusercontent.com/${REPO}/${ref}`;

    const routes = {
      "/install": `${base}/install.sh`,
      "/install.sh": `${base}/install.sh`,
    };

    if (path === "/") {
      const displayVersion = version.replace(/^v/, "");
      return new Response(
        `byop — Bring Your Own Provider ${version !== "latest" ? "v" + displayVersion : ""}

Wire a custom OpenAI-compatible LLM provider into Zed, py.dev, and more.

Install (macOS):
  curl -sfS https://byop.cosmaxis.com/install | sh

Then run:
  byop

Documentation: https://github.com/${REPO}
`,
        { headers: { "Content-Type": "text/plain;charset=utf-8" } },
      );
    }

    const targetUrl = routes[path];
    if (!targetUrl) {
      return new Response("Not found", { status: 404 });
    }

    try {
      const response = await fetch(targetUrl, {
        cf: { cacheTtl: 60, cacheEverything: true },
      });
      if (!response.ok) {
        return new Response(`Upstream error: ${response.status}`, {
          status: 502,
          headers: { "Content-Type": "text/plain;charset=utf-8" },
        });
      }
      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/plain;charset=utf-8",
          "Cache-Control": "public, max-age=60",
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch {
      return new Response("Failed to fetch upstream resource", {
        status: 502,
        headers: { "Content-Type": "text/plain;charset=utf-8" },
      });
    }
  },
};
