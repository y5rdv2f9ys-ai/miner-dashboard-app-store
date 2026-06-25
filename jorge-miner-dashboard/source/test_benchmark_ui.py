import unittest
from pathlib import Path

import app_v2


class BenchmarkUiTests(unittest.TestCase):
    def test_benchmark_static_files_exist(self):
        static = Path(app_v2.APP_DIR) / "static"

        self.assertTrue((static / "benchmark.html").exists())
        self.assertTrue((static / "benchmark.css").exists())
        self.assertTrue((static / "benchmark.js").exists())

    def test_benchmark_page_references_required_endpoints(self):
        static = Path(app_v2.APP_DIR) / "static"
        html = (static / "benchmark.html").read_text()
        script = (static / "benchmark.js").read_text()

        self.assertIn("Benchmark Tuner", html)
        self.assertIn("/api/benchmark/prepare", script)
        self.assertIn("/api/benchmark/run-candidate", script)
        self.assertIn("/api/benchmark/cancel-active", script)
        self.assertIn("/api/benchmark/report", script)

    def test_existing_pages_link_to_benchmark_tuner(self):
        static = Path(app_v2.APP_DIR) / "static"

        self.assertIn('href="/benchmark"', (static / "dashboard.html").read_text())
        self.assertIn('href="/benchmark"', (static / "miners.html").read_text())
        self.assertIn('href="/benchmark"', (static / "thermal-settings.html").read_text())


if __name__ == "__main__":
    unittest.main()
