import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_v2


class PublicPage3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token_path = Path(self.temp.name) / ".page3_public_token"
        self.token_path.write_text("test-token\n")
        self.token_patch = patch.object(app_v2, "PAGE3_PUBLIC_TOKEN_PATH", self.token_path)
        self.token_patch.start()

    def tearDown(self):
        self.token_patch.stop()
        self.temp.cleanup()

    def test_public_paths_include_data_alias(self):
        self.assertIn("/public/page3", app_v2.PAGE3_PUBLIC_PATHS)
        self.assertIn("/public/page3-data", app_v2.PAGE3_PUBLIC_PATHS)
        self.assertNotIn("/api/page3", app_v2.PAGE3_PUBLIC_PATHS)

    def test_public_token_validation(self):
        self.assertTrue(app_v2.valid_page3_public_token("test-token"))
        self.assertFalse(app_v2.valid_page3_public_token(""))
        self.assertFalse(app_v2.valid_page3_public_token("wrong-token"))

    def test_public_payload_uses_page3_builder(self):
        snapshot = {
            "updated": "2026-06-22 12:00:00",
            "miners": [],
            "runs": {},
            "odds": {},
            "braiins": {"available": False, "workers": []},
            "solopool": {"available": False, "workers": []},
            "system_status": {"thermal_management": True, "miner_logging": True},
        }
        with patch.object(app_v2, "get_dashboard_snapshot", return_value=snapshot):
            payload = app_v2.page3_public_payload()
        json.dumps(payload)
        self.assertIn("updated", payload)


if __name__ == "__main__":
    unittest.main()
