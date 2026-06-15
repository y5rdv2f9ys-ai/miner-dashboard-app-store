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


if __name__ == "__main__":
    unittest.main()
