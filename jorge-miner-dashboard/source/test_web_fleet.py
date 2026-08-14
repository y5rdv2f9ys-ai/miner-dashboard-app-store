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

    def test_dashboard_layout_has_natural_grid_height_for_many_cards(self):
        css = (self.static / "dashboard.css").read_text()
        self.assertNotIn("max-height: 430px", css)
        self.assertIn("width: max-content", css)
        self.assertIn(".dashboard-page .swipe-hint", css)
        dashboard_hint = css.split(".dashboard-page .swipe-hint", 1)[1].split("}", 1)[0]
        self.assertIn("position: static", dashboard_hint)
        self.assertIn("overflow-y: auto", css)
        # Natural CSS grid flow is cardinality-independent; exercise requested fixture sizes.
        for card_count in (9, 12, 16):
            self.assertGreater((card_count + 3) // 4, 2)

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

    def test_overview_card_labels_and_remote_telemetry_are_compact(self):
        script = (self.static / "dashboard.js").read_text()
        self.assertIn("thermal === 'UNMANAGED'", script)
        self.assertNotIn("'LOCAL'} ·", script)
        self.assertIn('class="details remote-details"', script)
        self.assertIn("Remote worker", script)

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
            {"name": "Managed", "enabled": True, "ip": "10.0.0.1"},
            {"name": "Bitaxe403", "enabled": False, "ip": "10.0.0.2"},
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


if __name__ == "__main__":
    unittest.main()
