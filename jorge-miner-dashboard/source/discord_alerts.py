from pathlib import Path
import csv
import hashlib
import json
import os
import sqlite3
import time
from urllib.request import Request, urlopen


DEFAULT_CONFIG = {
    "enabled": True,
    "startup_grace_seconds": 300,
    "offline_delay_seconds": 120,
    "online_delay_seconds": 30,
    "low_hash_delay_seconds": 600,
    "low_hash_percent": 70,
    "low_hash_recovery_percent": 85,
    "minimum_24h_samples": 60,
}

SOLO_MINERS = {"BitaxeBTC", "BitaxeBCH"}


class DiscordAlertManager:
    def __init__(
        self,
        base_dir,
        history_path,
        btc_blocks_db,
        webhook_sender=None,
        clock=None,
    ):
        self.base_dir = Path(base_dir)
        self.history_path = Path(history_path)
        self.btc_blocks_db = Path(btc_blocks_db)
        self.webhook_path = self.base_dir / ".discord_webhook_url"
        self.config_path = self.base_dir / "discord_alert_config.json"
        self.state_path = self.base_dir / "discord_alert_state.json"
        self.clock = clock or time.time
        self.started_at = self.clock()
        self.webhook_sender = webhook_sender or self._post_webhook
        self._baseline_cache = {}
        self._baseline_cache_at = 0

    def load_config(self):
        config = DEFAULT_CONFIG.copy()
        try:
            saved = json.loads(self.config_path.read_text())
            if isinstance(saved, dict):
                config.update(saved)
        except Exception:
            pass
        return config

    def load_state(self):
        try:
            state = json.loads(self.state_path.read_text())
            if isinstance(state, dict):
                return state
        except Exception:
            pass
        return {"miners": {}, "blocks": {"btc_seen": [], "bch_seen": []}}

    def save_state(self, state):
        temp_path = self.state_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, self.state_path)

    def process(self, miners, bch_blocks):
        config = self.load_config()
        if not config.get("enabled", True):
            return

        now = self.clock()
        state = self.load_state()
        changed = self._process_miners(miners, state, config, now)
        changed |= self._process_blocks(bch_blocks or [], state, config, now)
        if changed:
            self.save_state(state)

    def send_test(self):
        return self._send(
            "Miner Manager test",
            "Discord alerts are configured and the miner dashboard is running.",
            0x3498DB,
        )

    def _process_miners(self, miners, state, config, now):
        miner_states = state.setdefault("miners", {})
        baselines = self._get_online_24h_averages(
            int(config["minimum_24h_samples"]), now
        )
        in_grace = now - self.started_at < int(config["startup_grace_seconds"])
        changed = False

        for miner in miners:
            name = miner["name"]
            current_th = float(miner.get("th", 0) or 0)
            healthy = bool(miner.get("online")) and current_th > 0

            if name not in miner_states:
                miner_states[name] = {
                    "offline_since": None,
                    "online_since": None,
                    "offline_alerted": False,
                    "low_since": None,
                    "low_alerted": False,
                }
                changed = True
            item = miner_states[name]

            if name not in SOLO_MINERS:
                for key, value in (
                    ("offline_since", None),
                    ("online_since", None),
                    ("offline_alerted", False),
                ):
                    if item.get(key) != value:
                        item[key] = value
                        changed = True

                if not healthy:
                    if item.get("low_since") is not None:
                        item["low_since"] = None
                        changed = True
                    continue

            if not healthy:
                if item.get("offline_since") is None:
                    item["offline_since"] = now
                    changed = True
                if item.get("online_since") is not None:
                    item["online_since"] = None
                    changed = True
                if item.get("low_since") is not None:
                    item["low_since"] = None
                    changed = True

                offline_for = now - item["offline_since"]
                if (
                    not in_grace
                    and not item.get("offline_alerted")
                    and offline_for >= int(config["offline_delay_seconds"])
                ):
                    detail = "unreachable" if not miner.get("online") else "online at 0 TH/s"
                    if self._send(
                        f"Miner offline: {name}",
                        f"{name} has been {detail} for at least 2 minutes.",
                        0xE74C3C,
                    ):
                        item["offline_alerted"] = True
                        changed = True
                continue

            if item.get("offline_since") is not None:
                item["offline_since"] = None
                changed = True

            if item.get("offline_alerted"):
                if item.get("online_since") is None:
                    item["online_since"] = now
                    changed = True
                if (
                    not in_grace
                    and now - item["online_since"] >= int(config["online_delay_seconds"])
                ):
                    if self._send(
                        f"Miner online: {name}",
                        f"{name} is back online at {current_th:.2f} TH/s.",
                        0x2ECC71,
                    ):
                        item["offline_alerted"] = False
                        item["online_since"] = None
                        item["low_alerted"] = False
                        changed = True
                if item.get("offline_alerted"):
                    continue
            elif item.get("online_since") is not None:
                item["online_since"] = None
                changed = True

            baseline = baselines.get(name)
            if not baseline:
                continue

            percent = current_th / baseline * 100
            if percent < float(config["low_hash_percent"]):
                if item.get("low_since") is None:
                    item["low_since"] = now
                    changed = True
                if (
                    not in_grace
                    and not item.get("low_alerted")
                    and now - item["low_since"] >= int(config["low_hash_delay_seconds"])
                ):
                    if self._send(
                        f"Low hashrate: {name}",
                        (
                            f"{name} is at {current_th:.2f} TH/s, {percent:.0f}% of its "
                            f"{baseline:.2f} TH/s online-only 24-hour average."
                        ),
                        0xF39C12,
                    ):
                        item["low_alerted"] = True
                        changed = True
            else:
                if item.get("low_since") is not None:
                    item["low_since"] = None
                    changed = True
                if (
                    item.get("low_alerted")
                    and percent >= float(config["low_hash_recovery_percent"])
                    and not in_grace
                ):
                    if self._send(
                        f"Hashrate recovered: {name}",
                        (
                            f"{name} recovered to {current_th:.2f} TH/s, "
                            f"{percent:.0f}% of its {baseline:.2f} TH/s 24-hour average."
                        ),
                        0x2ECC71,
                    ):
                        item["low_alerted"] = False
                        changed = True

        return changed

    def _process_blocks(self, bch_blocks, state, config, now):
        block_state = state.setdefault("blocks", {"btc_seen": [], "bch_seen": []})
        btc_seen = set(block_state.setdefault("btc_seen", []))
        bch_seen = set(block_state.setdefault("bch_seen", []))
        btc_blocks = self._read_btc_blocks()
        bch_items = [(self._block_id(block), block) for block in bch_blocks]
        changed = False

        # The first observation establishes a baseline and never alerts on old blocks.
        if not block_state.get("initialized"):
            block_state["btc_seen"] = [block["id"] for block in btc_blocks][-100:]
            block_state["bch_seen"] = [block_id for block_id, _ in bch_items][-100:]
            block_state["initialized"] = True
            return True

        in_grace = now - self.started_at < int(config["startup_grace_seconds"])
        if in_grace:
            return False

        for block in btc_blocks:
            block_id = block["id"]
            if block_id in btc_seen:
                continue
            if self._send(
                "BTC solo block found",
                (
                    f"BitaxeBTC found Bitcoin block {block['height']} "
                    f"through worker {block['worker']}."
                ),
                0xF7931A,
            ):
                btc_seen.add(block_id)
                changed = True

        for block_id, block in bch_items:
            if block_id in bch_seen:
                continue
            height = self._first_value(block, "height", "blockHeight", "blockheight")
            suffix = f" at height {height}" if height is not None else ""
            if self._send(
                "BCH solo block found",
                f"BitaxeBCH found a Bitcoin Cash block{suffix}.",
                0x22C55E,
            ):
                bch_seen.add(block_id)
                changed = True

        if changed:
            block_state["btc_seen"] = sorted(btc_seen)[-100:]
            block_state["bch_seen"] = sorted(bch_seen)[-100:]
        return changed

    def _get_online_24h_averages(self, minimum_samples, now):
        if now - self._baseline_cache_at < 60:
            return self._baseline_cache

        values = {}
        cutoff = now - 86400
        try:
            with self.history_path.open(newline="") as history:
                for row in csv.DictReader(history):
                    epoch = float(row["epoch"])
                    hashrate = float(row["th"])
                    if epoch >= cutoff and hashrate > 0:
                        values.setdefault(row["miner"], []).append(hashrate)
        except Exception:
            values = {}

        self._baseline_cache = {
            name: sum(samples) / len(samples)
            for name, samples in values.items()
            if len(samples) >= minimum_samples
        }
        self._baseline_cache_at = now
        return self._baseline_cache

    def _read_btc_blocks(self):
        if not self.btc_blocks_db.exists():
            return []
        try:
            db = sqlite3.connect(f"file:{self.btc_blocks_db}?mode=ro", uri=True)
            try:
                rows = db.execute(
                    "SELECT id, height, worker FROM blocks_entity ORDER BY id"
                ).fetchall()
            finally:
                db.close()
            return [
                {"id": str(row[0]), "height": row[1], "worker": row[2]}
                for row in rows
            ]
        except Exception as error:
            print(f"BTC block alert read error: {error}", flush=True)
            return []

    @staticmethod
    def _first_value(block, *keys):
        if not isinstance(block, dict):
            return None
        for key in keys:
            if block.get(key) is not None:
                return block[key]
        return None

    @staticmethod
    def _block_id(block):
        if isinstance(block, dict):
            for key in ("id", "hash", "blockHash", "height", "blockHeight"):
                if block.get(key) is not None:
                    return f"{key}:{block[key]}"
        raw = json.dumps(block, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()

    def _send(self, title, description, color):
        try:
            self.webhook_sender(title, description, color)
            print(f"Discord alert sent: {title}", flush=True)
            return True
        except Exception as error:
            print(f"Discord alert error: {error}", flush=True)
            return False

    def _post_webhook(self, title, description, color):
        webhook_url = self.webhook_path.read_text().strip()
        if not webhook_url.startswith(
            ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
        ):
            raise ValueError("Discord webhook is not configured")

        payload = json.dumps(
            {
                "username": "Miner Manager",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": title,
                        "description": description,
                        "color": color,
                    }
                ],
            }
        ).encode()
        request = Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "miner-manager/1"},
            method="POST",
        )
        with urlopen(request, timeout=8) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {response.status}")
