from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import app_v2


class ThermalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "miners_v2.json"
        self.original = {
            "name": "TestMiner",
            "type": "axeos",
            "ip": "192.168.1.20",
            "pool": "Test",
            "base_freq": 600,
            "hot_freq": 575,
            "critical_freq": 550,
            "warn_temp": 68,
            "critical_temp": 70,
            "recover_temp": 64,
            "unrelated": "preserved",
        }
        self.path.write_text(json.dumps([self.original]))
        self.path_patch = patch.object(app_v2, "MINERS_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def settings(self, **overrides):
        values = {
            "name": "TestMiner",
            "enabled": True,
            "base_freq": 610,
            "hot_freq": 580,
            "critical_freq": 540,
            "warn_temp": 67,
            "critical_temp": 71,
            "recover_temp": 63.5,
        }
        values.update(overrides)
        return values

    def test_save_updates_only_thermal_fields_and_preserves_other_data(self):
        app_v2.save_thermal_settings(self.settings())
        saved = json.loads(self.path.read_text())[0]
        self.assertEqual(saved["base_freq"], 610)
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["unrelated"], "preserved")
        self.assertEqual(saved["ip"], "192.168.1.20")

    def test_frequency_order_is_validated(self):
        with self.assertRaisesRegex(ValueError, "Frequency order"):
            app_v2.save_thermal_settings(self.settings(hot_freq=620))
        self.assertEqual(json.loads(self.path.read_text())[0], self.original)

    def test_temperature_order_is_validated(self):
        with self.assertRaisesRegex(ValueError, "Temperature order"):
            app_v2.save_thermal_settings(self.settings(recover_temp=68))
        self.assertEqual(json.loads(self.path.read_text())[0], self.original)

    def test_unknown_miner_is_rejected(self):
        with self.assertRaisesRegex(LookupError, "not found"):
            app_v2.save_thermal_settings(self.settings(name="Missing"))

    def test_enabled_must_be_boolean(self):
        with self.assertRaisesRegex(ValueError, "true or false"):
            app_v2.save_thermal_settings(self.settings(enabled="yes"))

    def test_add_miner_defaults_thermal_management_to_disabled(self):
        miner = app_v2.add_miner({
            "name": "New Nerd",
            "type": "NerdOS",
            "ip": "192.168.1.115",
            "pool": "Braiins",
            "coin": "btc",
        })
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved), 2)
        self.assertEqual(miner["name"], "New Nerd")
        self.assertEqual(saved[1]["type"], "nerdos")
        self.assertEqual(saved[1]["ip"], "192.168.1.115")
        self.assertEqual(saved[1]["coin"], "BTC")
        self.assertFalse(saved[1]["enabled"])
        self.assertIn("base_freq", saved[1])

    def test_update_miner_preserves_thermal_fields(self):
        app_v2.update_miner({
            "original_name": "TestMiner",
            "name": "TestMiner",
            "type": "axeos",
            "ip": "192.168.1.115",
            "pool": "Updated",
            "coin": "bch",
        })
        saved = json.loads(self.path.read_text())[0]
        self.assertEqual(saved["ip"], "192.168.1.115")
        self.assertEqual(saved["pool"], "Updated")
        self.assertEqual(saved["coin"], "BCH")
        self.assertEqual(saved["base_freq"], 600)
        self.assertEqual(saved["unrelated"], "preserved")

    def test_duplicate_miner_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "already exists"):
            app_v2.add_miner({
                "name": "TestMiner",
                "type": "axeos",
                "ip": "192.168.1.115",
                "pool": "",
                "coin": "",
            })
        self.assertEqual(json.loads(self.path.read_text()), [self.original])

    def test_delete_miner_removes_one_entry(self):
        app_v2.add_miner({
            "name": "New Axe",
            "type": "axeos",
            "ip": "192.168.1.115",
            "pool": "",
            "coin": "",
        })
        app_v2.delete_miner({"name": "New Axe"})
        self.assertEqual(json.loads(self.path.read_text()), [self.original])

    def test_invalid_ip_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "IP address"):
            app_v2.add_miner({
                "name": "Bad Ip",
                "type": "axeos",
                "ip": "not-an-ip",
                "pool": "",
                "coin": "",
            })


if __name__ == "__main__":
    unittest.main()
