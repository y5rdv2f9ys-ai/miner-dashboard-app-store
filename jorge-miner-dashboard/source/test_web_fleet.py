import unittest
from pathlib import Path
from unittest.mock import patch

import app_v2


class WebFleetTests(unittest.TestCase):
    def setUp(self):
        self.static = Path(app_v2.APP_DIR) / "static"

    def test_dashboard_uses_backend_fleet_summary_and_null_safe_metrics(self):
        script = (self.static / "dashboard.js").read_text()
        self.assertIn("data.fleet_summary", script)
        self.assertIn("Active ${active}/${total}", script)
        self.assertIn("Off-site ${summary.offsite_mining", script)
        self.assertIn("function metricValue", script)
        self.assertNotIn("miner.temp.toFixed", script)
        self.assertNotIn("miner.vr_temp.toFixed", script)
        self.assertIn("'—'", script)

    def test_dashboard_uses_compact_responsive_live_fleet_list(self):
        css = (self.static / "dashboard.css").read_text()
        html = (self.static / "dashboard.html").read_text()
        script = (self.static / "dashboard.js").read_text()
        self.assertNotIn("max-height: 430px", css)
        self.assertIn(".live-fleet-list", css)
        self.assertIn(".live-fleet-row", css)
        self.assertIn("@media (max-width: 430px)", css)
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn('class="live-fleet-list"', html)
        self.assertIn("live-fleet-head", script)
        self.assertIn("fleet-hash", script)
        self.assertNotIn("mobile-card", script)
        self.assertNotIn("Pool ${", script.split("function renderMinerDashboard", 1)[1].split("function renderStrategy", 1)[0])
        self.assertIn(".dashboard-page .swipe-hint", css)
        dashboard_hint = css.split(".dashboard-page .swipe-hint", 1)[1].split("}", 1)[0]
        self.assertIn("position: static", dashboard_hint)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("overflow: hidden", css.split(".live-fleet-list", 1)[1].split("}", 1)[0])

    def test_dashboard_content_is_fluid_through_tablet_and_capped_on_desktop(self):
        css = (self.static / "dashboard.css").read_text()
        content_rule = css.split(".dashboard-page > * {", 1)[1].split("}", 1)[0]
        self.assertIn("width: 100%", content_rule)
        self.assertIn("max-width: 1160px", content_rule)
        self.assertIn("margin-left: auto", content_rule)
        self.assertIn("margin-right: auto", content_rule)
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn("@media (max-width: 430px)", css)
        mobile_rule = css.split("@media (max-width: 430px)", 1)[1].split("@media (max-width: 360px)", 1)[0]
        self.assertIn("min-height: 68px", mobile_rule)
        self.assertIn(".fleet-vr", mobile_rule)
        self.assertIn(".fleet-mhz", mobile_rule)

    def test_dashboard_has_offsite_styles_and_solo_assignment_rows(self):
        css = (self.static / "dashboard.css").read_text()
        html = (self.static / "dashboard.html").read_text()
        script = (self.static / "dashboard.js").read_text()
        self.assertIn(".status.OFF-SITE", css)
        self.assertIn(".status.OFF-SITE-INACTIVE", css)
        self.assertIn('id="btcSoloMinerList"', html)
        self.assertIn('id="bchSoloMinerList"', html)
        self.assertIn("renderSoloAssignment", script)
        self.assertIn("data.braiins_workers", script)
        self.assertNotIn("braiins.workers || []", script)

    def test_overview_rows_use_authoritative_scope_and_telemetry(self):
        script = (self.static / "dashboard.js").read_text()
        overview = script.split("function renderMinerDashboard", 1)[1].split("function renderStrategy", 1)[0]
        self.assertIn("management === 'UNMANAGED'", overview)
        self.assertIn("remoteTelemetry ? '—'", overview)
        self.assertIn("miner.telemetry_source === 'BRAIINS'", overview)
        self.assertIn("OFF-SITE INACTIVE", overview)
        self.assertIn("thermal_status", overview)
        self.assertNotIn("miner.pool", overview)
        self.assertIn("remoteTelemetry ? 'Braiins telemetry' : 'Local telemetry available'", overview)
        self.assertNotIn(" : 'Local'", overview)

    def test_performance_thermal_counts_are_backend_authoritative(self):
        script = (self.static / "dashboard.js").read_text()
        self.assertIn("latestThermalCounts = data.thermal_counts || {}", script)
        self.assertIn("const snapshotCounts = latestThermalCounts", script)

    def test_performance_strip_wraps_and_solo_columns_are_separated(self):
        css = (self.static / "dashboard.css").read_text()
        html = (self.static / "dashboard.html").read_text()
        self.assertIn('class="footer performance-summary"', html)
        thermal_strip = css.split(".thermal-strip {", 1)[1].split("}", 1)[0]
        self.assertIn("display: flex", thermal_strip)
        self.assertIn("flex-wrap: wrap", thermal_strip)
        solo_row = css.split(".solo-miner-row {", 1)[1].split("}", 1)[0]
        self.assertIn("minmax(48px, auto)", solo_row)
        self.assertIn("column-gap: 10px", solo_row)

    def test_management_and_benchmark_sources_remain_configured_local_only(self):
        configured = [
            {"name": "Managed", "enabled": True, "ip": "10.0.0.1", "type": "axeos"},
            {"name": "Bitaxe403", "enabled": False, "ip": "10.0.0.2", "type": "axeos"},
        ]
        with patch.object(app_v2, "load_miners", return_value=configured):
            payload = app_v2.miner_management_payload()
            self.assertEqual([item["name"] for item in payload["miners"]], ["Managed", "Bitaxe403"])
            self.assertTrue(app_v2.find_configured_miner("Managed")["enabled"])
            self.assertFalse(app_v2.find_configured_miner("Bitaxe403")["enabled"])
            with self.assertRaises(LookupError):
                app_v2.find_configured_miner("Remote-S21")

    def test_ambiguous_configured_benchmark_name_is_rejected(self):
        with patch.object(app_v2, "load_miners", return_value=[{"name": "Duplicate"}, {"name": "Duplicate"}]):
            with self.assertRaises(LookupError):
                app_v2.find_configured_miner("Duplicate")

    def test_management_ui_exposes_capabilities_and_canonical_pool_selector(self):
        html = (self.static / "miners.html").read_text()
        script = (self.static / "miners.js").read_text()
        for field in ('name="location_scope"', 'name="telemetry_source"', 'name="worker_name"'):
            self.assertIn(field, html)
        self.assertNotIn('name="coin"', html)
        self.assertIn('value="Umbrel Solo"', html)
        self.assertIn('value="BCH SoloPool"', html)
        self.assertIn("available_braiins_workers", script)
        self.assertIn("UNADOPTED / POOL-ONLY", script)
        self.assertIn("location_scope: ''", script)
        self.assertIn("miner.location_scope ?? 'LOCAL'", script)
        self.assertIn('<option value="">Choose location…</option>', html)


if __name__ == "__main__":
    unittest.main()
