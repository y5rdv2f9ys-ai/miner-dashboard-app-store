import tempfile
import unittest
from pathlib import Path

import benchmark_restore


class BenchmarkRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "benchmark_restore_profiles.json"
        self.miner = {
            "name": "NOctaxe",
            "ip": "192.168.1.115",
            "type": "nerdos",
            "base_freq": 635,
            "base_volt": 1155,
            "hot_freq": 590,
            "critical_freq": 500,
            "warn_temp": 71,
            "critical_temp": 73,
            "recover_temp": 69,
        }
        self.stats = {"freq": 635, "volt": 1155}

    def tearDown(self):
        self.temp.cleanup()

    def test_save_restore_profile_persists_current_settings_and_profile(self):
        profile = benchmark_restore.save_restore_profile(
            self.path,
            "bench_001",
            self.miner,
            self.stats,
            created_at="2026-06-24T01:00:00+00:00",
        )

        self.assertEqual(profile["status"], "active")
        self.assertEqual(profile["miner"], "NOctaxe")
        self.assertEqual(profile["restore"]["frequency"], 635)
        self.assertEqual(profile["restore"]["voltage"], 1155)
        thermal = profile["restore"]["thermal_profile"]
        self.assertEqual(thermal["base_volt"], 1155)
        self.assertEqual(thermal["hot_volt"], 1155)
        self.assertEqual(thermal["critical_volt"], 1155)
        self.assertEqual(
            benchmark_restore.get_restore_profile(self.path, "bench_001"),
            profile,
        )

    def test_save_restore_profile_rejects_duplicate_session(self):
        benchmark_restore.save_restore_profile(self.path, "bench_001", self.miner, self.stats)
        with self.assertRaisesRegex(ValueError, "already exists"):
            benchmark_restore.save_restore_profile(self.path, "bench_001", self.miner, self.stats)

    def test_mark_restore_profile_updates_status(self):
        benchmark_restore.save_restore_profile(self.path, "bench_001", self.miner, self.stats)
        profile = benchmark_restore.mark_restore_profile(
            self.path,
            "bench_001",
            "restored",
            completed_at="2026-06-24T02:00:00+00:00",
            reason="completed",
        )

        self.assertEqual(profile["status"], "restored")
        self.assertEqual(profile["completed_at"], "2026-06-24T02:00:00+00:00")
        self.assertEqual(profile["reason"], "completed")

    def test_mark_restore_profile_rejects_unknown_session(self):
        with self.assertRaisesRegex(LookupError, "not found"):
            benchmark_restore.mark_restore_profile(self.path, "missing", "failed")

    def test_load_restore_profiles_handles_missing_or_invalid_files(self):
        self.assertEqual(benchmark_restore.load_restore_profiles(self.path), {})
        self.path.write_text("[]")
        self.assertEqual(benchmark_restore.load_restore_profiles(self.path), {})
        self.path.write_text("not json")
        self.assertEqual(benchmark_restore.load_restore_profiles(self.path), {})


if __name__ == "__main__":
    unittest.main()
