import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const providersFile = new URL("./byop-keychain.json", import.meta.url);

function serverFor(provider) {
  try {
    const providers = JSON.parse(readFileSync(providersFile, "utf8"));
    return Object.hasOwn(providers, provider) ? providers[provider] : undefined;
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw new Error("Could not read the byop OpenCode Keychain configuration.");
  }
}

function keyFor(server) {
  try {
    const key = execFileSync(
      "/usr/bin/security",
      ["find-internet-password", "-s", server, "-a", "Bearer", "-w"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    ).trim();
    if (key) return key;
  } catch {}
  throw new Error("Could not read the OpenCode provider key from macOS Keychain.");
}

function matchesEndpoint(request, server) {
  const url = new URL(request);
  const base = new URL(server);
  const path = base.pathname.replace(/\/+$/, "");
  return url.origin === base.origin && (url.pathname === path || url.pathname.startsWith(path + "/"));
}

export default {
  id: "byop.keychain",
  async server() {
    return {
      async "chat.headers"(input, output) {
        const server = serverFor(input.model.providerID);
        if (!server) return;
        if (input.provider.options.baseURL?.replace(/\/+$/, "") !== server) {
          throw new Error("The OpenCode provider endpoint changed; reapply the byop profile.");
        }
        output.headers.Authorization = `Bearer ${keyFor(server)}`;
      },
    };
  },
  async setup(ctx) {
    await ctx.session.hook("http.request", (event) => {
      const server = serverFor(event.model.providerID);
      if (!server) return;
      if (!matchesEndpoint(event.request.url, server)) {
        throw new Error("The OpenCode provider endpoint changed; reapply the byop profile.");
      }
      event.request.headers.set("Authorization", `Bearer ${keyFor(server)}`);
    });
  },
};
