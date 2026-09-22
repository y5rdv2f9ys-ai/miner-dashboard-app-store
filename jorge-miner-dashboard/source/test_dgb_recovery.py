import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import app_v2 as app
import dgb_recovery_bridge as bridge


class DgbRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Path(self.directory.name) / "recovery.json"
        self.log = Path(self.directory.name) / "thermal.log"
        self.token = Path(self.directory.name) / "bridge-token"
        self.settings_patch = patch.object(app, "DGB_RECOVERY_SETTINGS_PATH", self.settings)
        self.log_patch = patch.object(app, "LOG_PATH", self.log)
        self.token_patch = patch.object(app, "DGB_RECOVERY_TOKEN_PATH", self.token)
        self.settings_patch.start()
        self.log_patch.start()
        self.token_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.log_patch.stop)
        self.addCleanup(self.token_patch.stop)
        app.DGB_RECOVERY_LAST_ATTEMPT.clear()
        app.DGB_RECOVERY_COMPLETED.clear()

    def status(self, state="paused", reason="unclean_exit", pause_id="pause-1"):
        return {"memory": {"state": state, "pause": {"reason": reason, "id": pause_id}}}

    def test_default_off_and_setting_persists(self):
        self.assertFalse(app.dgb_recovery_enabled())
        app.save_dgb_recovery_setting(True)
        self.assertTrue(json.loads(self.settings.read_text())["enabled"])
        self.assertTrue(app.dgb_recovery_enabled())
        app.save_dgb_recovery_setting(False)
        self.assertFalse(app.dgb_recovery_enabled())

    def test_private_persistent_token_and_backend_header(self):
        app.ensure_dgb_recovery_token()
        token = self.token.read_text().strip()
        self.assertEqual(len(token), 64)
        self.assertEqual(self.token.stat().st_mode & 0o777, 0o600)
        app.ensure_dgb_recovery_token()
        self.assertEqual(self.token.read_text().strip(), token)
        class FakeResponse:
            def __enter__(self):
                return io.BytesIO(b'{"memory":{}}')
            def __exit__(self, *_):
                return False
        with patch.object(app, "urlopen", return_value=FakeResponse()) as urlopen:
            app.dgb_recovery_request("/api/node")
            self.assertEqual(urlopen.call_args.args[0].get_header("X-dgb-recovery-token"), token)

    def test_setting_endpoint_does_not_return_token(self):
        app.ensure_dgb_recovery_token()
        app.save_dgb_recovery_setting(True)
        handler = object.__new__(app.Handler)
        handler.path = "/api/dgb-recovery"
        with patch.object(app.Handler, "send_json") as send_json:
            handler.do_GET()
        self.assertEqual(send_json.call_args.args, (200, {"enabled": True}))
        browser_files = (app.APP_DIR / "static" / "dashboard.html").read_text() + (app.APP_DIR / "static" / "dashboard.js").read_text()
        self.assertNotIn("X-DGB-Recovery-Token", browser_files)
        self.assertNotIn(self.token.read_text().strip(), browser_files)

    def test_disabled_does_not_query_or_resume(self):
        with patch.object(app, "dgb_recovery_request") as request:
            app.dgb_recovery_tick(100)
        request.assert_not_called()

    def test_only_unclean_exit_with_pause_id_resumes(self):
        app.save_dgb_recovery_setting(True)
        cases = [
            (self.status(state="running"), 0),
            (self.status(reason="core_exit"), 0),
            (self.status(reason="memory_pressure"), 0),
            (self.status(reason="ram_pressure"), 0),
            (self.status(reason="other"), 0),
            (self.status(pause_id=""), 0),
            ({"memory": {"state": "paused", "pause": None}}, 0),
            ({"memory": {"state": "paused", "pause": []}}, 0),
            ({"memory": {"state": "paused", "pause": {"reason": "unclean_exit"}}}, 0),
            (self.status(), 1),
        ]
        for index, (status, expected) in enumerate(cases):
            with self.subTest(status=status), patch.object(app, "dgb_recovery_request", side_effect=[status, None]) as request:
                app.dgb_recovery_tick(index * 1000)
                self.assertEqual(request.call_count, 1 + expected)
        self.assertIn("DGB Core | AUTO RESUMED AFTER UNCLEAN SHUTDOWN", self.log.read_text())
        self.assertEqual(app.recent_thermal_events()[0]["message"], "DGB Core automatically resumed after unclean shutdown")

    def test_unavailable_and_409_are_safe(self):
        app.save_dgb_recovery_setting(True)
        with patch.object(app, "dgb_recovery_request", side_effect=URLError("offline")) as request:
            app.dgb_recovery_tick(100)
            self.assertEqual(request.call_count, 1)
        conflict = HTTPError("http://127.0.0.1", 409, "already running", {}, None)
        with patch.object(app, "dgb_recovery_request", side_effect=[self.status(), conflict]) as request:
            app.dgb_recovery_tick(200)
            self.assertEqual(request.call_count, 2)
        with patch.object(app, "dgb_recovery_request", return_value=self.status()) as request:
            app.dgb_recovery_tick(1000)
            self.assertEqual(request.call_count, 1)
        self.assertFalse(self.log.exists())

    def test_cooldown_and_disable_guard(self):
        app.save_dgb_recovery_setting(True)
        with patch.object(app, "dgb_recovery_request", side_effect=[self.status(), None, self.status(), self.status()]) as request:
            app.dgb_recovery_tick(100)
            app.dgb_recovery_tick(101)
            app.dgb_recovery_tick(401)
            self.assertEqual(request.call_count, 4)
        app.save_dgb_recovery_setting(False)
        with patch.object(app, "dgb_recovery_request") as request:
            app.dgb_recovery_tick(1000)
            request.assert_not_called()


class DgbBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_path = Path(self.directory.name) / "token"
        self.token = "a" * 64
        self.token_path.write_text(self.token + "\n")
        token_patch = patch.object(bridge, "TOKEN_PATH", self.token_path)
        token_patch.start()
        self.addCleanup(token_patch.stop)

    def handler(self, path, body=b"", content_type="application/json", token=None):
        handler = object.__new__(bridge.BridgeHandler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body)), "Content-Type": content_type}
        if token is not None:
            handler.headers["X-DGB-Recovery-Token"] = token
        handler.rfile = io.BytesIO(body)
        return handler

    def test_bridge_rejects_missing_and_wrong_auth_without_forwarding(self):
        with patch.object(bridge.BridgeHandler, "forward") as forward, patch.object(bridge.BridgeHandler, "send_error") as error:
            self.handler("/api/node").do_GET()
            self.handler("/api/node/resume", b"{}").do_POST()
            self.handler("/api/node", token="b" * 64).do_GET()
            self.handler("/api/node/resume", b"{}", token="b" * 64).do_POST()
            forward.assert_not_called()
            self.assertEqual([call.args[0] for call in error.call_args_list], [401] * 4)

    def test_bridge_allows_only_authenticated_status_and_empty_resume(self):
        with patch.object(bridge.BridgeHandler, "forward") as forward, patch.object(bridge.BridgeHandler, "send_error") as error:
            self.handler("/api/node", token=self.token).do_GET()
            self.handler("/api/node/resume", b"{}", token=self.token).do_POST()
            self.assertEqual([call.args for call in forward.call_args_list], [("GET",), ("POST", b"{}")])
            self.handler("/api/node/stop", b"{}", token=self.token).do_POST()
            self.handler("/api/node/resume", b'{"force":true}', token=self.token).do_POST()
            self.handler("/api/node/resume", token=self.token).do_GET()
            self.assertEqual(forward.call_count, 2)
            self.assertEqual(error.call_count, 3)


if __name__ == "__main__":
    unittest.main()
