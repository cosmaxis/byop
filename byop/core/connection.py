"""Optional, non-inference check of an OpenAI-compatible endpoint."""

from __future__ import annotations

import http.client
import ipaddress
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_api_key(api_url: str, api_key: str) -> tuple[str, str]:
    if not api_key:
        return "error", "API key unavailable; check Keychain or the environment variable."
    try:
        url = urlsplit(api_url)
        host = url.hostname or ""
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if (url.scheme != "https" and not (url.scheme == "http" and loopback)) or not host:
            return "error", "Use HTTPS to test the key (HTTP is allowed only for localhost)."
        if url.username or url.password or url.query or url.fragment:
            return "error", "API URL must not contain credentials, a query, or a fragment for this check."
        request = urllib.request.Request(
            api_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if loopback:
            handlers.append(urllib.request.ProxyHandler({}))
        with urllib.request.build_opener(*handlers).open(request, timeout=10) as response:
            status = response.status
        return "ok", f"Endpoint accepted the request (HTTP {status}); it may not require authentication."
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "error", "Endpoint rejected authentication (HTTP 401); check the API key."
        if exc.code == 403:
            return "error", "Endpoint denied access (HTTP 403); check the key's permissions."
        return "warn", f"Could not verify the key (HTTP {exc.code}); check the endpoint and provider."
    except (http.client.HTTPException, OSError, ValueError):
        return "warn", "Could not reach or check the endpoint; verify the URL and network."
