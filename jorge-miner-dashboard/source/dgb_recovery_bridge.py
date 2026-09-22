"""Loopback-published, narrow HTTP bridge to AxeDGB's node recovery API."""

import http.client
import hmac
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_PATH = Path("/data/.dgb_recovery_bridge_token")


class BridgeHandler(BaseHTTPRequestHandler):
    def authenticated(self):
        supplied = self.headers.get("X-DGB-Recovery-Token", "")
        if len(supplied) != 64 or any(character not in "0123456789abcdef" for character in supplied):
            self.send_error(401)
            return False
        try:
            expected = TOKEN_PATH.read_text().strip()
        except (OSError, UnicodeError):
            self.send_error(503)
            return False
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected) or not hmac.compare_digest(expected, supplied):
            self.send_error(401)
            return False
        return True

    def do_GET(self):
        if not self.authenticated():
            return
        if self.path != "/api/node":
            self.send_error(404)
            return
        self.forward("GET")

    def do_POST(self):
        if not self.authenticated():
            return
        if self.path != "/api/node/resume" or self.headers.get("Content-Length") != "2" or self.headers.get("Content-Type") != "application/json":
            self.send_error(404)
            return
        if self.rfile.read(2) != b"{}":
            self.send_error(400)
            return
        self.forward("POST", b"{}")

    def forward(self, method, body=None):
        connection = http.client.HTTPConnection("axedgb-app", 3000, timeout=3)
        try:
            connection.request(method, self.path, body=body, headers={"Content-Type": "application/json"} if body else {})
            response = connection.getresponse()
            payload = response.read(262145)
            if len(payload) > 262144:
                self.send_error(502)
                return
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, TimeoutError, http.client.HTTPException):
            self.send_error(502)
        finally:
            connection.close()

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 5058), BridgeHandler).serve_forever()
