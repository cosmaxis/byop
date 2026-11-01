"""Exercise Codex's actual Responses request when the native CLI is available."""

import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest import mock

import pytest

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.codex import CodexTarget

_CODEX = shutil.which("codex")


@pytest.mark.skipif(_CODEX is None, reason="native Codex CLI is not installed")
def test_single_model_reasoning_none_reaches_responses_request(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            message = {
                "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "OK", "annotations": []}],
            }
            response = {
                "id": "resp_1", "object": "response", "created_at": 1700000000,
                "status": "completed", "model": request["model"], "output": [message],
                "usage": {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
            }
            events = [
                {"type": "response.created", "response": {
                    "id": "resp_1", "object": "response", "created_at": 1700000000,
                    "status": "in_progress", "output": [],
                }},
                {"type": "response.output_item.added", "output_index": 0, "item": {
                    "id": "msg_1", "type": "message", "role": "assistant",
                    "status": "in_progress", "content": [],
                }},
                {"type": "response.content_part.added", "item_id": "msg_1", "output_index": 0,
                 "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
                {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0,
                 "content_index": 0, "delta": "OK"},
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": response},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        codex_home = tmp_path / ".codex"
        target = CodexTarget(codex_home / "config.toml")
        provider = ProviderConfig(
            "Demo", f"http://127.0.0.1:{server.server_port}/v1", "dummy-test-key",
            [ModelConfig("plain", reasoning_effort="none")],
        )
        with mock.patch("byop.core.targets.codex.kc.ensure_key"):
            target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)

        env = {
            "HOME": str(tmp_path), "CODEX_HOME": str(codex_home),
            "PATH": os.environ.get("PATH", ""), "DEMO_API_KEY": "dummy-test-key",
            "NO_COLOR": "1", "CODEX_DISABLE_TELEMETRY": "1",
        }
        result = subprocess.run(
            [_CODEX, "exec", "--skip-git-repo-check", "--ephemeral", "-C", str(tmp_path), "Say OK"],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stderr
        assert requests[0]["model"] == "plain"
        assert requests[0]["reasoning"]["effort"] == "none"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
