import ast
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app_v2


class RemovedBenchmarkTests(unittest.TestCase):
    def request(self, method, path):
        handler = object.__new__(app_v2.Handler)
        handler.path = path
        handler.headers = {"Host": "dashboard", "Origin": "http://dashboard",
                           "Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        getattr(handler, "do_" + method)()
        return handler

    def test_removed_get_routes_and_assets_return_404(self):
        for path in ("/benchmark", "/api/benchmark",
                     "/api/benchmark/report?session_id=old-session",
                     "/static/benchmark.html", "/static/benchmark.js",
                     "/static/benchmark.css"):
            with self.subTest(path=path):
                self.request("GET", path).send_response.assert_called_once_with(404)

    def test_removed_actions_return_404_without_reading_request_body(self):
        for action in ("start", "prepare", "cancel", "cancel-active",
                       "run-candidate", "run-full", "retry-restore",
                       "confirm-manual-restore"):
            with self.subTest(action=action):
                handler = self.request("POST", "/api/benchmark/" + action)
                handler.send_response.assert_called_once_with(404)
                self.assertEqual(handler.rfile.tell(), 0)

    def test_normal_pages_and_health_remain_available(self):
        for path in ("/", "/miners", "/thermal-settings", "/health"):
            with self.subTest(path=path):
                handler = self.request("GET", path)
                handler.send_response.assert_called_once_with(200)
                self.assertNotIn(b"/benchmark", handler.wfile.getvalue())

    def test_managed_miner_status_uses_telemetry(self):
        miner = {"name": "Test", "enabled": True, "base_freq": 600,
                 "hot_freq": 550, "critical_freq": 500}
        telemetry = {"temp": 60, "freq": 600, "volt": 1150,
                     "vr_temp": 50, "th": 1, "reject": 0}
        with patch.object(app_v2, "normalized_stats", return_value=telemetry):
            self.assertEqual(app_v2.read_miner(miner)["status"], "STABLE")
            telemetry["freq"] = 550
            self.assertEqual(app_v2.read_miner(miner)["status"], "COOLING")
            telemetry["th"] = 0
            self.assertEqual(app_v2.read_miner(miner)["status"], "OFFLINE")

    def test_startup_leaves_legacy_persistence_untouched(self):
        tree = ast.parse(Path(app_v2.__file__).read_text())
        startup = tree.body[-1]
        self.assertIsInstance(startup, ast.If)
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            legacy = {
                "benchmark_sessions.json": '{"old":{"state":"benchmarking","settings_written":true}}',
                "benchmark_restore_profiles.json": '{"old":{"recovery_required":true}}',
                "benchmark_results.json": '{"old":[]}',
                "thermal_locks.json": '{"Test":{"locked_by":"benchmark","session_id":"old"}}',
            }
            for name, content in legacy.items():
                (data_dir / name).write_text(content)
            namespace = dict(vars(app_v2), __name__="__main__", DATA_DIR=data_dir)
            namespace.update(ensure_page3_public_token=Mock(), startup_discovery=Mock(),
                             threading=Mock(), ThreadingHTTPServer=Mock())
            exec(compile(ast.Module(body=[startup], type_ignores=[]), "startup", "exec"), namespace)
            namespace["startup_discovery"].assert_called_once_with()
            namespace["ThreadingHTTPServer"].return_value.serve_forever.assert_called_once_with()
            self.assertEqual({p.name: p.read_text() for p in data_dir.iterdir()}, legacy)
