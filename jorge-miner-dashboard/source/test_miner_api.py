import unittest
from unittest.mock import patch

import miner_api


class MinerApiTests(unittest.TestCase):
    def test_settings_payload_uses_device_voltage_shape(self):
        self.assertEqual(
            miner_api.settings_payload("axeos", frequency=600, voltage=1150),
            {"frequency": 600, "coreVoltage": 1150},
        )
        self.assertEqual(
            miner_api.settings_payload("nerdos", frequency=700, voltage=1220),
            {"frequency": 700, "coreVoltage": 1220},
        )

    def test_apply_settings_patches_system_endpoint(self):
        miner = {"type": "nerdos", "ip": "192.168.1.115"}
        with patch.object(miner_api, "request_json", return_value={"ok": True}) as request:
            result = miner_api.apply_settings(miner, frequency=635, voltage=1155, timeout=3)

        self.assertEqual(result, {"ok": True})
        request.assert_called_once_with(
            "http://192.168.1.115/api/system",
            method="PATCH",
            payload={"frequency": 635, "coreVoltage": 1155},
            timeout=3,
        )

    def test_normalized_stats_keeps_core_and_input_voltage_separate(self):
        miner = {"type": "nerdos", "ip": "192.0.2.10"}
        data = {
            "temp": 55,
            "frequency": 635,
            "coreVoltage": 1155,
            "voltage": 11906.25,
            "hashRate": 9000,
        }

        with patch.object(miner_api, "get_system_info", return_value=data):
            result = miner_api.normalized_stats(miner, timeout=3)

        self.assertEqual(result["volt"], 1155)
        self.assertEqual(result["input_voltage"], 11.90625)
        self.assertEqual(result["voltage"], 11906.25)

    def test_normalized_stats_preserves_raw_fields(self):
        miner = {"type": "axeos", "ip": "192.168.1.26"}
        info = {
            "temp": 61.5,
            "frequency": 600,
            "coreVoltage": 1150,
            "hashRate": 1200,
            "sharesAccepted": 10,
            "sharesRejected": 1,
            "bestDiff": "42G",
        }
        with patch.object(miner_api, "get_system_info", return_value=info):
            stats = miner_api.normalized_stats(miner, timeout=3)

        self.assertEqual(stats["temp"], 61.5)
        self.assertEqual(stats["freq"], 600)
        self.assertEqual(stats["volt"], 1150)
        self.assertEqual(stats["th"], 1.2)
        self.assertAlmostEqual(stats["reject"], 9.0909090909)
        self.assertEqual(stats["bestDiff"], "42G")

    def test_normalized_stats_keeps_normalized_fields_over_raw_strings(self):
        miner = {"type": "axeos", "ip": "192.168.1.26"}
        info = {
            "temp": "61.5",
            "frequency": "600",
            "coreVoltage": "1150",
        }
        with patch.object(miner_api, "get_system_info", return_value=info):
            stats = miner_api.normalized_stats(miner, timeout=3)

        self.assertEqual(stats["temp"], 61.5)
        self.assertEqual(stats["freq"], 600)
        self.assertEqual(stats["volt"], 1150)

    def test_normalized_stats_rejects_corrupt_axeos_domain_counters(self):
        miner = {"type": "axeos", "ip": "192.0.2.85"}
        info = {
            "temp": 70.75,
            "frequency": 675,
            "coreVoltage": 1050,
            "hashRate": 244139.6875,
            "hashRate_1m": 294495.90625,
            "expectedHashrate": 1377,
            "hashrateMonitor": {"asics": [{
                "domains": [353.046, 350.469, 227731.1875, 15657.733],
            }]},
        }
        with patch.object(miner_api, "get_system_info", return_value=info):
            stats = miner_api.normalized_stats(miner, timeout=3)

        self.assertAlmostEqual(stats["th"], 1.40703, places=4)
        self.assertEqual(miner_api.get_hashrate_th(stats), stats["th"])

    def test_normalized_stats_keeps_zero_for_real_axeos_power_fault(self):
        miner = {"type": "axeos", "ip": "192.0.2.66"}
        info = {
            "temp": 37,
            "frequency": 550,
            "coreVoltage": 950,
            "hashRate": 0,
            "hashRate_1m": 0,
            "expectedHashrate": 1122,
            "power_fault": "Power Fault Detected.",
        }
        with patch.object(miner_api, "get_system_info", return_value=info):
            stats = miner_api.normalized_stats(miner, timeout=3)

        self.assertEqual(stats["th"], 0.0)


if __name__ == "__main__":
    unittest.main()
