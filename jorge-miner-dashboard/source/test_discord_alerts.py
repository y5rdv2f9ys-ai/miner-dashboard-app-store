from pathlib import Path
import csv
import json
import sqlite3
import tempfile
import unittest

from discord_alerts import DiscordAlertManager


class FakeClock:
    def __init__(self, value=1000000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DiscordAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.history = self.base / "history.csv"
        self.blocks = self.base / "blocks.sqlite"
        self.clock = FakeClock()
        self.sent = []
        self.manager = DiscordAlertManager(
            self.base,
            self.history,
            self.blocks,
            webhook_sender=lambda *message: self.sent.append(message),
            clock=self.clock,
        )
        (self.base / "discord_alert_config.json").write_text(
            json.dumps(
                {
                    "startup_grace_seconds": 300,
                    "offline_delay_seconds": 120,
                    "online_delay_seconds": 30,
                    "low_hash_delay_seconds": 600,
                    "low_hash_percent": 70,
                    "low_hash_recovery_percent": 85,
                    "minimum_24h_samples": 2,
                }
            )
        )
        with self.history.open("w", newline="") as output:
            writer = csv.DictWriter(
                output, fieldnames=["epoch", "timestamp", "miner", "th", "temp"]
            )
            writer.writeheader()
            writer.writerow(
                {"epoch": self.clock() - 60, "timestamp": "", "miner": "Test", "th": 10, "temp": 50}
            )
            writer.writerow(
                {"epoch": self.clock() - 120, "timestamp": "", "miner": "Test", "th": 10, "temp": 50}
            )
        db = sqlite3.connect(self.blocks)
        db.execute(
            "CREATE TABLE blocks_entity (id INTEGER PRIMARY KEY, height INTEGER, worker TEXT)"
        )
        db.commit()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def miner(online=True, th=10):
        return {"name": "Test", "online": online, "th": th}

    @staticmethod
    def solo_miner(online=True, th=10):
        return {"name": "BitaxeBTC", "online": online, "th": th}

    def test_solo_offline_and_online_are_delayed_and_not_repeated(self):
        self.manager.process([self.solo_miner(False, 0)], [])
        self.clock.advance(299)
        self.manager.process([self.solo_miner(False, 0)], [])
        self.assertEqual(self.sent, [])
        self.clock.advance(1)
        self.manager.process([self.solo_miner(False, 0)], [])
        self.assertEqual(
            [item[0] for item in self.sent], ["Miner offline: BitaxeBTC"]
        )
        self.manager.process([self.solo_miner(False, 0)], [])
        self.assertEqual(len(self.sent), 1)

        self.manager.process([self.solo_miner()], [])
        self.clock.advance(29)
        self.manager.process([self.solo_miner()], [])
        self.assertEqual(len(self.sent), 1)
        self.clock.advance(1)
        self.manager.process([self.solo_miner()], [])
        self.assertEqual(
            [item[0] for item in self.sent],
            ["Miner offline: BitaxeBTC", "Miner online: BitaxeBTC"],
        )

    def test_non_solo_miners_do_not_send_offline_or_online_alerts(self):
        self.manager.process([self.miner(False, 0)], [])
        self.clock.advance(900)
        self.manager.process([self.miner(False, 0)], [])
        self.manager.process([self.miner()], [])
        self.clock.advance(60)
        self.manager.process([self.miner()], [])
        self.assertEqual(self.sent, [])

    def test_low_hash_and_recovery_are_delayed_and_not_repeated(self):
        self.manager.process([self.miner(th=6)], [])
        self.clock.advance(599)
        self.manager.process([self.miner(th=6)], [])
        self.assertEqual(self.sent, [])
        self.clock.advance(1)
        self.manager.process([self.miner(th=6)], [])
        self.assertEqual([item[0] for item in self.sent], ["Low hashrate: Test"])
        self.manager.process([self.miner(th=6)], [])
        self.assertEqual(len(self.sent), 1)
        self.manager.process([self.miner(th=9)], [])
        self.assertEqual(
            [item[0] for item in self.sent],
            ["Low hashrate: Test", "Hashrate recovered: Test"],
        )

    def test_transient_unhealthy_poll_does_not_erase_low_hash_incident(self):
        self.manager.process([self.miner(th=6)], [])
        self.clock.advance(600)
        self.manager.process([self.miner(th=6)], [])
        self.assertEqual([item[0] for item in self.sent], ["Low hashrate: Test"])

        self.manager.process([self.miner(False, 0)], [])
        self.clock.advance(30)
        self.manager.process([self.miner(th=9)], [])
        self.assertEqual(
            [item[0] for item in self.sent],
            ["Low hashrate: Test", "Hashrate recovered: Test"],
        )

    def test_non_solo_legacy_offline_state_is_cleared_silently(self):
        state = {
            "miners": {
                "NOctaxe": {
                    "offline_since": self.clock() - 300,
                    "online_since": None,
                    "offline_alerted": True,
                    "low_since": None,
                    "low_alerted": False,
                }
            },
            "blocks": {"btc_seen": [], "bch_seen": [], "initialized": True},
        }
        self.manager.save_state(state)
        self.clock.advance(300)
        self.manager.process(
            [{"name": "NOctaxe", "online": False, "th": 0}], []
        )
        self.assertEqual(self.sent, [])
        item = self.manager.load_state()["miners"]["NOctaxe"]
        self.assertIsNone(item["offline_since"])
        self.assertIsNone(item["online_since"])
        self.assertFalse(item["offline_alerted"])

    def test_existing_blocks_are_seeded_and_new_blocks_alert_once(self):
        db = sqlite3.connect(self.blocks)
        db.execute("INSERT INTO blocks_entity VALUES (1, 900000, 'Test')")
        db.commit()
        db.close()
        old_bch = [{"height": 900001, "hash": "old"}]
        self.manager.process([self.miner()], old_bch)
        self.clock.advance(300)
        self.manager.process([self.miner()], old_bch)
        self.assertEqual(self.sent, [])

        db = sqlite3.connect(self.blocks)
        db.execute("INSERT INTO blocks_entity VALUES (2, 900002, 'Test')")
        db.commit()
        db.close()
        new_bch = old_bch + [{"height": 900003, "hash": "new"}]
        self.manager.process([self.miner()], new_bch)
        self.manager.process([self.miner()], new_bch)
        self.assertEqual(
            [item[0] for item in self.sent],
            ["BTC solo block found", "BCH solo block found"],
        )

    def test_solo_hashrate_recovery_waits_for_offline_recovery(self):
        state = {
            "miners": {
                "BitaxeBTC": {
                    "offline_since": self.clock() - 120,
                    "online_since": None,
                    "offline_alerted": True,
                    "low_since": None,
                    "low_alerted": True,
                }
            },
            "blocks": {"btc_seen": [], "bch_seen": [], "initialized": True},
        }
        self.manager.save_state(state)
        self.clock.advance(300)
        self.manager.process([self.solo_miner(th=10)], [])
        self.assertEqual(self.sent, [])
        self.clock.advance(30)
        self.manager.process([self.solo_miner(th=10)], [])
        self.assertEqual(
            [item[0] for item in self.sent],
            ["Miner online: BitaxeBTC"],
        )


if __name__ == "__main__":
    unittest.main()
