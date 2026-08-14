import unittest
from unittest.mock import patch

import app_v2


class DashboardCollectorTests(unittest.TestCase):
    def test_build_odds_preserves_missing_network_difficulty(self):
        miners = [{"name": "PoolMiner", "pool": "Braiins", "coin": None,
                   "online": True, "th": 1.1}]
        with patch.object(app_v2, "get_network_difficulty", return_value=None):
            odds = app_v2.build_odds(miners, {})
        self.assertIsNone(odds["Braiins"]["difficulty"])
        self.assertIsNone(odds["Braiins"]["hour_den"])
        self.assertIsNone(odds["Braiins"]["day_den"])

    def test_snapshot_handles_mixed_fleet_and_missing_difficulty(self):
        results = [
            {"name": "LocalSolo", "online": True, "th": 1.1, "temp": 62, "vr_temp": 58,
             "freq": 600, "volt": 1150, "reject": .1, "status": "STABLE",
             "thermal_status": "STABLE", "thermal_enabled": True, "pool": "Umbrel Solo",
             "coin": None, "expected_th": 1.1, "thermal_limit": 70},
            {"name": "Bitaxe403", "online": True, "th": .82, "temp": 69, "vr_temp": 64,
             "freq": 490, "volt": 1120, "reject": .3, "status": "UNMANAGED",
             "thermal_status": "UNMANAGED", "thermal_enabled": False, "pool": "Braiins",
             "coin": "BTC", "expected_th": .82, "thermal_limit": 70},
            {"name": "Moved", "online": False, "th": 0, "temp": 0, "vr_temp": -1,
             "freq": 0, "volt": 0, "reject": 0, "status": "OFFLINE",
             "thermal_status": "OFFLINE", "thermal_enabled": True, "pool": "Braiins",
             "coin": "BTC", "expected_th": 1, "thermal_limit": 70,
             "location_scope": "OFF-SITE", "telemetry_source": "BRAIINS", "worker_name": "Moved"},
            {"name": "AvalonQ", "online": False, "th": 0, "temp": None, "vr_temp": None,
             "freq": None, "volt": None, "reject": None, "status": "INACTIVE",
             "thermal_status": "UNMANAGED", "thermal_enabled": False, "pool": "Braiins",
             "coin": "BTC", "expected_th": 1, "location_scope": "LOCAL",
             "telemetry_source": "BRAIINS", "worker_name": "AvalonQ"},
        ]
        braiins = {"workers": [
            {"name": "Bitaxe403", "state": "ok", "hash_rate_5m_th": .8, "hash_rate_60m_th": .8},
            {"name": "moved", "state": "ok", "hash_rate_5m_th": 1.2, "hash_rate_60m_th": 1.1},
            {"name": "AvalonQ", "state": "off", "hash_rate_5m_th": 0, "hash_rate_60m_th": 0},
        ]}
        with patch.object(app_v2, "load_miners", return_value=[{"name": item["name"]} for item in results]), \
             patch.object(app_v2, "collect_miners", return_value=results), \
             patch.object(app_v2, "record_history"), \
             patch.object(app_v2, "update_pool_runs", return_value={}), \
             patch.object(app_v2, "fetch_solopool_stats", return_value={"blocks": []}), \
             patch.object(app_v2, "fetch_braiins_stats", return_value=braiins), \
             patch.object(app_v2, "get_network_difficulty", return_value=None), \
             patch.object(app_v2.ALERT_MANAGER, "process"):
            snapshot = app_v2.collect_dashboard_snapshot()
        self.assertEqual([miner["name"] for miner in snapshot["miners"]],
                         ["LocalSolo", "Bitaxe403", "Moved", "AvalonQ"])
        moved = next(miner for miner in snapshot["miners"] if miner["name"] == "Moved")
        self.assertEqual(moved["location_scope"], "OFF-SITE")
        self.assertEqual(moved["thermal_status"], "UNMANAGED")
        self.assertIsNone(moved["temp"])
        self.assertEqual(snapshot["thermal_counts"]["STABLE"], 1)
        self.assertEqual(snapshot["thermal_counts"]["MAX COOLING"], 0)
        self.assertIsNone(snapshot["odds"]["Umbrel Solo"]["difficulty"])


if __name__ == "__main__":
    unittest.main()
