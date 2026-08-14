import json
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from textual.widgets import DataTable, Static

import app_v2
from tui.api_client import DashboardAPIClient, DashboardAPIError
from tui.app import HelpScreen, MinerDashboardApp
from tui.screens import (
    EventsScreen, MinerDetailScreen, MinersScreen, OverviewScreen, PerformanceScreen,
    PoolsScreen, SystemScreen, ThermalScreen,
)
from tui.screens.operations import allocation_bars


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
            "location_scope": "LOCAL", "management": "MANAGED",
            "braiins_worker": {"state": "ok", "hash_rate_5m_th": 1.1,
                               "hash_rate_60m_th": 1.08, "hash_rate_24h_th": 1.04},
        },
        {
            "name": "Remote-S21", "online": True, "th": 6.2,
            "status": "OFF-SITE", "pool": "Braiins", "coin": "BTC",
            "location_scope": "OFF-SITE", "management": "UNMANAGED",
            "hash_rate_5m_th": 6.2, "hash_rate_60m_th": 6.0,
        },
        {
            "name": "Remote-Idle", "online": False, "th": 0,
            "status": "OFF-SITE INACTIVE", "pool": "Braiins", "coin": "BTC",
            "location_scope": "OFF-SITE", "management": "UNMANAGED",
            "hash_rate_5m_th": 0, "hash_rate_60m_th": 0,
        },
        {
            "name": "BitaxeOffline", "online": False, "th": 0, "temp": 0,
            "vr_temp": -1, "freq": 0, "volt": 0, "reject": 0,
            "status": "OFFLINE", "pool": "Umbrel Solo", "coin": "BTC",
            "expected_th": 1.1, "thermal_limit": 70,
        },
    ],
    "system_status": {"thermal_management": True, "miner_logging": True},
    "performance_data": {"updated": "2026-08-11 12:00:00", "performance": [
        {"name": "Bitaxe001", "th_60m": 1.08, "th_12h": 1.06, "th_24h": 1.04},
        {"name": "BitaxeOffline", "th_60m": 0.25, "th_12h": 0.5, "th_24h": 0.7},
        {"name": "Remote-S21", "location_scope": "OFF-SITE", "th_now": 6.2,
         "th_60m": 6.0, "th_12h": None, "th_24h": 5.9},
        {"name": "Remote-Idle", "location_scope": "OFF-SITE", "th_now": 0,
         "th_60m": 0, "th_12h": None, "th_24h": 0.2},
    ]},
    "thermal_data": {"miners": [{
        "name": "Bitaxe001", "enabled": True, "current_temp": 61.5, "current_freq": 550,
        "status": "STABLE", "base_freq": 550, "hot_freq": 500, "critical_freq": 450,
        "recover_temp": 60, "warn_temp": 68, "critical_temp": 72,
        "base_volt": 1150, "hot_volt": 1100, "critical_volt": 1050,
    }]},
    "pools_data": {
        "total_th": 21.9,
        "btc_solo": {"hashrate_th": 1.3, "miners": ["Solo01"], "session_best": 120000,
                     "historic_best": 240000, "best_network_pct": 0.0000002,
                     "odds": {"difficulty": 120000000000000, "day_den": 250000, "month_den": 8300}},
        "bch_solo": {"hashrate_th": 2.7, "miners": ["BCH01"], "session_best": 500000,
                     "historic_best": 900000, "best_network_pct": 0.00012,
                     "odds": {"difficulty": 735000000000, "day_den": 1700, "month_den": 57}},
        "braiins": {"hashrate_th": 17.9, "pool_60m_th": 17.6, "today_reward": 0.00001234,
                    "balance": 0.00045678, "workers": [
                        {"name": "Bitaxe001", "scope": "LOCAL", "state": "ok", "hash_rate_5m_th": 1.1, "hash_rate_60m_th": 1.08},
                        {"name": "Remote-S21", "scope": "OFF-SITE", "state": "ok", "hash_rate_5m_th": 6.2, "hash_rate_60m_th": 6.0},
                        {"name": "Remote-Idle", "scope": "OFF-SITE", "state": "off", "hash_rate_5m_th": 0, "hash_rate_60m_th": 0},
                    ]},
    },
    "events_data": {"events": [
        {"level": "ERROR", "message": "BitaxeOffline | ERROR reading stats: timeout"},
        {"level": "WARNING", "message": "Bitaxe001 | HOT -> lowering"},
        {"level": "INFO", "message": "Bitaxe001 | Temp 61.5°C | Freq 550"},
    ]},
    "diagnostics_data": {
        "version": "1.2.19", "uptime_seconds": 223200,
        "snapshot_updated": "2026-08-11 12:00:00", "snapshot_age_seconds": 3,
        "thermal": {"updated_epoch": 1786459198},
        "history": {"updated_epoch": 1786459140},
        "storage": {"history_bytes": 26004684, "thermal_log_bytes": 3250586, "benchmark_bytes": 1468006},
    },
    "api_response_ms": 12.4,
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

    def test_all_dashboard_requests_are_get_only(self):
        def opener(request, timeout):
            payload = SNAPSHOT if request.full_url.endswith("/api/miners") else {}
            if request.full_url.endswith("/api/performance"):
                payload = SNAPSHOT["performance_data"]
            elif request.full_url.endswith("/api/thermal-settings"):
                payload = SNAPSHOT["thermal_data"]
            elif request.full_url.endswith("/api/page3"):
                payload = SNAPSHOT["pools_data"]
            elif "/api/events?" in request.full_url:
                payload = SNAPSHOT["events_data"]
            elif request.full_url.endswith("/api/diagnostics"):
                payload = SNAPSHOT["diagnostics_data"]
            self.assertEqual(request.get_method(), "GET")
            return Response(json.dumps(payload).encode())
        with patch("tui.api_client.urlopen", side_effect=opener):
            result = DashboardAPIClient("http://dashboard:5057").get_dashboard_data()
        self.assertEqual(result["pools_data"]["total_th"], 21.9)


class BackendSummaryTests(unittest.TestCase):
    def test_braiins_workers_are_exactly_matched_and_deduplicated(self):
        local = [{"name": "Bitaxe001", "online": True, "th": 1.1, "pool": "Braiins",
                  "thermal_enabled": True}]
        braiins = {"workers": [
            {"name": "bitaxe001", "state": "ok", "hash_rate_5m_th": 1.2, "hash_rate_60m_th": 1.1},
            {"name": "Remote-S21", "state": "ok", "hash_rate_5m_th": 6.2, "hash_rate_60m_th": 6.0},
            {"name": "Remote-Idle", "state": "off", "hash_rate_5m_th": 0, "hash_rate_60m_th": 0},
        ]}
        fleet = app_v2.normalize_fleet(local, braiins)
        self.assertEqual([item["name"] for item in fleet], ["Bitaxe001", "Remote-S21", "Remote-Idle"])
        self.assertEqual(fleet[0]["location_scope"], "LOCAL")
        self.assertEqual(fleet[1]["management"], "UNMANAGED")
        self.assertEqual(fleet[1]["status"], "OFF-SITE")
        self.assertEqual(fleet[2]["status"], "OFF-SITE INACTIVE")
        self.assertAlmostEqual(sum(float(item.get("th") or 0) for item in fleet), 7.3)

    def test_local_management_follows_thermal_state_without_changing_scope(self):
        fleet = app_v2.normalize_fleet([
            {"name": "Managed", "thermal_enabled": True},
            {"name": "Bitaxe403", "thermal_enabled": False},
        ], {})
        self.assertEqual(fleet[0]["management"], "MANAGED")
        self.assertEqual(fleet[1]["management"], "UNMANAGED")
        self.assertEqual(fleet[1]["location_scope"], "LOCAL")

    def test_fleet_summary_uses_hashing_activity_not_reachability(self):
        fleet = [
            {"location_scope": "LOCAL", "online": True, "th": 1.1},
            {"location_scope": "LOCAL", "online": True, "th": 0, "status": "OFFLINE"},
            {"location_scope": "OFF-SITE", "status": "OFF-SITE", "online": True},
            {"location_scope": "OFF-SITE", "status": "OFF-SITE INACTIVE", "online": False},
        ]
        self.assertEqual(app_v2.fleet_summary(fleet), {
            "active": 2, "total": 4, "local_online": 1, "local_total": 2,
            "offsite_mining": 1, "offsite_total": 2,
        })

    def test_solo_summary_supports_multiple_assignments_and_excludes_offsite(self):
        fleet = [
            {"name": "SoloA", "location_scope": "LOCAL", "pool": "Umbrel Solo", "online": True, "th": 1.2},
            {"name": "SoloB", "location_scope": "LOCAL", "pool": "Umbrel Solo", "online": True, "th": 0},
            {"name": "Remote", "location_scope": "OFF-SITE", "pool": "Umbrel Solo", "online": True, "th": 9},
        ]
        summary = app_v2.solo_pool_summary(fleet, "Umbrel Solo")
        self.assertEqual(summary["assigned_count"], 2)
        self.assertEqual(summary["active_count"], 1)
        self.assertEqual(summary["current_hashrate_th"], 1.2)
        self.assertEqual(summary["assigned_miners"][1], {"name": "SoloB", "active": False, "hashrate_th": 0.0})

    def test_all_offline_solo_assignments_have_zero_current_hashrate(self):
        summary = app_v2.solo_pool_summary([
            {"name": "BCH1", "location_scope": "LOCAL", "pool": "BCH SoloPool", "online": False, "th": 0},
            {"name": "BCH2", "location_scope": "LOCAL", "pool": "BCH SoloPool", "online": True, "th": 0},
        ], "BCH SoloPool")
        self.assertEqual(summary["active_count"], 0)
        self.assertEqual(summary["current_hashrate_th"], 0)

    def test_remote_missing_telemetry_does_not_affect_health_or_thermal_alerts(self):
        local = {"online": True, "th": 1, "expected_th": 1, "temp": 60,
                 "thermal_limit": 70, "reject": 0, "status": "STABLE", "location_scope": "LOCAL"}
        remote = {"online": False, "th": 0, "status": "OFF-SITE INACTIVE",
                  "location_scope": "OFF-SITE", "management": "UNMANAGED"}
        self.assertEqual(app_v2.dashboard_health([local, remote]), 100)
        self.assertEqual(app_v2.dashboard_alert_count([local, remote]), 0)

    def test_pool_payload_marks_local_and_offsite_workers_without_double_count(self):
        snapshot = {
            "miners": [
                {"name": "Bitaxe001", "pool": "Braiins", "th": 1.1, "online": True, "location_scope": "LOCAL",
                 "braiins_worker": {"state": "ok", "hash_rate_5m_th": 1.2, "hash_rate_60m_th": 1.1}},
                {"name": "Remote-S21", "pool": "Braiins", "th": 6.2, "online": True, "location_scope": "OFF-SITE",
                 "worker_state": "ok", "hash_rate_5m_th": 6.2, "hash_rate_60m_th": 6.0},
            ],
            "braiins": {"workers": [
                {"name": "Bitaxe001", "state": "ok", "hash_rate_5m_th": 1.2, "hash_rate_60m_th": 1.1},
                {"name": "Remote-S21", "state": "ok", "hash_rate_5m_th": 6.2, "hash_rate_60m_th": 6.0},
            ]},
        }
        payload = app_v2.build_page3_payload(snapshot)
        self.assertAlmostEqual(payload["braiins"]["hashrate_th"], 7.3)
        self.assertEqual([worker["scope"] for worker in payload["braiins"]["workers"]], ["OFF-SITE", "LOCAL"])

    def test_offsite_performance_uses_only_braiins_windows_and_deduplicates(self):
        snapshot = {"miners": [
            {"name": "Local", "location_scope": "LOCAL", "braiins_worker": {"hash_rate_60m_th": 1.0}},
            {"name": "Remote", "location_scope": "OFF-SITE", "hash_rate_5m_th": 6.2,
             "hash_rate_60m_th": 6.0, "hash_rate_24h_th": 5.8},
        ]}
        rows = app_v2.append_offsite_performance([
            {"name": "Local", "th_60m": 1.0, "th_12h": 1.0, "th_24h": 1.0}
        ], snapshot)
        self.assertEqual([row["name"] for row in rows], ["Local", "Remote"])
        self.assertEqual(rows[1]["th_now"], 6.2)
        self.assertEqual(rows[1]["th_60m"], 6.0)
        self.assertIsNone(rows[1]["th_12h"])
        self.assertEqual(rows[1]["th_24h"], 5.8)

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

    def test_miners_endpoint_returns_fleet_summary_from_cached_snapshot(self):
        snapshot = dict(SNAPSHOT)
        snapshot["fleet_summary"] = app_v2.fleet_summary(snapshot["miners"])
        handler = app_v2.Handler.__new__(app_v2.Handler)
        handler.path = "/api/miners"
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        with patch.object(app_v2, "get_dashboard_snapshot", return_value=snapshot):
            handler.do_GET()
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(payload["fleet_summary"]["local_total"], 2)
        self.assertEqual(payload["fleet_summary"]["offsite_total"], 2)

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

    def test_recent_events_are_bounded_newest_first(self):
        content = b"one\nwarning cooling\nERROR newest\n"
        with patch.object(app_v2, "LOG_PATH") as path:
            path.open.return_value.__enter__.return_value = io.BytesIO(content)
            events = app_v2.recent_thermal_events(2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["level"], "ERROR")
        self.assertEqual(events[1]["level"], "WARNING")

    def test_events_endpoint_caps_limit(self):
        handler = app_v2.Handler.__new__(app_v2.Handler)
        handler.path = "/api/events?limit=99999"
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        with patch.object(app_v2, "recent_thermal_events", return_value=[]) as recent:
            handler.do_GET()
        recent.assert_called_once_with(99999)
        # The reader itself owns the hard cap, even for an oversized query value.
        self.assertEqual(app_v2.recent_thermal_events.__defaults__[0], 100)


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
            self.assertEqual(table.row_count, 4)
            self.assertIn("7.30 TH/s", str(app.screen.query_one("#total-hash", Static).render()))
            self.assertIn("2/4", str(app.screen.query_one("#online-count", Static).render()))
            self.assertIn("Active", str(app.screen.query_one("#online-count", Static).render()))
            self.assertIn("Off-site", str(app.screen.query_one("#fleet-scope", Static).render()))
            labels = {str(column.label) for column in table.columns.values()}
            self.assertEqual(labels, {"State", "Miner", "TH/s", "ASIC", "Thermal"})

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
            self.assertEqual(table.row_count, 4)
            self.assertEqual({str(key.value) for key in table.rows}, {"Bitaxe001", "BitaxeOffline", "Remote-S21", "Remote-Idle"})
            offline_row = next(index for index, key in enumerate(table.rows) if str(key.value) == "BitaxeOffline")
            table.move_cursor(row=offline_row)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, MinerDetailScreen)
            self.assertIn("BitaxeOffline", str(app.screen.query_one("#detail-content", Static).render()))
            await pilot.press("escape")
            self.assertIsInstance(app.screen, MinersScreen)

    async def test_keyboard_navigation_all_screens_help_and_back(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            expected = (PerformanceScreen, ThermalScreen, PoolsScreen, EventsScreen, SystemScreen)
            for key, screen_type in zip("34567", expected):
                await pilot.press(key)
                await pilot.pause()
                self.assertIsInstance(app.screen, screen_type)
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
            labels = {str(column.label) for column in table.columns.values()}
            self.assertEqual(labels, {"State", "Miner", "TH/s", "ASIC", "VR", "MHz", "mV", "Reject", "Thermal", "Pool"})
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

    async def test_operational_screens_render_and_tolerate_missing_fields(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            for key, table_id in (("3", "#performance-table"), ("4", "#thermal-table"), ("6", "#events-table")):
                await pilot.press(key)
                await pilot.pause()
                with self.subTest(screen=key):
                    self.assertGreater(app.screen.query_one(table_id, DataTable).row_count, 0)
            await pilot.press("5")
            self.assertIn("Braiins", str(app.screen.query_one("#allocation", Static).render()))
            self.assertIn("OFF-SITE", str(app.screen.query_one("#pool-workers", Static).render()))
            await pilot.press("7")
            self.assertIn("APPLICATION", str(app.screen.query_one("#system-content", Static).render()))

        minimal = {"miners": [{}], "system_status": {}, "performance_data": {}, "thermal_data": {},
                   "pools_data": {}, "events_data": {}}
        app = MinerDashboardApp(FakeClient(minimal), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(90, 30)) as pilot:
            for key in "34567":
                await pilot.press(key)
                await pilot.pause()
                self.assertTrue(app.screen.is_mounted)

    async def test_remote_performance_is_available_and_thermal_remains_local(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("3")
            performance = app.screen.query_one("#performance-table", DataTable)
            self.assertEqual(performance.row_count, 4)
            self.assertIn("Active 2/4", str(app.screen.query_one("#performance-summary", Static).render()))
            await pilot.press("4")
            thermal = app.screen.query_one("#thermal-table", DataTable)
            self.assertEqual(thermal.row_count, 1)

    async def test_offsite_miners_show_unmanaged_without_fake_telemetry(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("2")
            table = app.screen.query_one(DataTable)
            row = [str(cell) for cell in table.get_row("Remote-S21")]
            self.assertTrue(any("OFF-SITE" in cell for cell in row))
            self.assertIn("UNMANAGED", row)
            self.assertGreaterEqual(row.count("—"), 5)

    async def test_narrow_operational_tables_hide_lower_priority_columns(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.press("3")
            labels = {str(column.label) for column in app.screen.query_one(DataTable).columns.values()}
            self.assertNotIn("24h", labels)
            self.assertNotIn("MHz", labels)
            await pilot.press("4")
            labels = {str(column.label) for column in app.screen.query_one(DataTable).columns.values()}
            self.assertNotIn("Base/Hot/Crit mV", labels)

    def test_pool_allocation_uses_horizontal_bars_only(self):
        bars = allocation_bars(SNAPSHOT["pools_data"])
        for label in ("Braiins", "Umbrel Solo", "BCH Solo"):
            self.assertIn(label, bars)
        self.assertIn("81.7%", bars)
        source = Path(__file__).with_name("tui").joinpath("screens", "operations.py").read_text()
        self.assertNotIn("donut", source.lower())
        self.assertNotIn("pool_visualization", source)

    def test_system_contains_no_host_monitoring(self):
        source = Path(__file__).with_name("tui").joinpath("screens", "operations.py").read_text().lower()
        for forbidden in ("cpu usage", "ram usage", "disk usage", "host uptime", "docker"):
            self.assertNotIn(forbidden, source)

    async def test_q_exits(self):
        app = MinerDashboardApp(FakeClient(), enable_periodic_refresh=False, threaded_requests=False)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("q")
            await pilot.pause()
            self.assertFalse(app.is_running)


if __name__ == "__main__":
    unittest.main()
