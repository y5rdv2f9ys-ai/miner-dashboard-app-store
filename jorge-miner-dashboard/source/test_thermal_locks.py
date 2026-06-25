import json
import tempfile
import unittest
from pathlib import Path

import thermal_locks


class ThermalLocksTests(unittest.TestCase):
    def test_load_locks_returns_empty_for_missing_or_invalid_files(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "thermal_locks.json"
            self.assertEqual(thermal_locks.load_locks(missing), {})

            invalid = Path(temp) / "invalid.json"
            invalid.write_text("not json")
            self.assertEqual(thermal_locks.load_locks(invalid), {})

            not_object = Path(temp) / "list.json"
            not_object.write_text("[]")
            self.assertEqual(thermal_locks.load_locks(not_object), {})

    def test_active_lock_requires_locked_by(self):
        locks = {
            "NQaxe": {"locked_by": "benchmark", "session_id": "bench_001"},
            "NOctaxe": {"session_id": "bench_002"},
        }
        self.assertEqual(thermal_locks.active_lock_for("NQaxe", locks), locks["NQaxe"])
        self.assertIsNone(thermal_locks.active_lock_for("NOctaxe", locks))
        self.assertIsNone(thermal_locks.active_lock_for("Missing", locks))

    def test_lock_reason_includes_session_when_present(self):
        self.assertEqual(
            thermal_locks.lock_reason({"locked_by": "benchmark", "session_id": "bench_001"}),
            "benchmark:bench_001",
        )
        self.assertEqual(
            thermal_locks.lock_reason({"locked_by": "benchmark"}),
            "benchmark",
        )

    def test_load_locks_reads_json_object(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "thermal_locks.json"
            payload = {"NQaxe": {"locked_by": "benchmark"}}
            path.write_text(json.dumps(payload))
            self.assertEqual(thermal_locks.load_locks(path), payload)

    def test_create_lock_persists_active_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "thermal_locks.json"
            lock = thermal_locks.create_lock(
                path,
                "NQaxe",
                locked_by="benchmark",
                session_id="bench_001",
                created_at="2026-06-24T01:00:00+00:00",
            )

            self.assertEqual(lock["locked_by"], "benchmark")
            self.assertEqual(lock["session_id"], "bench_001")
            self.assertEqual(
                thermal_locks.active_lock_for("NQaxe", thermal_locks.load_locks(path)),
                lock,
            )

    def test_create_lock_rejects_existing_active_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "thermal_locks.json"
            thermal_locks.create_lock(path, "NQaxe", locked_by="benchmark")

            with self.assertRaisesRegex(ValueError, "already has"):
                thermal_locks.create_lock(path, "NQaxe", locked_by="benchmark")

    def test_release_lock_removes_matching_lock_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "thermal_locks.json"
            thermal_locks.create_lock(
                path,
                "NQaxe",
                locked_by="benchmark",
                session_id="bench_001",
            )

            self.assertFalse(
                thermal_locks.release_lock(path, "NQaxe", session_id="bench_002")
            )
            self.assertIn("NQaxe", thermal_locks.load_locks(path))
            self.assertTrue(
                thermal_locks.release_lock(path, "NQaxe", session_id="bench_001")
            )
            self.assertEqual(thermal_locks.load_locks(path), {})


if __name__ == "__main__":
    unittest.main()
