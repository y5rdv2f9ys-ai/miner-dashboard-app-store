import unittest

import benchmark_profiles


class BenchmarkProfilesTests(unittest.TestCase):
    def test_selects_bitaxe_profile_from_axeos_type(self):
        profile = benchmark_profiles.select_profile(
            {"name": "BitaxeBTC", "type": "axeos"},
            {"model": "Bitaxe Gamma"},
        )

        self.assertEqual(profile["id"], "axeos_bitaxe")
        self.assertEqual(profile["safety"]["max_chip_temp"], 69)

    def test_selects_nerdqaxe_profile_from_name(self):
        profile = benchmark_profiles.select_profile(
            {"name": "NQaxe", "type": "nerdos"},
            {"model": "NerdQaxe"},
        )

        self.assertEqual(profile["id"], "nerdos_nerdqaxe")
        self.assertEqual(profile["frequency"]["step"], 20)
        self.assertEqual(profile["safety"]["max_chip_temp"], 67)

    def test_selects_nerdoctaxe_profile_before_generic_nerdos(self):
        profile = benchmark_profiles.select_profile(
            {"name": "NOctaxe", "type": "nerdos"},
            {"model": "NerdOctAxe"},
        )

        self.assertEqual(profile["id"], "nerdos_nerdoctaxe")
        self.assertEqual(profile["frequency"]["step"], 5)
        self.assertEqual(profile["safety"]["max_chip_temp"], 74)
        self.assertEqual(profile["safety"]["max_power_watts"], 220)

    def test_unknown_nerdos_defaults_to_nerdqaxe_profile(self):
        profile = benchmark_profiles.select_profile(
            {"name": "Unknown", "type": "nerdos"},
            {"model": "Unknown"},
        )

        self.assertEqual(profile["id"], "nerdos_nerdqaxe")

    def test_validate_setting_accepts_profile_steps(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdoctaxe")

        self.assertEqual(
            benchmark_profiles.validate_setting(profile, 635, 1155),
            {"frequency": 635, "voltage": 1155},
        )

    def test_validate_setting_rejects_frequency_outside_range(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")

        with self.assertRaisesRegex(ValueError, "Frequency"):
            benchmark_profiles.validate_setting(profile, 675, 1150)

    def test_validate_setting_rejects_voltage_step(self):
        profile = benchmark_profiles.get_profile("nerdos_nerdqaxe")

        with self.assertRaisesRegex(ValueError, "Voltage"):
            benchmark_profiles.validate_setting(profile, 760, 1223)

    def test_all_profiles_are_copies(self):
        profile = benchmark_profiles.get_profile("axeos_bitaxe")
        profile["frequency"]["max"] = 9999

        self.assertEqual(
            benchmark_profiles.get_profile("axeos_bitaxe")["frequency"]["max"],
            650,
        )


if __name__ == "__main__":
    unittest.main()
