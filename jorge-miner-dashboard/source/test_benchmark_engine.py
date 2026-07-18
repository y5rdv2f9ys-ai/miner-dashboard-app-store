import unittest

import benchmark_engine
import benchmark_profiles


class BenchmarkEngineTests(unittest.TestCase):
    def test_generate_matrix_starts_below_base_from_profile_minimums(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdoctaxe")
        matrix = benchmark_engine.generate_matrix(
            profile,
            baseline={"frequency": 635, "voltage": 1155},
        )

        self.assertGreater(len(matrix), 0)
        first = matrix[0]
        self.assertEqual(first["frequency"], profile["frequency"]["min"])
        self.assertEqual(first["voltage"], profile["voltage"]["min"])
        self.assertTrue(first["is_below_base"])
        self.assertEqual(first["frequency_relation"], "below_base")
        self.assertEqual(first["voltage_relation"], "below_base")

    def test_generate_matrix_uses_profile_range_not_base_as_lower_bound(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")
        matrix = benchmark_engine.generate_matrix(
            profile,
            baseline={"frequency": 600, "voltage": 1150},
        )

        frequencies = {candidate["frequency"] for candidate in matrix}
        voltages = {candidate["voltage"] for candidate in matrix}
        self.assertIn(400, frequencies)
        self.assertIn(1050, voltages)
        self.assertIn(600, frequencies)
        self.assertIn(1150, voltages)

    def test_generate_matrix_orders_low_voltage_then_low_frequency(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdqaxe")
        matrix = benchmark_engine.generate_matrix(
            profile,
            baseline={"frequency": 760, "voltage": 1220},
        )

        ordered_pairs = [
            (candidate["voltage"], candidate["frequency"]) for candidate in matrix
        ]
        self.assertEqual(ordered_pairs, sorted(ordered_pairs))

    def test_dry_run_plan_has_no_write_permission(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdoctaxe")
        plan = benchmark_engine.dry_run_plan(
            profile,
            baseline={"frequency": 635, "voltage": 1155},
            now=123,
        )

        self.assertEqual(plan["mode"], "dry_run")
        self.assertFalse(plan["writes_enabled"])
        self.assertEqual(plan["created_at_epoch"], 123)
        self.assertEqual(plan["candidates"][0]["frequency"], 500)

    def test_safety_failure_detects_chip_temp_before_write_hook(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdoctaxe")

        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 74, "th": 5.0}),
            "CHIP_TEMP_EXCEEDED",
        )

    def test_safety_failure_detects_vr_temp_power_and_nerdos_input_voltage(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdqaxe")

        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "vr_temp": 90}),
            "VR_TEMP_EXCEEDED",
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "power": 90}),
            "POWER_EXCEEDED",
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 11.5}),
            "INPUT_VOLTAGE_LOW",
        )
        self.assertIsNone(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 12.1})
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 12.6}),
            "INPUT_VOLTAGE_HIGH",
        )

        self.assertIsNone(
            benchmark_engine.safety_failure(profile, {"temp": 60, "voltage": 11890})
        )

    def test_safety_failure_uses_five_volt_input_limits_for_axeos(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")

        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 4.7}),
            "INPUT_VOLTAGE_LOW",
        )
        self.assertIsNone(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 5.4})
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "input_voltage": 5.6}),
            "INPUT_VOLTAGE_HIGH",
        )

    def test_safety_failure_detects_api_zero_hashrate_and_watchdog_limits(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")

        self.assertIsNone(
            benchmark_engine.safety_failure(profile, None, api_failures=1)
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, None, api_failures=3),
            "API_FAILURE_LIMIT",
        )
        self.assertEqual(
            benchmark_engine.safety_failure(
                profile,
                {"temp": 60},
                zero_hashrate_seconds=60,
            ),
            "ZERO_HASHRATE_TIMEOUT",
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60}, elapsed_seconds=900),
            "WATCHDOG_TIMEOUT",
        )
        self.assertEqual(
            benchmark_engine.safety_failure(profile, {"temp": 60, "th": 0}),
            "ZERO_HASHRATE",
        )

    def test_safety_failure_allows_safe_sample(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")

        self.assertIsNone(
            benchmark_engine.safety_failure(
                profile,
                {
                    "temp": 60,
                    "vr_temp": 70,
                    "power": 20,
                    "input_voltage": 5.0,
                    "th": 1.1,
                },
            )
        )

    def test_sample_summary_averages_samples_and_efficiency(self):
        summary = benchmark_engine.sample_summary([
            {"th": 1.0, "temp": 60, "vr_temp": 70, "power": 20},
            {"th": 2.0, "temp": 62, "vr_temp": 72, "power": 24},
        ])

        self.assertEqual(summary["average_hashrate_th"], 1.5)
        self.assertEqual(summary["average_temp"], 61.0)
        self.assertEqual(summary["average_vr_temp"], 71.0)
        self.assertEqual(summary["average_power_watts"], 22.0)
        self.assertAlmostEqual(summary["efficiency_jth"], 14.6666666667)


if __name__ == "__main__":
    unittest.main()
