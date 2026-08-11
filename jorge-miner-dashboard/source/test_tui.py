import json
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from textual.widgets import DataTable, Static

import app_v2
from tui.api_client import DashboardAPIClient, DashboardAPIError
from tui.app import HelpScreen, MinerDashboardApp
from tui.screens import MinerDetailScreen, MinersScreen, OverviewScreen, PlaceholderScreen


SNAPSHOT = {
    "updated": "2026-08-11 12:00:00",
    "health": 50,
    "alert_count": 1,
    "miners": [
        {
            "name": "Bitaxe001", "online": True, "th": 1.1, "temp": 61.5,
            "vr_temp": 70.2, "freq": 550, "volt": 1150, "reject": 0.25,
            "status": "STABLE", "status_class": "STABLE", "pool": "Braiins",
            "coin": "BTC", "expected_th": 1.1, "thermal_limit": 70,
            "best_session_diff": 12000, "best_diff": 24000,
        },
        {
            "name": "BitaxeOffline", "online": False, "th": 0, "temp": 0,
            "vr_temp": -1, "freq": 0, "volt": 0, "reject": 0,
            "status": "OFFLINE", "pool": "Umbrel Solo", "coin": "BTC",
            "expected_th": 1.1, "thermal_limit": 70,
        },
    ],
    "system_status": {"thermal_management": True, "miner_logging": True},
}


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class APIClientTests(unittest.TestCase):
    def test_parses_valid_miners_snapshot_with_get(self):
        opener = Mock(return_value=Response(json.dumps(SNAPSHOT).encode()))
        with patch("tui.api_client.urlopen", opener):
            result = DashboardAPIClient("http://dashboard:5057", timeout=2).get_miners()
        self.assertEqual(result["miners"][0]["name"], "Bitaxe001")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "http://dashboard:5057/api/miners")

    def test_connection_failure_is_clean_error(self):
        with patch("tui.api_client.urlopen", side_effect=OSError("connection refused")):
            with self.assertRaisesRegex(DashboardAPIError, "unavailable"):
                DashboardAPIClient().get_miners()

    def test_malformed_and_missing_fields(self):
        with patch("tui.api_client.urlopen", return_value=Response(b"not-json")):
            with self.assertRaisesRegex(DashboardAPIError, "malformed"):
                DashboardAPIClient().get_miners()
        with patch("tui.api_client.urlopen", return_value=Response(b"{}")):
            self.assertEqual(DashboardAPIClient().get_miners()["miners"], [])
        with patch("tui.api_client.urlopen", return_value=Response(b'{"miners": "bad"}')):
            with self.assertRaisesRegex(DashboardAPIError, "must be a list"):
                DashboardAPIClient().get_miners()

    def test_client_has_no_write_request_methods(self):
        client = DashboardAPIClient()
        for method in ("post", "patch", "put", "delete", "request"):
            self.assertFalse(hasattr(client, method))

    def test_client_source_has_no_direct_miner_endpoint(self):
        source = Path(__file__).with_name("tui").joinpath("api_client.py").read_text()
        self.assertNotIn("/api/system", source)
        self.assertNotIn("/data", source)


class BackendSummaryTests(unittest.TestCase):
    def test_health_preserves_existing_web_formula(self):
        self.assertEqual(app_v2.dashboard_health([]), 100)
        self.assertEqual(app_v2.dashboard_health([
            {"online": True, "th": 1, "expected_th": 1, "temp": 60, "thermal_limit": 70, "reject": 0}
        ]), 100)
        self.assertEqual(app_v2.dashboard_health([
            {"online": False, "th": 0, "expected_th": 1, "temp": 0, "thermal_limit": 70, "reject": 0}
        ]), 0)
        self.assertEqual(app_v2.dashboard_health([
            {"online": True, "th": 1, "expected_th": 1, "temp": 63, "thermal_limit": 70, "reject": 1}
        ]), 87)
        self.assertEqual(app_v2.dashboard_health(SNAPSHOT["miners"]), 50)

    def test_alert_count_matches_existing_statuses(self):
        miners = [
            {"status": "STABLE"}, {"status": "COOLING"},
            {"status": "MAX COOLING"}, {"status": "OFFLINE"},
            {"status": "HOLDING"}, {"status": "BENCHMARK"},
        ]
        self.assertEqual(app_v2.dashboard_alert_count(miners), 3)

    def test_cached_snapshot_contains_authoritative_summary(self):
        miners = SNAPSHOT["miners"]
        with patch.object(app_v2, "load_miners", return_value=[]), \
             patch.object(app_v2, "collect_miners", return_value=miners), \
             patch.object(app_v2, "record_history"), \
             patch.object(app_v2, "update_pool_runs", return_value={}), \
             patch.object(app_v2, "fetch_solopool_stats", return_value={"blocks": []}), \
             patch.object(app_v2, "fetch_braiins_stats", return_value={}), \
             patch.object(app_v2, "build_odds", return_value={}), \
             patch.object(app_v2.ALERT_MANAGER, "process"):
            result = app_v2.collect_dashboard_snapshot()
        self.assertEqual(result["health"], 50)
        self.assertEqual(result["alert_count"], 1)

    def test_miners_endpoint_returns_authoritative_summary_fields(self):
        handler = app_v2.Handler.__new__(app_v2.Handler)
        handler.path = "/api/miners"
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        with patch.object(app_v2, "get_dashboard_snapshot", return_value=SNAPSHOT):
            handler.do_GET()
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(payload["health"], 50)
        self.assertEqual(payload["alert_count"], 1)

    def test_web_and_tui_do_not_define_summary_formulas(self):
        root = Path(__file__).parent
        javascript = root.joinpath("static/dashboard.js").read_text()
        formatting = root.joinpath("tui/formatting.py").read_text()
        self.assertNotIn("calculateHealth", javascript)
        self.assertNotIn("def health_percent", formatting)
        self.assertNotIn("def alert_count", formatting)
        self.assertIn("data.health", javascript)
        self.assertIn("data.alert_count", javascript)

    def test_host_launcher_resolves_compose_service_by_labels(self):
        launcher = Path(__file__).with_name("miners-host").read_text()
        self.assertIn("com.docker.compose.project=jorge-miner-dashboard", launcher)
        self.assertIn("com.docker.compose.service=dashboard", launcher)
        self.assertNotIn("jorge-miner-dashboard_dashboard_1", launcher)


class FakeClient:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot or SNAPSHOT
        self.error = error
        self.calls = 0

    def get_miners(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.snapshot


class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_renders_expected_summary_and_miners(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, OverviewScreen)
            table = app.screen.query_one("#miner-table", DataTable)
            self.assertEqual(table.row_count, 2)
            self.assertIn("1.10 TH/s", str(app.screen.query_one("#total-hash", Static).render()))
            self.assertIn("1/2", str(app.screen.query_one("#online-count", Static).render()))

    async def test_overview_uses_backend_summary_without_recalculation(self):
        snapshot = dict(SNAPSHOT)
        snapshot["health"] = 37
        snapshot["alert_count"] = 9
        app = MinerDashboardApp(FakeClient(snapshot), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.pause()
            self.assertIn("37%", str(app.screen.query_one("#health", Static).render()))
            self.assertIn("9", str(app.screen.query_one("#alerts", Static).render()))

    async def test_miners_renders_online_and_offline_and_opens_detail(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            self.assertIsInstance(app.screen, MinersScreen)
            table = app.screen.query_one(DataTable)
            self.assertEqual(table.row_count, 2)
            self.assertEqual({str(key.value) for key in table.rows}, {"Bitaxe001", "BitaxeOffline"})
            table.move_cursor(row=1)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, MinerDetailScreen)
            self.assertIn("BitaxeOffline", str(app.screen.query_one("#detail-content", Static).render()))
            await pilot.press("escape")
            self.assertIsInstance(app.screen, MinersScreen)

    async def test_keyboard_navigation_placeholders_help_and_back(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            for key, title in zip("34567", ("Performance", "Thermal", "Pools", "Events", "System")):
                await pilot.press(key)
                await pilot.pause()
                self.assertIsInstance(app.screen, PlaceholderScreen)
                self.assertEqual(app.screen.title, title)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, OverviewScreen)
            await pilot.press("?")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, OverviewScreen)

    async def test_jk_navigation_and_selection_survives_refresh(self):
        client = FakeClient()
        app = MinerDashboardApp(client, enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("2", "j")
            table = app.screen.query_one(DataTable)
            self.assertEqual(table.cursor_row, 1)
            await pilot.press("r")
            await pilot.pause()
            self.assertGreaterEqual(client.calls, 2)
            self.assertEqual(table.cursor_row, 1)
            await pilot.press("k")
            self.assertEqual(table.cursor_row, 0)

    async def test_narrow_terminal_hides_columns_and_missing_values_are_safe(self):
        snapshot = dict(SNAPSHOT)
        snapshot["miners"] = [{"name": "Minimal", "online": True}]
        app = MinerDashboardApp(FakeClient(snapshot), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(70, 25)) as pilot:
            await pilot.press("2")
            await pilot.pause()
            table = app.screen.query_one(DataTable)
            labels = {str(column.label) for column in table.columns.values()}
            self.assertNotIn("Pool", labels)
            self.assertNotIn("Reject", labels)
            self.assertNotIn("mV", labels)
            self.assertNotIn("VR", labels)
            self.assertEqual(table.row_count, 1)
            await pilot.press("enter")
            self.assertIsInstance(app.screen, MinerDetailScreen)

    async def test_api_failure_keeps_ui_usable(self):
        app = MinerDashboardApp(FakeClient(error=DashboardAPIError("offline")), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, OverviewScreen)
            self.assertIn("API ERROR", str(app.screen.query_one("#api-status", Static).render()))

    async def test_q_exits(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("q")
            await pilot.pause()
            self.assertFalse(app.is_running)


if __name__ == "__main__":
    unittest.main()
