import atexit
import os
import tempfile
import unittest
from unittest.mock import patch

temp = tempfile.TemporaryDirectory()
atexit.register(temp.cleanup)
os.environ["MINER_DASHBOARD_DATA_DIR"] = temp.name

import miner_thermal_mode  # noqa: E402


class MinerThermalModeTests(unittest.TestCase):
    def test_state_profile_defaults_state_voltage_to_base_voltage(self):
        miner = {
            "base_freq": 700,
            "base_volt": 1200,
            "hot_freq": 650,
            "critical_freq": 560,
        }
        self.assertEqual(miner_thermal_mode.state_profile(miner, "hot"), (650, 1200))
        self.assertEqual(
            miner_thermal_mode.state_profile(
                {**miner, "critical_volt": 1160},
                "critical",
            ),
            (560, 1160),
        )

    def test_apply_profile_uses_shared_miner_api(self):
        miner = {"type": "nerdos", "ip": "192.168.1.115"}
        with patch.object(miner_thermal_mode, "apply_settings") as apply:
            miner_thermal_mode.apply_profile(miner, 635, 1155)
        apply.assert_called_once_with(
            miner,
            frequency=635,
            voltage=1155,
            timeout=miner_thermal_mode.REQUEST_TIMEOUT,
        )

    def test_should_skip_for_lock_skips_only_locked_miner(self):
        locks = {
            "NQaxe": {"locked_by": "benchmark", "session_id": "bench_001"},
        }
        self.assertTrue(
            miner_thermal_mode.should_skip_for_lock({"name": "NQaxe"}, locks)
        )
        self.assertFalse(
            miner_thermal_mode.should_skip_for_lock({"name": "NOctaxe"}, locks)
        )


if __name__ == "__main__":
    unittest.main()
