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
        self.benchmark_path = Path(self.temp.name) / "benchmark_sessions.json"
        self.restore_path = Path(self.temp.name) / "benchmark_restore_profiles.json"
        self.results_path = Path(self.temp.name) / "benchmark_results.json"
        self.locks_path = Path(self.temp.name) / "thermal_locks.json"
        self.original = {
            "name": "TestMiner",
            "type": "axeos",
            "ip": "192.168.1.20",
            "pool": "Test",
            "base_freq": 600,
            "base_volt": 1150,
            "hot_freq": 575,
            "hot_volt": 1150,
            "critical_freq": 550,
            "critical_volt": 1150,
            "warn_temp": 68,
            "critical_temp": 70,
            "recover_temp": 64,
            "unrelated": "preserved",
        }
        self.path.write_text(json.dumps([self.original]))
        self.path_patch = patch.object(app_v2, "MINERS_PATH", self.path)
        self.benchmark_path_patch = patch.object(
            app_v2,
            "BENCHMARK_SESSIONS_PATH",
            self.benchmark_path,
        )
        self.restore_path_patch = patch.object(
            app_v2,
            "BENCHMARK_RESTORE_PATH",
            self.restore_path,
        )
        self.results_path_patch = patch.object(
            app_v2,
            "BENCHMARK_RESULTS_PATH",
            self.results_path,
        )
        self.locks_path_patch = patch.object(
            app_v2,
            "THERMAL_LOCKS_PATH",
            self.locks_path,
        )
        self.path_patch.start()
        self.benchmark_path_patch.start()
        self.restore_path_patch.start()
        self.results_path_patch.start()
        self.locks_path_patch.start()
        app_v2.BENCHMARK_RUNNERS.clear()

    def tearDown(self):
        self.path_patch.stop()
        self.benchmark_path_patch.stop()
        self.restore_path_patch.stop()
        self.results_path_patch.stop()
        self.locks_path_patch.stop()
        app_v2.BENCHMARK_RUNNERS.clear()
        self.temp.cleanup()

    def settings(self, **overrides):
        values = {
            "name": "TestMiner",
            "enabled": True,
            "base_freq": 610,
            "base_volt": 1160,
            "hot_freq": 580,
            "hot_volt": 1160,
            "critical_freq": 540,
            "critical_volt": 1160,
            "warn_temp": 67,
            "critical_temp": 71,
            "recover_temp": 63.5,
        }
        values.update(overrides)
        return values

    def interrupted_session(self, state, **overrides):
        session = {
            "session_id": "bench_001",
            "miner": "TestMiner",
            "state": state,
            "created_at": "2026-06-24T01:00:00+00:00",
            "updated_at": "2026-06-24T01:00:00+00:00",
            "completed_at": None,
            "mode": "read_only_skeleton",
            "steps": [],
        }
        session.update(overrides)
        self.benchmark_path.write_text(json.dumps({"bench_001": session}))
        return session

    def restore_profile(self, session_id="bench_001"):
        profile = app_v2.benchmark_restore.build_restore_profile(
            session_id,
            self.original,
            {"freq": 600, "volt": 1150},
            created_at="2026-06-24T01:00:00+00:00",
        )
        self.restore_path.write_text(json.dumps({session_id: profile}))
        return profile

    def thermal_lock(self, session_id="bench_001"):
        self.locks_path.write_text(json.dumps({
            "TestMiner": {
                "locked_by": "benchmark",
                "session_id": session_id,
                "created_at": "2026-06-24T01:00:00+00:00",
            }
        }))

    def planned_candidate_results(self, session_id="bench_001", **overrides):
        candidate = {
            "sequence": 1,
            "frequency": 400,
            "voltage": 1050,
            "frequency_relation": "below_base",
            "voltage_relation": "below_base",
            "is_below_base": True,
        }
        candidate.update(overrides)
        app_v2.benchmark_results.save_planned_results(
            self.results_path,
            session_id,
            [candidate],
            created_at="2026-06-24T01:00:00+00:00",
        )
        return candidate

    def guarded_write_fixture(self, **session_overrides):
        self.interrupted_session(
            "benchmarking",
            device_profile="axeos_bitaxe",
            **session_overrides,
        )
        self.restore_profile()
        self.thermal_lock()
        self.planned_candidate_results()

    def test_save_updates_only_thermal_fields_and_preserves_other_data(self):
        app_v2.save_thermal_settings(self.settings())
        saved = json.loads(self.path.read_text())[0]
        self.assertEqual(saved["base_freq"], 610)
        self.assertEqual(saved["base_volt"], 1160)
        self.assertEqual(saved["hot_volt"], 1160)
        self.assertEqual(saved["critical_volt"], 1160)
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["unrelated"], "preserved")
        self.assertEqual(saved["ip"], "192.168.1.20")

    def test_missing_state_voltages_default_to_base_voltage(self):
        settings = self.settings()
        settings.pop("hot_volt")
        settings.pop("critical_volt")
        app_v2.save_thermal_settings(settings)
        saved = json.loads(self.path.read_text())[0]
        self.assertEqual(saved["base_volt"], 1160)
        self.assertEqual(saved["hot_volt"], 1160)
        self.assertEqual(saved["critical_volt"], 1160)

    def test_voltage_fields_are_validated(self):
        with self.assertRaisesRegex(ValueError, "hot_volt"):
            app_v2.save_thermal_settings(self.settings(hot_volt=0))
        self.assertEqual(json.loads(self.path.read_text())[0], self.original)

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
        self.assertEqual(saved[1]["hot_volt"], saved[1]["base_volt"])
        self.assertEqual(saved[1]["critical_volt"], saved[1]["base_volt"])

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

    def test_start_benchmark_session_creates_read_only_completed_session(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            return_value={"freq": 600, "volt": 1150, "temp": 61.5},
        ):
            session = app_v2.start_benchmark_session({"miner": "TestMiner"})

        self.assertEqual(session["miner"], "TestMiner")
        self.assertEqual(session["state"], "completed")
        self.assertEqual(session["mode"], "read_only_skeleton")
        self.assertIn("No miner settings were changed", session["steps"][0]["notes"])
        profiles = json.loads(self.restore_path.read_text())
        profile = profiles[session["session_id"]]
        self.assertEqual(profile["status"], "restored")
        self.assertEqual(profile["restore"]["frequency"], 600)
        self.assertEqual(profile["restore"]["voltage"], 1150)
        saved_session = json.loads(self.benchmark_path.read_text())[session["session_id"]]
        self.assertEqual(saved_session["device_profile"], "axeos_bitaxe")
        self.assertEqual(saved_session["device_profile_label"], "AxeOS / Bitaxe")
        plan = saved_session["benchmark_plan"]
        self.assertEqual(plan["mode"], "dry_run")
        self.assertFalse(plan["writes_enabled"])
        self.assertGreater(plan["candidate_count"], 0)
        self.assertEqual(plan["timing"]["candidate_seconds"], 780)
        self.assertTrue(plan["first_candidate"]["is_below_base"])
        self.assertLess(plan["first_candidate"]["frequency"], self.original["base_freq"])
        self.assertLess(plan["first_candidate"]["voltage"], self.original["base_volt"])
        planned = json.loads(self.results_path.read_text())[session["session_id"]]
        self.assertEqual(len(planned), plan["candidate_count"])
        self.assertEqual(planned[0]["status"], "planned")
        self.assertEqual(planned[0]["frequency"], plan["first_candidate"]["frequency"])
        self.assertEqual(planned[0]["sample_summary"]["efficiency_jth"], None)
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_start_benchmark_session_rejects_unknown_miner(self):
        with self.assertRaisesRegex(LookupError, "not found"):
            app_v2.start_benchmark_session({"miner": "Missing"})

    def test_start_benchmark_session_captures_restore_and_lock_before_completion(self):
        events = []

        def save_restore(*args, **kwargs):
            events.append("restore")

        def create_lock(*args, **kwargs):
            events.append("lock")

        def complete_session(path, session_id):
            events.append("complete")
            return {
                "session_id": session_id,
                "miner": "TestMiner",
                "state": "completed",
                "completed_at": "2026-06-24T02:00:00+00:00",
                "steps": [],
            }

        with patch.object(
            app_v2,
            "normalized_stats",
            return_value={"freq": 600, "volt": 1150, "temp": 61.5},
        ), patch.object(
            app_v2.benchmark_restore,
            "save_restore_profile",
            side_effect=save_restore,
        ), patch.object(
            app_v2.thermal_locks,
            "create_lock",
            side_effect=create_lock,
        ), patch.object(
            app_v2.benchmark_sessions,
            "complete_read_only_session",
            side_effect=complete_session,
        ), patch.object(
            app_v2.benchmark_restore,
            "mark_restore_profile",
        ), patch.object(
            app_v2.thermal_locks,
            "release_lock",
        ):
            app_v2.start_benchmark_session({"miner": "TestMiner"})

        self.assertEqual(events, ["restore", "lock", "complete"])

    def test_start_benchmark_session_failure_marks_session_failed(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            side_effect=RuntimeError("stats unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "stats unavailable"):
                app_v2.start_benchmark_session({"miner": "TestMiner"})

        sessions = json.loads(self.benchmark_path.read_text())
        session = next(iter(sessions.values()))
        self.assertEqual(session["state"], "failed")
        self.assertIn("stats unavailable", session["reason"])

    def test_start_benchmark_session_persists_results_without_setting_writes(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            return_value={"freq": 600, "volt": 1150, "temp": 61.5},
        ), patch.object(app_v2, "apply_settings") as apply:
            session = app_v2.start_benchmark_session({"miner": "TestMiner"})

        apply.assert_not_called()
        planned = json.loads(self.results_path.read_text())[session["session_id"]]
        self.assertGreater(len(planned), 0)
        self.assertTrue(all(row["status"] == "planned" for row in planned))

    def test_prepare_benchmark_session_creates_active_runnable_session(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            return_value={"freq": 600, "volt": 1150, "temp": 61.5},
        ), patch.object(app_v2, "apply_settings") as apply:
            session = app_v2.prepare_benchmark_session({"miner": "TestMiner"})

        apply.assert_not_called()
        self.assertEqual(session["state"], "benchmarking")
        self.assertEqual(session["mode"], "active_prepare")
        self.assertEqual(session["reason"], "prepared_for_manual_candidate_runs")
        self.assertEqual(session["device_profile"], "axeos_bitaxe")
        profiles = json.loads(self.restore_path.read_text())
        self.assertEqual(profiles[session["session_id"]]["status"], "active")
        locks = json.loads(self.locks_path.read_text())
        self.assertEqual(locks["TestMiner"]["session_id"], session["session_id"])
        planned = json.loads(self.results_path.read_text())[session["session_id"]]
        self.assertEqual(len(planned), session["benchmark_plan"]["candidate_count"])
        self.assertEqual(planned[0]["status"], "planned")
        self.assertEqual(session["benchmark_plan"]["timing"]["candidate_seconds"], 780)

    def test_prepare_benchmark_session_allows_run_candidate_route(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            return_value={"freq": 600, "volt": 1150, "temp": 61.5},
        ):
            session = app_v2.prepare_benchmark_session({"miner": "TestMiner"})
        samples = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20, "vr_temp": 65},
            {"temp": 61, "th": 1.1, "voltage": 5.0, "power": 21, "vr_temp": 66},
        ])

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}):
            result = app_v2.sample_benchmark_candidate(
                session["session_id"],
                1,
                sample_provider=lambda: next(samples),
                sleep_fn=lambda seconds: None,
                max_samples=1,
            )

        self.assertTrue(result["ok"])
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            session["session_id"],
            1,
        )
        self.assertEqual(row["status"], "sampled")

    def test_prepare_benchmark_session_failure_marks_session_failed(self):
        with patch.object(
            app_v2,
            "normalized_stats",
            side_effect=RuntimeError("stats unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "stats unavailable"):
                app_v2.prepare_benchmark_session({"miner": "TestMiner"})

        session = next(iter(json.loads(self.benchmark_path.read_text()).values()))
        self.assertEqual(session["state"], "failed")
        self.assertIn("stats unavailable", session["reason"])

    def test_cancel_active_benchmark_session_before_write_releases_lock(self):
        self.guarded_write_fixture()

        with patch.object(app_v2, "apply_settings") as apply:
            result = app_v2.cancel_active_benchmark_session({"session_id": "bench_001"})

        apply.assert_not_called()
        self.assertFalse(result["restored"])
        self.assertEqual(result["session"]["state"], "canceled")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "canceled")
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "canceled")
        self.assertEqual(row["safety_decision"], "CANCELED_BY_USER")

    def test_cancel_active_benchmark_session_after_write_restores_settings(self):
        self.guarded_write_fixture(settings_written=True)

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            result = app_v2.cancel_active_benchmark_session({"session_id": "bench_001"})

        self.assertTrue(result["restored"])
        apply.assert_called_once_with(
            {"name": "TestMiner", "ip": "192.168.1.20", "type": "axeos"},
            frequency=600,
            voltage=1150,
            timeout=app_v2.REQUEST_TIMEOUT,
        )
        self.assertEqual(result["session"]["state"], "canceled")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "restored")

    def test_cancel_active_benchmark_session_after_sample_keeps_sampled_result(self):
        self.guarded_write_fixture(settings_written=True)
        app_v2.benchmark_results.update_candidate_result(
            self.results_path,
            "bench_001",
            1,
            {"status": "sampled", "safety_decision": None},
        )

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}):
            result = app_v2.cancel_active_benchmark_session({"session_id": "bench_001"})

        self.assertTrue(result["restored"])
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "sampled")
        self.assertIsNone(row["safety_decision"])

    def test_cancel_active_benchmark_session_after_write_requires_restore_profile(self):
        self.interrupted_session(
            "benchmarking",
            device_profile="axeos_bitaxe",
            settings_written=True,
        )
        self.thermal_lock()
        self.planned_candidate_results()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(LookupError, "Restore profile"):
                app_v2.cancel_active_benchmark_session({"session_id": "bench_001"})

        apply.assert_not_called()
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "benchmarking")

    def test_benchmark_report_payload_includes_session_profile_restore_and_results(self):
        self.guarded_write_fixture()

        report = app_v2.benchmark_report_payload("bench_001")

        self.assertEqual(report["schema"], "benchmark_report_v1")
        self.assertEqual(report["session"]["session_id"], "bench_001")
        self.assertEqual(report["profile"]["id"], "axeos_bitaxe")
        self.assertEqual(report["restore_baseline"]["frequency"], 600)
        self.assertEqual(report["counts"]["planned"], 1)
        self.assertEqual(report["results"][0]["sequence"], 1)

    def test_benchmark_report_payload_rejects_missing_session(self):
        with self.assertRaisesRegex(LookupError, "not found"):
            app_v2.benchmark_report_payload("missing")

    def test_recover_benchmark_sessions_no_active_session(self):
        self.assertEqual(
            app_v2.recover_benchmark_sessions(),
            {"recovered": False, "reason": "no_active_session"},
        )

    def test_recover_benchmark_sessions_marks_preparing_failed_and_releases_lock(self):
        self.interrupted_session("preparing")
        self.restore_profile()
        self.thermal_lock()

        recovery = app_v2.recover_benchmark_sessions()

        self.assertTrue(recovery["recovered"])
        sessions = json.loads(self.benchmark_path.read_text())
        session = sessions["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("recovered_after_restart_from_preparing", session["reason"])
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "failed")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_recover_benchmark_sessions_marks_canceling_canceled(self):
        self.interrupted_session("canceling")
        self.restore_profile()
        self.thermal_lock()

        recovery = app_v2.recover_benchmark_sessions()

        self.assertEqual(recovery["state"], "canceled")
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "canceled")
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "canceled")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_recover_benchmark_sessions_marks_benchmarking_failed(self):
        self.interrupted_session("benchmarking")
        self.restore_profile()
        self.thermal_lock()

        recovery = app_v2.recover_benchmark_sessions()

        self.assertEqual(recovery["state"], "failed")
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("benchmarking", session["reason"])
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_recover_benchmark_sessions_marks_restoring_failed(self):
        self.interrupted_session("restoring")
        self.restore_profile()
        self.thermal_lock()

        recovery = app_v2.recover_benchmark_sessions()

        self.assertEqual(recovery["state"], "failed")
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("restoring", session["reason"])
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_recover_benchmark_sessions_restores_when_settings_were_written(self):
        self.interrupted_session("benchmarking", settings_written=True)
        self.restore_profile()
        self.thermal_lock()

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            recovery = app_v2.recover_benchmark_sessions()

        self.assertTrue(recovery["restored_settings"])
        apply.assert_called_once_with(
            {"name": "TestMiner", "ip": "192.168.1.20", "type": "axeos"},
            frequency=600,
            voltage=1150,
            timeout=app_v2.REQUEST_TIMEOUT,
        )
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "restored")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_recover_benchmark_sessions_preserves_lock_when_restore_fails(self):
        self.interrupted_session("benchmarking", settings_written=True)
        self.restore_profile()
        self.thermal_lock()

        with patch.object(app_v2, "apply_settings", side_effect=TimeoutError("offline")):
            recovery = app_v2.recover_benchmark_sessions()

        self.assertEqual(recovery["reason"], "manual_cleanup_required")
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertTrue(session["recovery_required"])
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "failed")
        self.assertTrue(profile["recovery_required"])
        self.assertIn("offline", profile["last_restore_error"])
        self.assertIn("TestMiner", json.loads(self.locks_path.read_text()))

        second_recovery = app_v2.recover_benchmark_sessions()
        self.assertEqual(second_recovery["reason"], "manual_cleanup_required")
        self.assertEqual(second_recovery["session_id"], "bench_001")

    def test_benchmark_status_payload_includes_profiles(self):
        payload = app_v2.benchmark_status_payload()

        self.assertIn("sessions", payload)
        self.assertIn("profiles", payload)
        self.assertIn("results", payload)
        self.assertIn("runner", payload)
        self.assertIsNone(payload["active_results"])
        self.assertIn(
            "axeos_bitaxe",
            [profile["id"] for profile in payload["profiles"]],
        )

    def test_cleanup_benchmark_reports_prunes_matching_result_and_restore_records(self):
        old_session = {
            "session_id": "old_done",
            "miner": "TestMiner",
            "state": "completed",
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T01:00:00+00:00",
            "completed_at": "2026-06-01T01:00:00+00:00",
        }
        recent_session = {
            "session_id": "recent_done",
            "miner": "TestMiner",
            "state": "canceled",
            "created_at": "2026-06-25T00:00:00+00:00",
            "updated_at": "2026-06-25T01:00:00+00:00",
            "completed_at": "2026-06-25T01:00:00+00:00",
        }
        app_v2.benchmark_sessions.write_sessions(
            self.benchmark_path,
            {
                "old_done": old_session,
                "recent_done": recent_session,
            },
        )
        app_v2.benchmark_results.write_results(
            self.results_path,
            {
                "old_done": [{"session_id": "old_done"}],
                "recent_done": [{"session_id": "recent_done"}],
            },
        )
        app_v2.benchmark_restore.write_restore_profiles(
            self.restore_path,
            {
                "old_done": {"session_id": "old_done"},
                "recent_done": {"session_id": "recent_done"},
            },
        )

        with patch.object(
            app_v2.benchmark_sessions,
            "prune_terminal_sessions",
            return_value=["old_done"],
        ):
            result = app_v2.cleanup_benchmark_reports()

        self.assertEqual(result["pruned"], ["old_done"])
        self.assertNotIn("old_done", json.loads(self.results_path.read_text()))
        self.assertNotIn("old_done", json.loads(self.restore_path.read_text()))
        self.assertIn("recent_done", json.loads(self.results_path.read_text()))
        self.assertIn("recent_done", json.loads(self.restore_path.read_text()))

    def test_guarded_benchmark_setting_write_requires_active_session(self):
        self.interrupted_session("completed", device_profile="axeos_bitaxe")
        self.restore_profile()
        self.thermal_lock()
        self.planned_candidate_results()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "not active"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 60, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()

    def test_guarded_benchmark_setting_write_requires_restore_profile(self):
        self.interrupted_session("benchmarking", device_profile="axeos_bitaxe")
        self.thermal_lock()
        self.planned_candidate_results()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(LookupError, "Restore profile"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 60, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()

    def test_guarded_benchmark_setting_write_requires_matching_thermal_lock(self):
        self.interrupted_session("benchmarking", device_profile="axeos_bitaxe")
        self.restore_profile()
        self.thermal_lock(session_id="bench_002")
        self.planned_candidate_results()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "another session"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 60, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()

    def test_guarded_benchmark_setting_write_requires_candidate_result(self):
        self.interrupted_session("benchmarking", device_profile="axeos_bitaxe")
        self.restore_profile()
        self.thermal_lock()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(LookupError, "candidate"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 60, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()

    def test_guarded_benchmark_setting_write_validates_candidate_profile_range(self):
        self.interrupted_session("benchmarking", device_profile="axeos_bitaxe")
        self.restore_profile()
        self.thermal_lock()
        self.planned_candidate_results(frequency=675)

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "Frequency"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 60, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()

    def test_guarded_benchmark_setting_write_blocks_unsafe_sample_and_marks_result(self):
        self.guarded_write_fixture()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "CHIP_TEMP_EXCEEDED"):
                app_v2.guarded_benchmark_setting_write(
                    "bench_001",
                    1,
                    {"temp": 69, "th": 1.0, "voltage": 5.0},
                )

        apply.assert_not_called()
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "aborted")
        self.assertEqual(row["safety_decision"], "CHIP_TEMP_EXCEEDED")

    def test_guarded_benchmark_setting_write_marks_settings_written_after_success(self):
        self.guarded_write_fixture()

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            result = app_v2.guarded_benchmark_setting_write(
                "bench_001",
                1,
                {"temp": 60, "th": 1.0, "voltage": 5.0},
            )

        self.assertTrue(result["ok"])
        apply.assert_called_once_with(
            {"name": "TestMiner", "ip": "192.168.1.20", "type": "axeos"},
            frequency=400,
            voltage=1050,
            timeout=app_v2.REQUEST_TIMEOUT,
        )
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertTrue(session["settings_written"])
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "applied")
        self.assertIsNotNone(row["applied_at"])

    def test_sample_benchmark_candidate_updates_sampled_result(self):
        self.guarded_write_fixture()
        samples = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20, "vr_temp": 65},
            {"temp": 61, "th": 1.1, "voltage": 5.0, "power": 21, "vr_temp": 66},
            {"temp": 62, "th": 1.3, "voltage": 5.0, "power": 23, "vr_temp": 68},
        ])

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            result = app_v2.sample_benchmark_candidate(
                "bench_001",
                1,
                sample_provider=lambda: next(samples),
                sleep_fn=lambda seconds: None,
                max_samples=2,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["samples"], 2)
        apply.assert_called_once()
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "sampled")
        self.assertIsNone(row["safety_decision"])
        self.assertAlmostEqual(row["sample_summary"]["average_hashrate_th"], 1.2)
        self.assertEqual(row["sample_summary"]["average_temp"], 61.5)

    def test_sample_benchmark_candidate_tolerates_transient_api_failures_and_resets_count(self):
        self.guarded_write_fixture()
        outcomes = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20},
            TimeoutError("first timeout"),
            TimeoutError("second timeout"),
            {"temp": 61, "th": 1.1, "voltage": 5.0, "power": 21},
            TimeoutError("third timeout"),
            TimeoutError("fourth timeout"),
        ])

        def sample_provider():
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}):
            result = app_v2.sample_benchmark_candidate(
                "bench_001",
                1,
                sample_provider=sample_provider,
                sleep_fn=lambda seconds: None,
                max_samples=5,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["samples"], 1)
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "benchmarking")
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "sampled")
        self.assertIsNone(row["safety_decision"])

    def test_sample_benchmark_candidate_aborts_at_consecutive_api_failure_limit(self):
        self.guarded_write_fixture()
        outcomes = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20},
            TimeoutError("first timeout"),
            TimeoutError("second timeout"),
            TimeoutError("third timeout"),
        ])

        def sample_provider():
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}):
            with self.assertRaisesRegex(ValueError, "API_FAILURE_LIMIT"):
                app_v2.sample_benchmark_candidate(
                    "bench_001",
                    1,
                    sample_provider=sample_provider,
                    sleep_fn=lambda seconds: None,
                    max_samples=3,
                )

        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("API_FAILURE_LIMIT", session["reason"])
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "aborted")
        self.assertEqual(row["safety_decision"], "API_FAILURE_LIMIT")

    def test_sample_benchmark_candidate_restores_and_fails_session_on_abort(self):
        self.guarded_write_fixture()
        samples = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20, "vr_temp": 65},
            {"temp": 69, "th": 1.1, "voltage": 5.0, "power": 21, "vr_temp": 66},
        ])

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            with self.assertRaisesRegex(ValueError, "CHIP_TEMP_EXCEEDED"):
                app_v2.sample_benchmark_candidate(
                    "bench_001",
                    1,
                    sample_provider=lambda: next(samples),
                    sleep_fn=lambda seconds: None,
                    max_samples=1,
                )

        self.assertEqual(apply.call_count, 2)
        self.assertEqual(apply.call_args_list[0].kwargs["frequency"], 400)
        self.assertEqual(apply.call_args_list[0].kwargs["voltage"], 1050)
        self.assertEqual(apply.call_args_list[1].kwargs["frequency"], 600)
        self.assertEqual(apply.call_args_list[1].kwargs["voltage"], 1150)
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("CHIP_TEMP_EXCEEDED", session["reason"])
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["status"], "aborted")
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(profile["status"], "restored")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_sample_benchmark_candidate_restore_failure_requires_recovery_and_keeps_lock(self):
        self.guarded_write_fixture()
        samples = iter([
            {"temp": 60, "th": 1.0, "voltage": 5.0, "power": 20},
            {"temp": 69, "th": 1.1, "voltage": 5.0, "power": 21},
        ])

        with patch.object(
            app_v2,
            "apply_settings",
            side_effect=[{"ok": True}, TimeoutError("restore timeout")],
        ):
            with self.assertRaisesRegex(ValueError, "CHIP_TEMP_EXCEEDED"):
                app_v2.sample_benchmark_candidate(
                    "bench_001",
                    1,
                    sample_provider=lambda: next(samples),
                    sleep_fn=lambda seconds: None,
                    max_samples=1,
                )

        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertTrue(session["recovery_required"])
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertTrue(profile["recovery_required"])
        self.assertIn("TestMiner", json.loads(self.locks_path.read_text()))

    def test_retry_benchmark_restore_releases_lock_after_success(self):
        self.guarded_write_fixture(settings_written=True)
        app_v2.mark_benchmark_restore_recovery_required(
            "bench_001", TimeoutError("offline"), "candidate_aborted"
        )

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            result = app_v2.retry_benchmark_restore({"session_id": "bench_001"})

        apply.assert_called_once()
        self.assertTrue(result["lock_released"])
        self.assertEqual(json.loads(self.locks_path.read_text()), {})
        profile = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertFalse(profile["recovery_required"])
        self.assertEqual(profile["status"], "restored")

    def test_manual_restore_confirmation_releases_matching_lock(self):
        self.guarded_write_fixture(settings_written=True)
        app_v2.mark_benchmark_restore_recovery_required(
            "bench_001", TimeoutError("offline"), "candidate_aborted"
        )

        result = app_v2.confirm_manual_benchmark_restore({"session_id": "bench_001"})

        self.assertTrue(result["lock_released"])
        self.assertEqual(json.loads(self.locks_path.read_text()), {})
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertFalse(session["recovery_required"])

    def test_restore_resolution_keeps_recovery_pending_when_lock_release_fails(self):
        self.guarded_write_fixture(settings_written=True)
        app_v2.mark_benchmark_restore_recovery_required(
            "bench_001", TimeoutError("offline"), "candidate_aborted"
        )

        with patch.object(app_v2.thermal_locks, "release_lock", return_value=False):
            with self.assertRaisesRegex(ValueError, "could not be released"):
                app_v2.confirm_manual_benchmark_restore({"session_id": "bench_001"})

        profile = json.loads(self.restore_path.read_text())["bench_001"]
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertTrue(profile["recovery_required"])
        self.assertEqual(profile["status"], "failed")
        self.assertTrue(session["recovery_required"])

    def test_prepare_benchmark_session_blocks_unresolved_restore_recovery(self):
        self.restore_profile()
        app_v2.benchmark_restore.mark_recovery_required(
            self.restore_path, "bench_001", "restore timeout"
        )

        with patch.object(app_v2, "normalized_stats") as stats:
            with self.assertRaisesRegex(ValueError, "restore recovery is required"):
                app_v2.prepare_benchmark_session({"miner": "TestMiner"})

        stats.assert_not_called()

    def test_sample_benchmark_candidate_prewrite_abort_releases_lock(self):
        self.guarded_write_fixture()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "CHIP_TEMP_EXCEEDED"):
                app_v2.sample_benchmark_candidate(
                    "bench_001",
                    1,
                    sample_provider=lambda: {"temp": 69, "th": 1.0},
                    sleep_fn=lambda seconds: None,
                    max_samples=1,
                )

        apply.assert_not_called()
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertIn("CHIP_TEMP_EXCEEDED", session["reason"])
        restore = json.loads(self.restore_path.read_text())["bench_001"]
        self.assertEqual(restore["status"], "failed")
        self.assertEqual(json.loads(self.locks_path.read_text()), {})
        row = app_v2.benchmark_results.candidate_result(
            self.results_path,
            "bench_001",
            1,
        )
        self.assertEqual(row["safety_decision"], "CHIP_TEMP_EXCEEDED")

    def test_run_benchmark_candidate_requires_valid_request(self):
        with patch.object(app_v2, "sample_benchmark_candidate") as sample:
            with self.assertRaisesRegex(ValueError, "Session ID"):
                app_v2.run_benchmark_candidate({"sequence": 1})
            with self.assertRaisesRegex(ValueError, "positive integer"):
                app_v2.run_benchmark_candidate({"session_id": "bench_001", "sequence": 0})

        sample.assert_not_called()

    def test_run_benchmark_candidate_delegates_one_candidate(self):
        with patch.object(
            app_v2,
            "sample_benchmark_candidate",
            return_value={"ok": True, "sequence": 2},
        ) as sample:
            result = app_v2.run_benchmark_candidate({
                "session_id": " bench_001 ",
                "sequence": 2,
                "blocking": True,
            })

        self.assertEqual(result, {"ok": True, "sequence": 2})
        sample.assert_called_once_with("bench_001", 2)

    def test_run_benchmark_candidate_does_not_bypass_active_session_guard(self):
        self.interrupted_session("completed", device_profile="axeos_bitaxe")
        self.restore_profile()
        self.thermal_lock()
        self.planned_candidate_results()

        with patch.object(app_v2, "apply_settings") as apply:
            with self.assertRaisesRegex(ValueError, "not active"):
                app_v2.run_benchmark_candidate({
                    "session_id": "bench_001",
                    "sequence": 1,
                })

        apply.assert_not_called()

    def test_candidate_runner_failure_persists_for_terminal_status_payload(self):
        self.guarded_write_fixture()

        class ImmediateThread:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()

        def fail_candidate(session_id, sequence):
            app_v2.benchmark_restore.mark_restore_profile(
                self.restore_path,
                session_id,
                "restored",
                reason="candidate_failed_after_restore",
            )
            app_v2.thermal_locks.release_lock(
                self.locks_path,
                "TestMiner",
                session_id=session_id,
            )
            app_v2.benchmark_sessions.transition_session(
                self.benchmark_path,
                session_id,
                "failed",
                reason="candidate_failed_after_restore",
            )
            raise RuntimeError("candidate exploded")

        with patch.object(app_v2.threading, "Thread", ImmediateThread), patch.object(
            app_v2,
            "sample_benchmark_candidate",
            side_effect=fail_candidate,
        ):
            app_v2.start_benchmark_candidate_runner("bench_001", 1)

        runner = json.loads(self.benchmark_path.read_text())["bench_001"]["runner"]
        self.assertEqual(runner["status"], "failed")
        self.assertEqual(runner["sequence"], 1)
        self.assertEqual(runner["error"], "candidate exploded")
        self.assertEqual(runner["session_reason"], "candidate_failed_after_restore")
        self.assertEqual(runner["restore_status"], "restored")
        self.assertFalse(runner["recovery_required"])

        app_v2.BENCHMARK_RUNNERS.clear()
        with patch.object(app_v2, "cleanup_benchmark_reports", return_value={"pruned": []}):
            payload = app_v2.benchmark_status_payload()
        self.assertEqual(payload["runner"], runner)

    def test_restart_marks_persisted_running_runner_failed_with_restore_outcome(self):
        self.interrupted_session(
            "benchmarking",
            settings_written=True,
            runner={
                "session_id": "bench_001",
                "sequence": 1,
                "status": "running",
                "started_at": "2026-06-24T01:05:00+00:00",
            },
        )
        self.restore_profile()
        self.thermal_lock()

        with patch.object(app_v2, "apply_settings", return_value={"ok": True}):
            recovery = app_v2.recover_benchmark_sessions()

        self.assertTrue(recovery["restored_settings"])
        runner = json.loads(self.benchmark_path.read_text())["bench_001"]["runner"]
        self.assertEqual(runner["status"], "failed")
        self.assertIn("dashboard restart", runner["error"])
        self.assertEqual(runner["restore_status"], "restored")
        self.assertFalse(runner["recovery_required"])

    def full_run_fixture(self):
        self.interrupted_session(
            "benchmarking", device_profile="axeos_bitaxe",
            benchmark_plan={
                "baseline": {"frequency": 600, "voltage": 1150},
                "candidate_count": 2,
            },
        )
        self.restore_profile()
        self.thermal_lock()
        candidates = [
            {"sequence": 1, "frequency": 400, "voltage": 1050,
             "frequency_relation": "below_base", "voltage_relation": "below_base",
             "is_below_base": True},
            {"sequence": 2, "frequency": 425, "voltage": 1050,
             "frequency_relation": "below_base", "voltage_relation": "below_base",
             "is_below_base": True},
        ]
        app_v2.benchmark_results.save_planned_results(
            self.results_path, "bench_001", candidates
        )

    def test_full_run_sequences_all_candidates_and_completes(self):
        self.full_run_fixture()

        class ImmediateThread:
            def __init__(self, target, daemon=None): self.target = target
            def start(self): self.target()

        seen = []
        def sample(session_id, sequence, **kwargs):
            seen.append(sequence)
            app_v2.benchmark_results.update_candidate_result(
                self.results_path, session_id, sequence,
                {"status": "sampled", "sample_summary": {
                    "sample_count": 60, "average_hashrate_th": 1 + sequence / 10,
                    "min_hashrate_th": 1, "max_hashrate_th": 1.3,
                    "hashrate_variability_pct": sequence,
                    "average_power_watts": 20 + sequence, "efficiency_jth": 18,
                    "average_temp": 60, "max_temp": 62,
                    "average_vr_temp": 65, "max_vr_temp": 67,
                }},
            )
            return {"ok": True}

        baseline = {"freq": 600, "volt": 1150, "temp": 60, "th": 1.0}
        with patch.object(app_v2.threading, "Thread", ImmediateThread), \
             patch.object(app_v2, "sample_benchmark_candidate", side_effect=sample), \
             patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply, \
             patch.object(app_v2, "normalized_stats", return_value=baseline):
            app_v2.start_full_benchmark_runner("bench_001")

        self.assertEqual(seen, [1, 2])
        self.assertEqual(apply.call_args.kwargs["frequency"], 600)
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "completed")
        self.assertIsNotNone(session["recommendations"])
        self.assertEqual(json.loads(self.locks_path.read_text()), {})

    def test_full_run_continues_after_safely_aborted_candidate(self):
        self.full_run_fixture()

        class ImmediateThread:
            def __init__(self, target, daemon=None): self.target = target
            def start(self): self.target()

        seen = []
        def sample(session_id, sequence, **kwargs):
            seen.append(sequence)
            status = "aborted" if sequence == 1 else "sampled"
            app_v2.benchmark_results.update_candidate_result(
                self.results_path, session_id, sequence,
                {"status": status, "safety_decision": "POWER_EXCEEDED" if sequence == 1 else None},
            )
            return {"ok": sequence != 1, "aborted": sequence == 1}

        with patch.object(app_v2.threading, "Thread", ImmediateThread), \
             patch.object(app_v2, "sample_benchmark_candidate", side_effect=sample), \
             patch.object(app_v2, "complete_full_benchmark", return_value={"recommendations": None}):
            app_v2.start_full_benchmark_runner("bench_001")
        self.assertEqual(seen, [1, 2])

    def test_cancel_during_full_run_stops_transition_and_restores(self):
        self.full_run_fixture()
        self.interrupted_session(
            "benchmarking", device_profile="axeos_bitaxe", settings_written=True,
            benchmark_plan={"baseline": {"frequency": 600, "voltage": 1150}},
        )

        class ImmediateThread:
            def __init__(self, target, daemon=None): self.target = target
            def start(self): self.target()

        seen = []
        def sample(session_id, sequence, **kwargs):
            seen.append(sequence)
            app_v2.cancel_active_benchmark_session({"session_id": session_id})
            return {"ok": False}

        with patch.object(app_v2.threading, "Thread", ImmediateThread), \
             patch.object(app_v2, "sample_benchmark_candidate", side_effect=sample), \
             patch.object(app_v2, "apply_settings", return_value={"ok": True}) as apply:
            app_v2.start_full_benchmark_runner("bench_001")
        self.assertEqual(seen, [1])
        self.assertEqual(apply.call_args.kwargs["frequency"], 600)
        self.assertEqual(json.loads(self.benchmark_path.read_text())["bench_001"]["state"], "canceled")

    def test_full_runner_rejects_concurrent_runner(self):
        self.full_run_fixture()
        app_v2.BENCHMARK_RUNNERS["bench_001"] = {"status": "running", "mode": "manual"}
        with self.assertRaisesRegex(ValueError, "already running"):
            app_v2.start_full_benchmark_runner("bench_001")

    def test_failed_final_restore_requires_recovery_and_preserves_lock(self):
        self.full_run_fixture()
        profile = app_v2.benchmark_restore.get_restore_profile(self.restore_path, "bench_001")
        with patch.object(app_v2, "apply_settings", side_effect=TimeoutError("offline")):
            with self.assertRaisesRegex(TimeoutError, "offline"):
                app_v2.complete_full_benchmark("bench_001", profile)
        session = json.loads(self.benchmark_path.read_text())["bench_001"]
        self.assertEqual(session["state"], "failed")
        self.assertTrue(session["recovery_required"])
        self.assertIn("TestMiner", json.loads(self.locks_path.read_text()))


if __name__ == "__main__":
    unittest.main()
