import unittest
from unittest.mock import patch

import app_v2


class DashboardCollectorTests(unittest.TestCase):
    def test_build_odds_preserves_missing_network_difficulty(self):
        miners = [{"name": "PoolMiner", "pool": "Braiins", "coin": None,
                   "online": True, "th": 1.1}]
        with patch.object(app_v2, "get_network_difficulty", return_value=None):
            odds = app_v2.build_odds(miners, {})
        self.assertIsNone(odds["Braiins"]["difficulty"])
        self.assertIsNone(odds["Braiins"]["hour_den"])
        self.assertIsNone(odds["Braiins"]["day_den"])


if __name__ == "__main__":
    unittest.main()
