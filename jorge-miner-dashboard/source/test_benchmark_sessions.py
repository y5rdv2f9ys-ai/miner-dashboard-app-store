import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import benchmark_sessions


class BenchmarkSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "benchmark_sessions.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_create_session_persists_preparing_session(self):
        session = benchmark_sessions.create_session(
            self.path,
            "NOctaxe",
            created_at="2026-06-24T01:00:00+00:00",
            session_id="bench_001",
        )

        self.assertEqual(session["session_id"], "bench_001")
        self.assertEqual(session["miner"], "NOctaxe")
        self.assertEqual(session["state"], "preparing")
        self.assertEqual(session["mode"], "read_only_skeleton")
        self.assertEqual(
            benchmark_sessions.load_sessions(self.path)["bench_001"],
            session,
        )

    def test_create_session_rejects_second_active_session(self):
        benchmark_sessions.create_session(self.path, "NOctaxe", session_id="bench_001")
        with self.assertRaisesRegex(ValueError, "already active"):
            benchmark_sessions.create_session(self.path, "NQaxe", session_id="bench_002")

    def test_complete_read_only_session_marks_completed(self):
        benchmark_sessions.create_session(self.path, "NOctaxe", session_id="bench_001")
        session = benchmark_sessions.complete_read_only_session(
            self.path,
            "bench_001",
            updated_at="2026-06-24T02:00:00+00:00",
        )

        self.assertEqual(session["state"], "completed")
        self.assertEqual(session["completed_at"], "2026-06-24T02:00:00+00:00")
        self.assertEqual(session["steps"][0]["status"], "completed")

    def test_update_session_persists_metadata(self):
        benchmark_sessions.create_session(self.path, "NOctaxe", session_id="bench_001")
        session = benchmark_sessions.update_session(
            self.path,
            "bench_001",
            {"device_profile": "nerdos_nerdoctaxe"},
        )

        self.assertEqual(session["device_profile"], "nerdos_nerdoctaxe")
        self.assertEqual(
            benchmark_sessions.load_sessions(self.path)["bench_001"]["device_profile"],
            "nerdos_nerdoctaxe",
        )

    def test_cancel_session_marks_canceled(self):
        benchmark_sessions.create_session(self.path, "NOctaxe", session_id="bench_001")
        session = benchmark_sessions.cancel_session(
            self.path,
            "bench_001",
            updated_at="2026-06-24T02:00:00+00:00",
        )

        self.assertEqual(session["state"], "canceled")
        self.assertEqual(session["reason"], "canceled_by_user")

    def test_sessions_payload_reports_active_and_sorted_sessions(self):
        benchmark_sessions.create_session(
            self.path,
            "NOctaxe",
            created_at="2026-06-24T01:00:00+00:00",
            session_id="bench_001",
        )
        benchmark_sessions.complete_read_only_session(self.path, "bench_001")
        benchmark_sessions.create_session(
            self.path,
            "NQaxe",
            created_at="2026-06-24T03:00:00+00:00",
            session_id="bench_002",
        )

        payload = benchmark_sessions.sessions_payload(self.path)
        self.assertEqual(payload["active"]["session_id"], "bench_002")
        self.assertEqual(
            [session["session_id"] for session in payload["sessions"]],
            ["bench_002", "bench_001"],
        )

    def test_prune_terminal_sessions_keeps_recent_and_active_sessions(self):
        sessions = {
            "old_done": {
                "session_id": "old_done",
                "state": "completed",
                "completed_at": "2026-06-01T00:00:00+00:00",
            },
            "recent_done": {
                "session_id": "recent_done",
                "state": "canceled",
                "completed_at": "2026-06-25T00:00:00+00:00",
            },
            "active_old": {
                "session_id": "active_old",
                "state": "benchmarking",
                "updated_at": "2026-06-01T00:00:00+00:00",
            },
        }
        benchmark_sessions.write_sessions(self.path, sessions)

        pruned = benchmark_sessions.prune_terminal_sessions(
            self.path,
            now=datetime(2026, 6, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(pruned, ["old_done"])
        remaining = benchmark_sessions.load_sessions(self.path)
        self.assertNotIn("old_done", remaining)
        self.assertIn("recent_done", remaining)
        self.assertIn("active_old", remaining)


if __name__ == "__main__":
    unittest.main()
