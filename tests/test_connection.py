from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from byop.core.connection import check_api_key


def test_key_check_handles_authentication_and_redirects():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect/models":
                self.send_response(302)
                self.send_header("Location", "/stolen")
            elif self.headers.get("Authorization") == "Bearer valid-key":
                self.send_response(200)
            else:
                self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert check_api_key(url + "/v1", "valid-key")[0] == "ok"
        assert check_api_key(url + "/v1", "wrong-key")[0] == "error"
        assert check_api_key(url + "/redirect", "valid-key")[0] == "warn"
        assert check_api_key("http://example.com/v1", "valid-key")[0] == "error"
        assert check_api_key("http://localhost/bad path", "valid-key")[0] == "warn"
        assert received == [
            ("/v1/models", "Bearer valid-key"),
            ("/v1/models", "Bearer wrong-key"),
            ("/redirect/models", "Bearer valid-key"),
        ]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
