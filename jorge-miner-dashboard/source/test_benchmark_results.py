import json
import tempfile
import unittest
from pathlib import Path

import benchmark_results


class BenchmarkResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "benchmark_results.json"
        self.candidates = [
            {
                "sequence": 1,
                "frequency": 500,
                "voltage": 1100,
                "frequency_relation": "below_base",
                "voltage_relation": "below_base",
                "is_below_base": True,
            },
            {
                "sequence": 2,
                "frequency": 505,
                "voltage": 1100,
                "frequency_relation": "below_base",
                "voltage_relation": "below_base",
                "is_below_base": True,
            },
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_save_planned_results_persists_report_ready_rows(self):
        rows = benchmark_results.save_planned_results(
            self.path,
            "bench_001",
            self.candidates,
            created_at="2026-06-24T01:00:00+00:00",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "planned")
        self.assertIsNone(rows[0]["safety_decision"])
        self.assertEqual(rows[0]["sample_summary"]["average_hashrate_th"], None)
        self.assertTrue(rows[0]["is_below_base"])
        self.assertEqual(
            benchmark_results.session_results(self.path, "bench_001"),
            rows,
        )

    def test_save_planned_results_rejects_duplicate_session(self):
        benchmark_results.save_planned_results(self.path, "bench_001", self.candidates)

        with self.assertRaisesRegex(ValueError, "already exist"):
            benchmark_results.save_planned_results(self.path, "bench_001", self.candidates)

    def test_report_payload_contains_top_placeholders_for_planned_rows(self):
        benchmark_results.save_planned_results(self.path, "bench_001", self.candidates)
        payload = benchmark_results.report_payload(self.path, "bench_001")

        self.assertEqual(payload["session_id"], "bench_001")
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["top_hashrate"], [])
        self.assertEqual(payload["top_efficiency"], [])

    def test_update_candidate_result_updates_one_row(self):
        benchmark_results.save_planned_results(self.path, "bench_001", self.candidates)

        row = benchmark_results.update_candidate_result(
            self.path,
            "bench_001",
            2,
            {"status": "applied"},
        )

        self.assertEqual(row["sequence"], 2)
        self.assertEqual(row["status"], "applied")
        rows = benchmark_results.session_results(self.path, "bench_001")
        self.assertEqual(rows[0]["status"], "planned")
        self.assertEqual(rows[1]["status"], "applied")

    def test_report_payload_sorts_sampled_rows(self):
        rows = [
            {
                "session_id": "bench_001",
                "sequence": 1,
                "status": "sampled",
                "sample_summary": {
                    "average_hashrate_th": 2.0,
                    "efficiency_jth": 30.0,
                },
            },
            {
                "session_id": "bench_001",
                "sequence": 2,
                "status": "sampled",
                "sample_summary": {
                    "average_hashrate_th": 3.0,
                    "efficiency_jth": 20.0,
                },
            },
        ]
        self.path.write_text(json.dumps({"bench_001": rows}))

        payload = benchmark_results.report_payload(self.path, "bench_001")

        self.assertEqual(payload["top_hashrate"][0]["sequence"], 2)
        self.assertEqual(payload["top_efficiency"][0]["sequence"], 2)

    def test_export_report_includes_session_restore_and_counts(self):
        benchmark_results.save_planned_results(self.path, "bench_001", self.candidates)
        session = {
            "session_id": "bench_001",
            "miner": "TestMiner",
            "device_profile": "axeos_bitaxe",
        }
        restore = {
            "session_id": "bench_001",
            "restore": {"frequency": 600, "voltage": 1150},
        }

        report = benchmark_results.export_report(
            self.path,
            session,
            restore_profile=restore,
            profile={"id": "axeos_bitaxe"},
        )

        self.assertEqual(report["schema"], "benchmark_report_v1")
        self.assertEqual(report["session"], session)
        self.assertEqual(report["profile"]["id"], "axeos_bitaxe")
        self.assertEqual(report["restore_baseline"], {"frequency": 600, "voltage": 1150})
        self.assertEqual(report["counts"]["planned"], 2)
        self.assertEqual(report["top_hashrate"], [])

    def test_export_report_lists_aborted_canceled_and_safety_decisions(self):
        rows = [
            {
                "session_id": "bench_001",
                "sequence": 1,
                "status": "aborted",
                "safety_decision": "CHIP_TEMP_EXCEEDED",
                "sample_summary": {},
            },
            {
                "session_id": "bench_001",
                "sequence": 2,
                "status": "canceled",
                "safety_decision": "CANCELED_BY_USER",
                "sample_summary": {},
            },
        ]
        self.path.write_text(json.dumps({"bench_001": rows}))

        report = benchmark_results.export_report(
            self.path,
            {"session_id": "bench_001"},
        )

        self.assertEqual(report["counts"]["aborted"], 1)
        self.assertEqual(report["counts"]["canceled"], 1)
        self.assertEqual(report["aborted"][0]["sequence"], 1)
        self.assertEqual(report["canceled"][0]["sequence"], 2)
        self.assertEqual(
            [decision["safety_decision"] for decision in report["safety_decisions"]],
            ["CHIP_TEMP_EXCEEDED", "CANCELED_BY_USER"],
        )

    def test_load_results_handles_missing_or_invalid_files(self):
        self.assertEqual(benchmark_results.load_results(self.path), {})
        self.path.write_text("[]")
        self.assertEqual(benchmark_results.load_results(self.path), {})
        self.path.write_text("not json")
        self.assertEqual(benchmark_results.load_results(self.path), {})


if __name__ == "__main__":
    unittest.main()
