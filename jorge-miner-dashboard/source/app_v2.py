from pathlib import Path
import json
import csv
import mimetypes
import os
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from urllib.request import Request, urlopen
from datetime import datetime
from zoneinfo import ZoneInfo

from discord_alerts import DiscordAlertManager
from miner_telemetry import get_hashrate_th, get_reject_pct, get_voltage_mv, get_vr_temp

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MINER_DASHBOARD_DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get("MINER_DASHBOARD_PORT", "5056"))
REQUEST_TIMEOUT = 3
TZ = ZoneInfo("America/Tegucigalpa")
LOG_PATH = DATA_DIR / "miner_thermal_mode.log"
HISTORY_PATH = DATA_DIR / "history_v2.csv"
HISTORY_INTERVAL = 60
HISTORY_RETENTION = 7 * 24 * 60 * 60
COLLECT_INTERVAL = 10
LAST_HISTORY_WRITE = 0
BRAIINS_CACHE = {"timestamp": 0, "data": {}}
SOLOPOOL_CACHE = {"timestamp": 0, "data": {}}
POOL_CACHE_SECONDS = 60
BRAIINS_TOKEN_PATH = DATA_DIR / ".braiins_api_token"
BCH_SOLO_ADDRESS = "qq04xrcpzwvw2gjpxh653txnm9qaq9fesuq652e4wn"
STATE_LOCK = threading.Lock()
SNAPSHOT_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
COLLECTOR_WAKE = threading.Event()
DASHBOARD_SNAPSHOT = None

RUNS_PATH = DATA_DIR / "active_mining_runs.json"
MINERS_PATH = DATA_DIR / "miners_v2.json"
MANUAL_RESET_PATH = DATA_DIR / "manual_reset_marker"
THERMAL_HEARTBEAT_PATH = DATA_DIR / "thermal_heartbeat"
BTC_BLOCKS_DB = Path(
    os.environ.get("PUBLIC_POOL_DB_PATH", "/public-pool/public-pool.sqlite")
)
ALERT_MANAGER = DiscordAlertManager(
    base_dir=DATA_DIR,
    history_path=HISTORY_PATH,
    btc_blocks_db=BTC_BLOCKS_DB,
)

def load_runs():
    try:
        with open(RUNS_PATH, "r") as f:
            return json.load(f)
    except:
        return {}

def save_runs(runs):
    temp_path = f"{RUNS_PATH}.tmp"
    with open(temp_path, "w") as f:
        json.dump(runs, f, indent=2)
    os.replace(temp_path, RUNS_PATH)

def update_pool_runs(miners):
    runs = load_runs()
    now = int(time.time())

    pools = {}
    for m in miners:
        pool = m.get("pool", "Unknown")
        pools.setdefault(pool, {"online_th": 0.0, "online_miners": 0})

        if m.get("online") and float(m.get("th", 0)) > 0:
            pools[pool]["online_th"] += float(m.get("th", 0))
            pools[pool]["online_miners"] += 1

    for pool, pdata in pools.items():
        if pool not in runs:
            runs[pool] = {
                "started": now,
                "uptime_seconds": 0,
                "downtime_seconds": 0,
                "last_update": now,
                "online": False,
                "cumulative_odds": 0.0,
                "th_seconds": 0.0
            }

        raw_elapsed = max(0, now - int(runs[pool].get("last_update", now)))

        manual_reset_recent = False
        try:
            if MANUAL_RESET_PATH.exists():
                marker_time = int(MANUAL_RESET_PATH.read_text().strip())
                if now - marker_time < 600:
                    manual_reset_recent = True
        except:
            manual_reset_recent = False

        # Pool uptime logic:
        # If at least one miner in this pool is hashing, the pool is UP.
        # If one miner goes offline but others keep hashing, runtime continues and odds just grow slower.
        # Only count downtime when the whole pool has 0 active miners / 0 TH.
        active_elapsed = raw_elapsed

        if manual_reset_recent:
            # Do not count reset/restart gap as runtime or downtime
            runs[pool]["online"] = pdata["online_miners"] > 0

        elif pdata["online_miners"] > 0 and pdata["online_th"] > 0:
            runs[pool]["uptime_seconds"] += active_elapsed
            runs[pool]["online"] = True

        else:
            runs[pool]["downtime_seconds"] += active_elapsed
            runs[pool]["online"] = False

        runs[pool]["th_seconds"] = float(runs[pool].get("th_seconds", 0)) + (pdata["online_th"] * active_elapsed)
        runs[pool]["last_update"] = now

    save_runs(runs)
    return runs


EXPECTED_TH = {
    "BitaxeBTC": 1.10,
    "BitaxeBCH": 1.18,
    "Bitaxe001": 1.10,
    "Bitaxe002": 1.10,
    "Bitaxe003": 1.10,
    "Bitaxe004": 1.10,
    "NQaxe": 5.20,
    "NOctaxe": 9.10,
}

def load_miners():
    with MINERS_PATH.open("r") as f:
        return json.load(f)

def write_miners(miners):
    temp_path = MINERS_PATH.with_name(f"{MINERS_PATH.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(miners, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, MINERS_PATH)

THERMAL_FIELDS = (
    "base_freq",
    "hot_freq",
    "critical_freq",
    "warn_temp",
    "critical_temp",
    "recover_temp",
)

DEFAULT_THERMAL_SETTINGS = {
    "axeos": {
        "base_freq": 550,
        "base_volt": 1150,
        "hot_freq": 525,
        "critical_freq": 500,
        "warn_temp": 68,
        "critical_temp": 70,
        "recover_temp": 64,
    },
    "nerdos": {
        "base_freq": 700,
        "base_volt": 1200,
        "hot_freq": 650,
        "critical_freq": 560,
        "warn_temp": 66,
        "critical_temp": 68,
        "recover_temp": 64,
    },
}

def validate_miner_identity(data, existing_name=None):
    if not isinstance(data, dict):
        raise ValueError("Miner settings must be a JSON object")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Miner name is required")
    name = " ".join(name.strip().split())
    if len(name) > 48:
        raise ValueError("Miner name must be 48 characters or fewer")

    target_name = existing_name if existing_name is not None else name
    if not isinstance(target_name, str) or not target_name.strip():
        raise ValueError("Original miner name is required")
    target_name = target_name.strip()

    raw_type = data.get("type")
    if not isinstance(raw_type, str):
        raise ValueError("Miner OS is required")
    miner_type = raw_type.strip().lower()
    if miner_type not in DEFAULT_THERMAL_SETTINGS:
        raise ValueError("Miner OS must be AxeOS or NerdOS")

    raw_ip = data.get("ip")
    if not isinstance(raw_ip, str) or not raw_ip.strip():
        raise ValueError("IP address is required")
    ip = raw_ip.strip()
    try:
        parsed_ip = ip_address(ip)
    except ValueError:
        raise ValueError("IP address is invalid")
    if parsed_ip.version != 4 or parsed_ip.is_unspecified or parsed_ip.is_multicast:
        raise ValueError("IP address must be a usable IPv4 address")

    pool = data.get("pool", "")
    coin = data.get("coin", "")
    if pool is None:
        pool = ""
    if coin is None:
        coin = ""
    if not isinstance(pool, str) or not isinstance(coin, str):
        raise ValueError("Pool and coin must be text")
    pool = pool.strip()
    coin = coin.strip().upper()
    if len(pool) > 64:
        raise ValueError("Pool must be 64 characters or fewer")
    if len(coin) > 16:
        raise ValueError("Coin must be 16 characters or fewer")

    return {
        "target_name": target_name,
        "name": name,
        "type": miner_type,
        "ip": ip,
        "pool": pool,
        "coin": coin,
    }

def miner_management_payload():
    with CONFIG_LOCK:
        miners = load_miners()

    return {
        "miners": [
            {
                "name": miner.get("name", ""),
                "type": miner.get("type", ""),
                "ip": miner.get("ip", ""),
                "pool": miner.get("pool", ""),
                "coin": miner.get("coin", ""),
                "enabled": miner.get("enabled", True),
            }
            for miner in miners
        ]
    }

def add_miner(data):
    settings = validate_miner_identity(data)

    with CONFIG_LOCK:
        miners = load_miners()
        if any(miner.get("name") == settings["name"] for miner in miners):
            raise ValueError("Miner name already exists")
        if any(miner.get("ip") == settings["ip"] for miner in miners):
            raise ValueError("IP address already exists")

        defaults = DEFAULT_THERMAL_SETTINGS[settings["type"]]
        miner = {
            "name": settings["name"],
            "type": settings["type"],
            "ip": settings["ip"],
            "pool": settings["pool"],
            "coin": settings["coin"],
            "enabled": False,
            **defaults,
        }
        miners.append(miner)
        write_miners(miners)

    COLLECTOR_WAKE.set()
    return miner

def update_miner(data):
    settings = validate_miner_identity(data, data.get("original_name"))

    with CONFIG_LOCK:
        miners = load_miners()
        matches = [miner for miner in miners if miner.get("name") == settings["target_name"]]
        if len(matches) != 1:
            raise LookupError("Miner was not found or its name is not unique")
        if any(
            miner is not matches[0] and miner.get("name") == settings["name"]
            for miner in miners
        ):
            raise ValueError("Miner name already exists")
        if any(
            miner is not matches[0] and miner.get("ip") == settings["ip"]
            for miner in miners
        ):
            raise ValueError("IP address already exists")

        miner = matches[0]
        miner["name"] = settings["name"]
        miner["type"] = settings["type"]
        miner["ip"] = settings["ip"]
        miner["pool"] = settings["pool"]
        miner["coin"] = settings["coin"]
        write_miners(miners)

    COLLECTOR_WAKE.set()
    return miner

def delete_miner(data):
    if not isinstance(data, dict):
        raise ValueError("Miner settings must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Miner name is required")
    name = name.strip()

    with CONFIG_LOCK:
        miners = load_miners()
        remaining = [miner for miner in miners if miner.get("name") != name]
        if len(remaining) == len(miners):
            raise LookupError("Miner was not found")
        if len(remaining) != len(miners) - 1:
            raise LookupError("Miner name is not unique")
        write_miners(remaining)

    COLLECTOR_WAKE.set()
    return {"name": name}

def validate_thermal_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Miner name is required")

    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false")

    settings = {"name": name.strip(), "enabled": enabled}
    for field in THERMAL_FIELDS:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        settings[field] = value

    for field in ("base_freq", "hot_freq", "critical_freq"):
        value = settings[field]
        if not value.is_integer() or not 1 <= value <= 2000:
            raise ValueError(f"{field} must be a whole number between 1 and 2000")
        settings[field] = int(value)

    for field in ("warn_temp", "critical_temp", "recover_temp"):
        if not 1 <= settings[field] <= 120:
            raise ValueError(f"{field} must be between 1 and 120")

    if not (
        settings["critical_freq"]
        <= settings["hot_freq"]
        <= settings["base_freq"]
    ):
        raise ValueError(
            "Frequency order must be critical <= hot <= base"
        )

    if not (
        settings["recover_temp"]
        < settings["warn_temp"]
        < settings["critical_temp"]
    ):
        raise ValueError(
            "Temperature order must be recovery < warning < critical"
        )

    return settings

def save_thermal_settings(data):
    settings = validate_thermal_settings(data)

    with CONFIG_LOCK:
        miners = load_miners()
        matches = [miner for miner in miners if miner.get("name") == settings["name"]]
        if len(matches) != 1:
            raise LookupError("Miner was not found or its name is not unique")

        miner = matches[0]
        miner["enabled"] = settings["enabled"]
        for field in THERMAL_FIELDS:
            miner[field] = settings[field]

        write_miners(miners)

    COLLECTOR_WAKE.set()
    return miner

def thermal_settings_payload():
    live = get_dashboard_snapshot() or empty_dashboard_snapshot()
    live_by_name = {
        miner.get("name"): miner for miner in live.get("miners", [])
    }
    miners = []
    with CONFIG_LOCK:
        configured = load_miners()

    for miner in configured:
        current = live_by_name.get(miner.get("name"), {})
        miners.append({
            "name": miner.get("name", ""),
            "type": miner.get("type", ""),
            "ip": miner.get("ip", ""),
            "enabled": miner.get("enabled", True),
            "base_freq": miner.get("base_freq"),
            "hot_freq": miner.get("hot_freq"),
            "critical_freq": miner.get("critical_freq"),
            "warn_temp": miner.get("warn_temp"),
            "critical_temp": miner.get("critical_temp"),
            "recover_temp": miner.get("recover_temp"),
            "current_temp": current.get("temp"),
            "current_freq": current.get("freq"),
            "status": current.get("status", "OFFLINE"),
            "online": current.get("online", False),
        })
    return {"miners": miners, "check_interval_seconds": 60}

def collect_miners(miners):
    with ThreadPoolExecutor(max_workers=max(1, len(miners))) as executor:
        return list(executor.map(read_miner, miners))

def file_recent(path, max_age_seconds=180):
    try:
        return (time.time() - os.path.getmtime(path)) <= max_age_seconds
    except Exception:
        return False

def fetch_braiins_json(path):
    token = BRAIINS_TOKEN_PATH.read_text().strip()
    request = Request(
        f"https://pool.braiins.com{path}",
        headers={"Pool-Auth-Token": token, "User-Agent": "miner-dashboard/2"}
    )
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))

def fetch_braiins_stats():
    now = time.time()
    if now - BRAIINS_CACHE["timestamp"] < POOL_CACHE_SECONDS:
        return BRAIINS_CACHE["data"]

    try:
        profile = fetch_braiins_json("/accounts/profile/json/btc/").get("btc", {})
        worker_data = fetch_braiins_json("/accounts/workers/json/btc/").get("btc", {}).get("workers", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        start = datetime.fromtimestamp(now - (30 * 86400), TZ).strftime("%Y-%m-%d")
        rewards = fetch_braiins_json(
            f"/accounts/rewards/json/btc?from={start}&to={today}"
        ).get("btc", {}).get("daily_rewards", [])

        workers = []
        for name, worker in worker_data.items():
            workers.append({
                "name": name.split(".", 1)[-1],
                "full_name": name,
                "state": worker.get("state", "unknown"),
                "hash_rate_5m_th": float(worker.get("hash_rate_5m", 0)) / 1000,
                "hash_rate_60m_th": float(worker.get("hash_rate_60m", 0)) / 1000,
                "hash_rate_24h_th": float(worker.get("hash_rate_24h", 0)) / 1000,
            })

        result = {
            "available": True,
            "hash_rate_5m_th": float(profile.get("hash_rate_5m", 0)) / 1000,
            "hash_rate_60m_th": float(profile.get("hash_rate_60m", 0)) / 1000,
            "hash_rate_24h_th": float(profile.get("hash_rate_24h", 0)) / 1000,
            "ok_workers": int(profile.get("ok_workers", 0)),
            "low_workers": int(profile.get("low_workers", 0)),
            "off_workers": int(profile.get("off_workers", 0)),
            "current_balance": float(profile.get("current_balance", 0)),
            "today_reward": float(profile.get("today_reward", 0)),
            "estimated_reward": float(profile.get("estimated_reward", 0)),
            "all_time_reward": float(profile.get("all_time_reward", 0)),
            "workers": workers,
            "daily_rewards": rewards,
        }
        BRAIINS_CACHE.update({"timestamp": now, "data": result})
        return result
    except Exception as e:
        print(f"Braiins API error: {e}", flush=True)
        return BRAIINS_CACHE["data"] or {"available": False}

def fetch_solopool_stats():
    now = time.time()
    if now - SOLOPOOL_CACHE["timestamp"] < POOL_CACHE_SECONDS:
        return SOLOPOOL_CACHE["data"]
    try:
        data = fetch_json_url(
            f"https://bch.solopool.org/api/miners/{BCH_SOLO_ADDRESS}",
            timeout=8
        ) or {}
        workers = []
        for name, worker in (data.get("workers") or {}).items():
            workers.append({
                "name": name,
                "hash_rate_th": float(worker.get("hr", 0)) / 1e12,
                "offline": bool(worker.get("offline", False)),
            })
        result = {
            "available": True,
            "hash_rate_th": float(data.get("hashrate", 0)) / 1e12,
            "average_hash_rate_th": float(data.get("averageHashrate", 0)) / 1e12,
            "last_share": data.get("lastShare"),
            "best_share": float(data.get("bestShare", 0)),
            "round_shares": float(data.get("roundShares", 0)),
            "blocks": data.get("blocks", []),
            "workers": workers,
        }
        SOLOPOOL_CACHE.update({"timestamp": now, "data": result})
        return result
    except Exception as e:
        print(f"SoloPool API error: {e}", flush=True)
        return SOLOPOOL_CACHE["data"] or {"available": False}

def get_temp_color(temp):
    if temp >= 75:
        return "red"
    elif temp >= 70:
        return "orange"
    elif temp >= 65:
        return "yellow"
    return "green"

def get_status(name, temp, th):
    if th <= 0:
        return "OFFLINE"
    if name == "NOctaxe":
        if temp >= 75:
            return "CRITICAL"
        elif temp >= 71:
            return "HOT"
    if name.startswith("NQaxe"):
        if temp >= 68:
            return "CRITICAL"
        elif temp >= 66:
            return "HOT"
    if temp >= 70:
        return "HOT"
    return "BASE"

def get_thermal_limit(name):
    if name == "NOctaxe":
        return 75
    if name.startswith("NQaxe"):
        return 68
    return 70

def read_miner(miner):
    data = {}
    url = f"http://{miner['ip']}/api/system/info"
    try:
        with urlopen(url, timeout=REQUEST_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))

        temp = float(data.get("temp", 0))
        freq = int(data.get("frequency", 0))

        volt = get_voltage_mv(data, miner["type"])
        vr_temp = get_vr_temp(data)
        th = get_hashrate_th(data)
        reject = get_reject_pct(data)
        base_freq = miner.get("base_freq")
        hot_freq = miner.get("hot_freq")
        critical_freq = miner.get("critical_freq")

        if th <= 0:
            status = "OFFLINE"
        elif critical_freq and freq <= critical_freq:
            status = "MAX COOLING"
        elif hot_freq and freq <= hot_freq:
            status = "COOLING"
        elif base_freq and freq < base_freq:
            status = "HOLDING"
        else:
            status = "STABLE"

        return {
            "name": miner["name"],
            "pool": miner.get("pool", "Unknown"),
            "coin": miner.get("coin", ""),
            "online": True,
            "temp": temp,
            "vr_temp": vr_temp,
            "freq": freq,
            "volt": volt,
            "th": th,
            "reject": reject,
            "status": status,
            "status_class": status.replace(" ", "-"),
            "temp_color": get_temp_color(temp),
            "expected_th": miner.get("expected_th", EXPECTED_TH.get(miner["name"], max(th, 1.0))),
            "thermal_limit": miner.get("critical_temp", get_thermal_limit(miner["name"])),
            "best_session_diff": float(data.get("bestSessionDiff", 0) or 0),
            "best_diff": float(data.get("bestDiff", data.get("bestSessionDiff", 0)) or 0)
        }

    except Exception:
        return {
            "name": miner["name"],
            "pool": miner.get("pool", "Unknown"),
            "coin": miner.get("coin", ""),
            "online": False,
            "temp": 0,
            "vr_temp": -1,
            "freq": 0,
            "volt": 0,
            "th": 0,
            "reject": 0,
            "status": "OFFLINE",
            "temp_color": "gray",
            "expected_th": miner.get("expected_th", EXPECTED_TH.get(miner["name"], 1.0)),
            "thermal_limit": miner.get("critical_temp", get_thermal_limit(miner["name"])),
            "best_session_diff": 0,
            "best_diff": 0
        }

def cleanup_history():
    if not os.path.exists(HISTORY_PATH):
        return

    cutoff = time.time() - HISTORY_RETENTION
    rows = []

    try:
        with open(HISTORY_PATH, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    if float(row["epoch"]) >= cutoff:
                        rows.append(row)
                except:
                    pass

        with open(HISTORY_PATH, "w", newline="") as f:
            fieldnames = ["epoch", "timestamp", "miner", "th", "temp"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except:
        pass

def record_history(miners):
    global LAST_HISTORY_WRITE

    now = time.time()
    if now - LAST_HISTORY_WRITE < HISTORY_INTERVAL:
        return

    LAST_HISTORY_WRITE = now
    cleanup_history()

    exists = os.path.exists(HISTORY_PATH)
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

    with open(HISTORY_PATH, "a", newline="") as f:
        fieldnames = ["epoch", "timestamp", "miner", "th", "temp"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not exists:
            writer.writeheader()

        for m in miners:
            writer.writerow({
                "epoch": now,
                "timestamp": timestamp,
                "miner": m["name"],
                "th": m["th"],
                "temp": m["temp"]
            })

def get_performance():
    miners = load_miners()
    names = [m["name"] for m in miners]
    result = {}

    for name in names:
        result[name] = {
            "name": name,
            "th_60m": None,
            "th_12h": None,
            "th_24h": None,
            "temp_60m": None,
            "temp_12h": None,
            "temp_24h": None,
        }

    if not os.path.exists(HISTORY_PATH):
        return list(result.values())

    now = time.time()
    windows = {
        "60m": 60 * 60,
        "12h": 12 * 60 * 60,
        "24h": 24 * 60 * 60
    }

    buckets = {}
    for name in names:
        buckets[name] = {}
        for label in windows:
            buckets[name][label] = {"th": [], "temp": []}

    try:
        with open(HISTORY_PATH, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("miner")
                if name not in buckets:
                    continue

                try:
                    epoch = float(row["epoch"])
                    th = float(row["th"])
                    temp = float(row["temp"])
                except:
                    continue

                age = now - epoch

                for label, seconds in windows.items():
                    if age <= seconds:
                        buckets[name][label]["th"].append(th)
                        buckets[name][label]["temp"].append(temp)

        for name in names:
            for label in windows:
                th_values = buckets[name][label]["th"]
                temp_values = buckets[name][label]["temp"]

                if th_values:
                    result[name][f"th_{label}"] = sum(th_values) / len(th_values)
                if temp_values:
                    result[name][f"temp_{label}"] = sum(temp_values) / len(temp_values)

    except:
        pass

    return list(result.values())


# =========================
# REAL ODDS CALCULATOR
# =========================

NETWORK_CACHE = {
    "timestamp": 0,
    "BTC": None,
    "BCH": None
}

def fetch_float_url(url, timeout=5):
    try:
        with urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8").strip()
            return float(raw)
    except:
        return None

def fetch_json_url(url, timeout=5):
    try:
        with urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except:
        return None

def get_network_difficulty(coin):
    now = time.time()

    if NETWORK_CACHE.get(coin) and now - NETWORK_CACHE["timestamp"] < 600:
        return NETWORK_CACHE[coin]

    btc_diff = fetch_float_url("https://blockchain.info/q/getdifficulty")
    if btc_diff:
        NETWORK_CACHE["BTC"] = btc_diff

    bch_diff = None
    bch_stats = fetch_json_url("https://api.blockchair.com/bitcoin-cash/stats")
    try:
        bch_diff = float(bch_stats["data"]["difficulty"])
    except:
        bch_diff = None

    if bch_diff:
        NETWORK_CACHE["BCH"] = bch_diff

    NETWORK_CACHE["timestamp"] = now

    # Fallbacks only if internet/API fails.
    if not NETWORK_CACHE.get("BTC"):
        NETWORK_CACHE["BTC"] = 120_000_000_000_000
    if not NETWORK_CACHE.get("BCH"):
        NETWORK_CACHE["BCH"] = 735_000_000_000

    return NETWORK_CACHE.get(coin)

def probability_for_hashrate(th, seconds, difficulty):
    import math

    if th <= 0 or seconds <= 0 or difficulty <= 0:
        return 0.0

    hashes = th * 1_000_000_000_000 * seconds
    expected_hashes = difficulty * 4294967296
    return 1 - math.exp(-hashes / expected_hashes)

def odds_denominator(probability):
    if probability <= 0:
        return None
    return 1 / probability

def build_odds(miners, runs):
    pools = {}

    for m in miners:
        pool = m.get("pool", "Unknown")
        coin = m.get("coin", "")

        if pool not in pools:
            pools[pool] = {
                "coin": coin,
                "th": 0.0
            }

        if m.get("online") and float(m.get("th", 0)) > 0:
            pools[pool]["th"] += float(m.get("th", 0))

    out = {}

    for pool, pdata in pools.items():
        coin = pdata["coin"]
        th = pdata["th"]
        diff = get_network_difficulty(coin)

        p_hour = probability_for_hashrate(th, 3600, diff)
        p_day = probability_for_hashrate(th, 86400, diff)
        p_month = probability_for_hashrate(th, 86400 * 30, diff)
        p_year = probability_for_hashrate(th, 86400 * 365, diff)

        run = runs.get(pool, {})
        th_seconds = float(run.get("th_seconds", 0))
        p_run = 0.0
        if diff and diff > 0:
            import math
            expected_hashes = diff * 4294967296
            hashes = th_seconds * 1_000_000_000_000
            p_run = 1 - math.exp(-hashes / expected_hashes)

        out[pool] = {
            "coin": coin,
            "difficulty": diff,
            "th": th,
            "hour_den": odds_denominator(p_hour),
            "day_den": odds_denominator(p_day),
            "month_den": odds_denominator(p_month),
            "year_den": odds_denominator(p_year),
            "run_probability_pct": p_run * 100
        }

    return out


def collect_dashboard_snapshot():
    miners = load_miners()
    results = collect_miners(miners)

    with STATE_LOCK:
        record_history(results)
        active_runs = update_pool_runs(results)

    solopool = fetch_solopool_stats()
    braiins = fetch_braiins_stats()
    ALERT_MANAGER.process(results, solopool.get("blocks", []))

    return {
        "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "miners": results,
        "runs": active_runs,
        "odds": build_odds(results, active_runs),
        "braiins": braiins,
        "solopool": solopool,
        "system_status": {
            "thermal_management": file_recent(THERMAL_HEARTBEAT_PATH, 180),
            "miner_logging": file_recent(HISTORY_PATH, 180),
        },
    }


def refresh_dashboard_snapshot():
    global DASHBOARD_SNAPSHOT

    snapshot = collect_dashboard_snapshot()
    with SNAPSHOT_LOCK:
        DASHBOARD_SNAPSHOT = snapshot
    return snapshot


def get_dashboard_snapshot():
    with SNAPSHOT_LOCK:
        return DASHBOARD_SNAPSHOT

def empty_dashboard_snapshot():
    return {
        "updated": "Starting...",
        "miners": [],
        "runs": {},
        "odds": {},
        "braiins": {"available": False, "workers": []},
        "solopool": {"available": False, "workers": []},
        "system_status": {
            "thermal_management": file_recent(THERMAL_HEARTBEAT_PATH, 180),
            "miner_logging": file_recent(HISTORY_PATH, 180),
        },
    }

def build_page3_payload(snapshot):
    miners = snapshot.get("miners", []) or []
    odds = snapshot.get("odds", {}) or {}
    solopool = snapshot.get("solopool", {}) or {}
    braiins = snapshot.get("braiins", {}) or {}

    def pool_miners(pool):
        return [miner for miner in miners if miner.get("pool") == pool]

    def pool_hash(pool_items):
        total = 0.0
        for miner in pool_items:
            try:
                total += float(miner.get("th", 0) or 0)
            except (TypeError, ValueError):
                pass
        return total

    def is_hashing(miner):
        try:
            return bool(miner.get("online")) and float(miner.get("th", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def max_value(values):
        best = 0.0
        for value in values:
            try:
                best = max(best, float(value or 0))
            except (TypeError, ValueError):
                pass
        return best

    def best_network_pct(best, difficulty):
        try:
            best = float(best or 0)
            difficulty = float(difficulty or 0)
        except (TypeError, ValueError):
            return None
        if best <= 0 or difficulty <= 0:
            return None
        return (best / difficulty) * 100

    def odds_payload(pool_name):
        item = odds.get(pool_name, {}) or {}
        return {
            "difficulty": item.get("difficulty"),
            "hour_den": item.get("hour_den"),
            "day_den": item.get("day_den"),
            "month_den": item.get("month_den"),
            "year_den": item.get("year_den"),
            "run_probability_pct": item.get("run_probability_pct"),
        }

    btc_miners = pool_miners("Umbrel Solo")
    bch_miners = pool_miners("BCH SoloPool")
    braiins_miners = pool_miners("Braiins")
    btc_th = pool_hash(btc_miners)
    bch_th = pool_hash(bch_miners)
    braiins_th = pool_hash(braiins_miners)
    total_th = btc_th + bch_th + braiins_th

    def allocation_pct(value):
        return (value / total_th) * 100 if total_th > 0 else 0.0

    btc_odds = odds_payload("Umbrel Solo")
    bch_odds = odds_payload("BCH SoloPool")
    btc_session_best = max_value(miner.get("best_session_diff") for miner in btc_miners)
    btc_historic_best = max_value(miner.get("best_diff") for miner in btc_miners)
    bch_session_best = max_value(miner.get("best_session_diff") for miner in bch_miners)
    bch_historic_best = max_value(
        [miner.get("best_diff") for miner in bch_miners] + [solopool.get("best_share")]
    )

    workers = []
    for worker in braiins.get("workers", []) or []:
        try:
            hash_5m = float(worker.get("hash_rate_5m_th", 0) or 0)
            hash_60m = float(worker.get("hash_rate_60m_th", 0) or 0)
        except (TypeError, ValueError):
            hash_5m = 0.0
            hash_60m = 0.0
        if hash_5m > 0 or hash_60m > 0:
            workers.append({
                "name": worker.get("name", ""),
                "state": worker.get("state", "unknown"),
                "hash_rate_5m_th": hash_5m,
                "hash_rate_60m_th": hash_60m,
                "hash_rate_24h_th": worker.get("hash_rate_24h_th"),
            })
    workers.sort(key=lambda worker: worker["hash_rate_5m_th"], reverse=True)

    return {
        "updated": snapshot.get("updated"),
        "total_th": total_th,
        "allocation": {
            "btc_solo_pct": allocation_pct(btc_th),
            "bch_solo_pct": allocation_pct(bch_th),
            "braiins_pct": allocation_pct(braiins_th),
        },
        "btc_solo": {
            "pool": "Umbrel Solo",
            "coin": "BTC",
            "hashrate_th": btc_th,
            "miners": [miner.get("name", "") for miner in btc_miners],
            "online_miners": [
                miner.get("name", "")
                for miner in btc_miners
                if is_hashing(miner)
            ],
            "session_best": btc_session_best,
            "historic_best": btc_historic_best,
            "best_network_pct": best_network_pct(btc_historic_best, btc_odds["difficulty"]),
            "odds": btc_odds,
        },
        "bch_solo": {
            "pool": "BCH SoloPool",
            "coin": "BCH",
            "hashrate_th": bch_th,
            "miners": [miner.get("name", "") for miner in bch_miners],
            "online_miners": [
                miner.get("name", "")
                for miner in bch_miners
                if is_hashing(miner)
            ],
            "session_best": bch_session_best,
            "historic_best": bch_historic_best,
            "best_network_pct": best_network_pct(bch_historic_best, bch_odds["difficulty"]),
            "odds": bch_odds,
            "solopool_best_share": solopool.get("best_share"),
        },
        "braiins": {
            "pool": "Braiins",
            "coin": "BTC",
            "hashrate_th": braiins_th,
            "pool_60m_th": braiins.get("hash_rate_60m_th"),
            "pool_24h_th": braiins.get("hash_rate_24h_th"),
            "today_reward": braiins.get("today_reward"),
            "balance": braiins.get("current_balance"),
            "workers": workers,
        },
    }

def collector_loop():
    while True:
        try:
            refresh_dashboard_snapshot()
        except Exception as error:
            print(f"Dashboard collector error: {error}", flush=True)
        COLLECTOR_WAKE.wait(COLLECT_INTERVAL)
        COLLECTOR_WAKE.clear()




def serve_static_file(handler):
    try:
        rel = handler.path.replace("/static/", "", 1)

        static_root = (APP_DIR / "static").resolve()
        path = (static_root / rel).resolve()

        if not path.is_relative_to(static_root) or not path.is_file():
            handler.send_response(404)
            handler.end_headers()
            return

        body = path.read_bytes()
        content_type, _ = mimetypes.guess_type(path)
        handler.send_response(200)
        handler.send_header("Content-Type", content_type or "application/octet-stream")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        handler.wfile.write(body)

    except Exception as e:
        handler.send_response(500)
        handler.end_headers()
        handler.wfile.write(str(e).encode())


def html_page():
    return Path(__file__).with_name("static").joinpath("dashboard.html").read_text()

class Handler(BaseHTTPRequestHandler):

    def do_POST(self):
        global LAST_HISTORY_WRITE

        try:
            protected_paths = (
                "/reset_run",
                "/reset_all_runs_logs",
                "/api/discord/test",
                "/api/thermal-settings",
                "/api/miner-management/add",
                "/api/miner-management/update",
                "/api/miner-management/delete",
            )
            if self.path in protected_paths and not self.is_same_origin():
                self.send_response(403)
                self.end_headers()
                return

            if self.path == "/api/thermal-settings":
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > 16384:
                    self.send_json(400, {"ok": False, "error": "Invalid request size"})
                    return

                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(400, {"ok": False, "error": "Invalid JSON body"})
                    return
                try:
                    miner = save_thermal_settings(data)
                except ValueError as error:
                    self.send_json(400, {"ok": False, "error": str(error)})
                    return
                except LookupError as error:
                    self.send_json(404, {"ok": False, "error": str(error)})
                    return

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "miner": {
                            "name": miner["name"],
                            "enabled": miner.get("enabled", True),
                            **{field: miner[field] for field in THERMAL_FIELDS},
                        },
                    },
                )
                return

            if self.path.startswith("/api/miner-management/"):
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > 16384:
                    self.send_json(400, {"ok": False, "error": "Invalid request size"})
                    return

                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(400, {"ok": False, "error": "Invalid JSON body"})
                    return

                try:
                    if self.path == "/api/miner-management/add":
                        miner = add_miner(data)
                    elif self.path == "/api/miner-management/update":
                        miner = update_miner(data)
                    elif self.path == "/api/miner-management/delete":
                        miner = delete_miner(data)
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                except ValueError as error:
                    self.send_json(400, {"ok": False, "error": str(error)})
                    return
                except LookupError as error:
                    self.send_json(404, {"ok": False, "error": str(error)})
                    return

                self.send_json(200, {"ok": True, "miner": miner})
                return

            if self.path == "/reset_run":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body)
                pool = data.get("pool")

                now = int(time.time())

                with STATE_LOCK:
                    runs = load_runs()
                    if pool:
                        runs[pool] = {
                            "started": now,
                            "uptime_seconds": 0,
                            "downtime_seconds": 0,
                            "last_update": now,
                            "online": False,
                            "cumulative_odds": 0.0,
                            "th_seconds": 0.0
                        }
                        save_runs(runs)

                MANUAL_RESET_PATH.write_text(str(now))
                COLLECTOR_WAKE.set()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "pool": pool}).encode())
                return

            if self.path == "/reset_all_runs_logs":
                now = int(time.time())

                with STATE_LOCK:
                    for f in [RUNS_PATH, HISTORY_PATH]:
                        try:
                            Path(f).unlink()
                        except:
                            pass
                    LAST_HISTORY_WRITE = 0

                try:
                    LOG_PATH.write_text("")
                except Exception:
                    pass

                MANUAL_RESET_PATH.write_text(str(now))
                COLLECTOR_WAKE.set()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "reset_all": True}).encode())
                return

            if self.path == "/api/discord/test":
                if ALERT_MANAGER.send_test():
                    self.send_json(200, {"ok": True})
                else:
                    self.send_json(
                        502,
                        {
                            "ok": False,
                            "error": "Discord test alert could not be sent",
                        },
                    )
                return

            self.send_response(404)
            self.end_headers()

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            try:
                icon = APP_DIR / "static" / "apple-touch-icon.png"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(icon.read_bytes())
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                return

        if self.path.startswith("/static/icon"):
            try:
                icon = APP_DIR / "static" / "icon.png"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(icon.read_bytes())
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                return

        if self.path.startswith("/static/"):
            serve_static_file(self)
            return


        if self.path == "/" or self.path == "/pool":
            html = html_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if self.path == "/thermal-settings":
            body = (APP_DIR / "static" / "thermal-settings.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/miners":
            body = (APP_DIR / "static" / "miners.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/thermal-settings":
            self.send_json(200, thermal_settings_payload())
            return

        if self.path == "/api/miner-management":
            self.send_json(200, miner_management_payload())
            return

        if self.path == "/api/miners":
            payload = get_dashboard_snapshot()
            if payload is None:
                payload = empty_dashboard_snapshot()

            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/page3":
            snapshot = get_dashboard_snapshot()
            if snapshot is None:
                snapshot = empty_dashboard_snapshot()
            self.send_json(200, build_page3_payload(snapshot))
            return

        if self.path == "/api/performance":
            with STATE_LOCK:
                payload = {
                    "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "performance": get_performance()
                }

            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/download-log":
            try:
                with open(LOG_PATH, "rb") as f:
                    body = f.read()

                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=miner_thermal_mode.log")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            except Exception as e:
                body = str(e).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def is_same_origin(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        if origin:
            return origin in (f"http://{host}", f"https://{host}")
        if referer:
            return referer.startswith(f"http://{host}/") or referer.startswith(f"https://{host}/")
        return False

if __name__ == "__main__":
    print(f"Miner dashboard V2 running on port {PORT}")
    threading.Thread(target=collector_loop, name="miner-collector", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
