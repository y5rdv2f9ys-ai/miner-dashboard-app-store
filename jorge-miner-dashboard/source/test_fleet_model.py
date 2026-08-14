import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_v2


def worker(name, rate=0, state="off"):
    return {"name": name, "state": state, "hash_rate_5m_th": rate,
            "hash_rate_60m_th": rate, "hash_rate_24h_th": rate}


class FleetSchemaTests(unittest.TestCase):
    def test_legacy_defaults_are_local_api_and_worker_defaults_name(self):
        item = app_v2.configured_miner({"name": "Legacy", "pool": ""})
        self.assertEqual((item["location_scope"], item["telemetry_source"], item["worker_name"]),
                         ("LOCAL", "LOCAL_API", "Legacy"))
        self.assertEqual(item["pool"], "")

    def test_validation_derives_coin_and_enforces_source_capabilities(self):
        local = app_v2.validate_miner_identity({"name": "A", "ip": "10.0.0.2", "type": "axeos",
                                                "pool": "BCH SoloPool", "location_scope": "LOCAL",
                                                "telemetry_source": "LOCAL_API"})
        self.assertEqual(local["coin"], "BCH")
        remote = app_v2.validate_miner_identity({"name": "R", "pool": "Braiins",
                                                 "location_scope": "OFF-SITE", "telemetry_source": "BRAIINS",
                                                 "worker_name": "rig.r"})
        self.assertEqual((remote["ip"], remote["type"], remote["coin"]), ("", "", "BTC"))
        with self.assertRaisesRegex(ValueError, "Pool must"):
            app_v2.validate_miner_identity({"name": "A", "ip": "10.0.0.2", "type": "axeos",
                                            "pool": "", "telemetry_source": "LOCAL_API"})
        with self.assertRaisesRegex(ValueError, "IP address"):
            app_v2.validate_miner_identity({"name": "A", "type": "axeos", "pool": "Braiins",
                                            "telemetry_source": "LOCAL_API"})
        with self.assertRaisesRegex(ValueError, "requires the Braiins pool"):
            app_v2.validate_miner_identity({"name": "R", "pool": "Umbrel Solo",
                                            "telemetry_source": "BRAIINS", "worker_name": "R",
                                            "location_scope": "LOCAL"})
        with self.assertRaisesRegex(ValueError, "explicitly selected"):
            app_v2.validate_miner_identity({"name": "R", "pool": "Braiins",
                                            "telemetry_source": "BRAIINS", "worker_name": "R"})

    def test_collection_skips_braiins_local_api_poll(self):
        miners = [
            {"name": "Local", "type": "axeos", "ip": "10.0.0.2", "pool": "Umbrel Solo"},
            {"name": "AvalonQ", "pool": "Braiins", "telemetry_source": "BRAIINS", "worker_name": "AvalonQ"},
        ]
        with patch.object(app_v2, "read_miner", return_value={"name": "Local", "online": True}) as poll:
            rows = app_v2.collect_miners(miners)
        poll.assert_called_once()
        self.assertEqual(rows[1]["telemetry_source"], "BRAIINS")
        self.assertIsNone(rows[1]["temp"])


class FleetNormalizationTests(unittest.TestCase):
    def test_local_api_location_is_authoritative_and_stale_worker_is_ignored(self):
        local = {"name": "Bitaxe403", "online": False, "status": "OFFLINE", "th": 0,
                 "pool": "BCH SoloPool", "thermal_enabled": False,
                 "location_scope": "LOCAL", "telemetry_source": "LOCAL_API"}
        fleet = app_v2.normalize_fleet([local], {"workers": [worker("bitaxe403", 1, "ok")]})
        self.assertEqual(len(fleet), 1)
        self.assertEqual(fleet[0]["location_scope"], "LOCAL")
        self.assertEqual(fleet[0]["pool"], "BCH SoloPool")
        self.assertNotIn("braiins_worker", fleet[0])
        self.assertEqual(fleet[0]["thermal_status"], "UNMANAGED")
        pools = app_v2.normalized_braiins_workers(fleet, {"workers": [worker("bitaxe403", 1, "ok")]})
        self.assertEqual(len(pools), 1)
        self.assertEqual((pools[0]["scope"], pools[0]["membership"]), ("POOL-ONLY", "UNADOPTED"))

    def test_configured_braiins_local_and_offsite_active_and_inactive(self):
        configured = [
            {"name": "AvalonQ", "pool": "Braiins", "location_scope": "LOCAL",
             "telemetry_source": "BRAIINS", "worker_name": "avalon.worker", "thermal_enabled": False},
            {"name": "BitaxeTouch", "pool": "Braiins", "location_scope": "OFF-SITE",
             "telemetry_source": "BRAIINS", "worker_name": "touch", "thermal_enabled": False},
            {"name": "RemoteIdle", "pool": "Braiins", "location_scope": "OFF-SITE",
             "telemetry_source": "BRAIINS", "worker_name": "idle", "thermal_enabled": False},
        ]
        braiins = {"workers": [worker("AVALON.WORKER", 3.2, "ok"), worker("touch", 1.1, "ok"), worker("idle")]}
        fleet = app_v2.normalize_fleet(configured, braiins)
        self.assertEqual([m["location_scope"] for m in fleet], ["LOCAL", "OFF-SITE", "OFF-SITE"])
        self.assertEqual([m["status"] for m in fleet], ["ACTIVE", "OFF-SITE", "OFF-SITE INACTIVE"])
        self.assertTrue(fleet[0]["online"])
        self.assertEqual(fleet[0]["th"], 3.2)
        self.assertTrue(all(m["management"] == "UNMANAGED" for m in fleet))
        self.assertTrue(all(m["temp"] is None for m in fleet))

    def test_unknown_workers_are_pool_only_and_not_fleet(self):
        braiins = {"workers": [worker("Stale", 0), worker("Unknown", 2, "ok")]}
        fleet = app_v2.normalize_fleet([], braiins)
        self.assertEqual(fleet, [])
        pools = app_v2.normalized_braiins_workers(fleet, braiins)
        self.assertEqual([row["scope"] for row in pools], ["POOL-ONLY", "POOL-ONLY"])
        self.assertTrue(all(row["membership"] == "UNADOPTED" for row in pools))

    def test_ambiguous_worker_mapping_is_not_guessed(self):
        configured = [
            {"name": "One", "pool": "Braiins", "telemetry_source": "BRAIINS", "worker_name": "same"},
            {"name": "Two", "pool": "Braiins", "telemetry_source": "BRAIINS", "worker_name": "SAME"},
        ]
        fleet = app_v2.normalize_fleet(configured, {"workers": [worker("same", 2, "ok")]})
        self.assertTrue(all(not m["online"] for m in fleet))
        self.assertTrue(all("braiins_worker" not in m for m in fleet))

    def test_thermal_and_benchmark_capabilities_follow_telemetry(self):
        rows = [
            {"location_scope": "LOCAL", "telemetry_source": "LOCAL_API", "management": "MANAGED", "thermal_status": "STABLE"},
            {"location_scope": "LOCAL", "telemetry_source": "BRAIINS", "management": "MANAGED", "thermal_status": "STABLE"},
        ]
        self.assertEqual(app_v2.thermal_state_counts(rows)["STABLE"], 1)
        with patch.object(app_v2, "load_miners", return_value=[{"name": "Remote", "telemetry_source": "BRAIINS", "pool": "Braiins"}]):
            with self.assertRaisesRegex(ValueError, "Local miner API"):
                app_v2.find_configured_miner("Remote")


class HistoryTests(unittest.TestCase):
    def test_hashrate_history_survives_blank_temperature(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.csv"
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["epoch", "timestamp", "miner", "th", "temp"])
                writer.writeheader()
                writer.writerow({"epoch": __import__("time").time(), "timestamp": "", "miner": "AvalonQ", "th": "3.4", "temp": ""})
            with patch.object(app_v2, "HISTORY_PATH", path), patch.object(app_v2, "load_miners", return_value=[{"name": "AvalonQ"}]):
                rows = app_v2.get_performance({"miners": []})
        self.assertEqual(rows[0]["th_60m"], 3.4)
        self.assertIsNone(rows[0]["temp_60m"])


if __name__ == "__main__":
    unittest.main()
