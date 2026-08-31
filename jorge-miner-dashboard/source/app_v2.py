from pathlib import Path
import json
import csv
import mimetypes
import os
import math
import time
import threading
import secrets
import socket
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from datetime import datetime
from zoneinfo import ZoneInfo

import benchmark_restore
import benchmark_results
import benchmark_sessions
import benchmark_profiles
import benchmark_engine
import thermal_locks
from discord_alerts import DiscordAlertManager
from miner_api import apply_settings, get_system_info, normalized_stats

APP_DIR = Path(__file__).resolve().parent
APP_START_TIME = time.time()
DATA_DIR = Path(os.environ.get("MINER_DASHBOARD_DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get("MINER_DASHBOARD_PORT", "5056"))
REQUEST_TIMEOUT = 3
FREQUENCY_TOLERANCE_MHZ = int(os.environ.get("THERMAL_FREQUENCY_TOLERANCE_MHZ", "10"))
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
BENCHMARK_RUNNER_LOCK = threading.Lock()
BENCHMARK_RUNNERS = {}
BENCHMARK_CANCEL_EVENTS = {}

RUNS_PATH = DATA_DIR / "active_mining_runs.json"
MINERS_PATH = DATA_DIR / "miners_v2.json"
PENDING_DISCOVERY_PATH = DATA_DIR / "pending_discovered_miners.json"
PAGE3_PUBLIC_TOKEN_PATH = DATA_DIR / ".page3_public_token"
PAGE3_PUBLIC_PATHS = {"/public/page3", "/public/page3-data"}
BENCHMARK_SESSIONS_PATH = DATA_DIR / "benchmark_sessions.json"
BENCHMARK_RESTORE_PATH = DATA_DIR / "benchmark_restore_profiles.json"
BENCHMARK_RESULTS_PATH = DATA_DIR / "benchmark_results.json"
BENCHMARK_REPORT_RETENTION_DAYS = 7
THERMAL_LOCKS_PATH = DATA_DIR / "thermal_locks.json"
MANUAL_RESET_PATH = DATA_DIR / "manual_reset_marker"
THERMAL_HEARTBEAT_PATH = DATA_DIR / "thermal_heartbeat"
APP_VERSION = os.environ.get("APP_VERSION", "1.2.28")
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

def write_json_file(path, payload):
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, path)

def load_pending_discovery():
    try:
        with PENDING_DISCOVERY_PATH.open("r") as f:
            pending = json.load(f)
        return pending if isinstance(pending, list) else []
    except Exception:
        return []

def ensure_page3_public_token():
    try:
        token = PAGE3_PUBLIC_TOKEN_PATH.read_text().strip()
        if token:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    PAGE3_PUBLIC_TOKEN_PATH.write_text(f"{token}\n")
    try:
        PAGE3_PUBLIC_TOKEN_PATH.chmod(0o600)
    except Exception:
        pass
    return token

def read_page3_public_token():
    try:
        return PAGE3_PUBLIC_TOKEN_PATH.read_text().strip()
    except Exception:
        return ""

def valid_page3_public_token(token):
    expected = read_page3_public_token()
    return bool(expected) and secrets.compare_digest(token or "", expected)

def page3_public_payload():
    snapshot = get_dashboard_snapshot()
    if snapshot is None:
        snapshot = empty_dashboard_snapshot()
    return build_page3_payload(snapshot)

def normalize_mac(value):
    if not isinstance(value, str):
        return ""
    chars = "".join(char.lower() for char in value if char.isalnum())
    if len(chars) != 12 or any(char not in "0123456789abcdef" for char in chars):
        return ""
    return ":".join(chars[index:index + 2] for index in range(0, 12, 2))

def find_first_key(payload, candidates):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in candidates and value not in (None, ""):
                return value
        for value in payload.values():
            found = find_first_key(value, candidates)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_first_key(value, candidates)
            if found not in (None, ""):
                return found
    return None

def extract_miner_identity(info):
    mac_keys = {
        "mac",
        "macaddr",
        "macaddress",
        "mac_address",
        "wifimac",
        "wifi_mac",
        "ethmac",
        "eth_mac",
        "sta_mac",
        "ap_mac",
    }
    hostname_keys = {"hostname", "host", "name", "device", "devicename", "device_name"}
    model_keys = {"model", "devicemodel", "device_model", "board", "asicmodel", "asic_model"}
    version_keys = {"version", "fwversion", "firmware", "firmwareversion", "firmware_version"}

    mac = normalize_mac(find_first_key(info, mac_keys))
    hostname = find_first_key(info, hostname_keys)
    model = find_first_key(info, model_keys)
    version = find_first_key(info, version_keys)

    return {
        "mac": mac,
        "hostname": str(hostname).strip()[:80] if hostname not in (None, "") else "",
        "model": str(model).strip()[:80] if model not in (None, "") else "",
        "version": str(version).strip()[:80] if version not in (None, "") else "",
    }

def detect_miner_type(info):
    text = json.dumps(info, sort_keys=True).lower()
    if "nerd" in text or "nqaxe" in text or "noctaxe" in text:
        return "nerdos"
    return "axeos"

def default_discovery_cidr():
    configured = os.environ.get("MINER_DISCOVERY_CIDR", "").strip()
    if configured:
        return configured
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            local_ip = sock.getsockname()[0]
        parts = local_ip.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3] + ["0"]) + "/24"
    except Exception:
        pass
    return "192.168.1.0/24"

def probe_miner(ip):
    try:
        info = get_system_info(ip, timeout=0.5)
        if not isinstance(info, dict):
            return None
        identity = extract_miner_identity(info)
        return {
            "ip": str(ip),
            "type": detect_miner_type(info),
            "identity": identity,
        }
    except Exception:
        return None

def scan_for_miners(cidr=None):
    cidr = cidr or default_discovery_cidr()
    network = ip_network(cidr, strict=False)
    if network.version != 4:
        raise ValueError("Discovery CIDR must be IPv4")
    hosts = list(network.hosts())
    if len(hosts) > 1024:
        raise ValueError("Discovery CIDR is too large")

    discovered = []
    with ThreadPoolExecutor(max_workers=64) as executor:
        for result in executor.map(probe_miner, hosts):
            if result:
                discovered.append(result)
    discovered.sort(key=lambda item: tuple(int(part) for part in item["ip"].split(".")))
    return discovered

def miner_identity_mac(miner):
    identity = miner.get("identity")
    if isinstance(identity, dict):
        mac = normalize_mac(identity.get("mac"))
        if mac:
            return mac
    return normalize_mac(miner.get("mac"))

def merge_identity(miner, discovered):
    identity = dict(miner.get("identity") or {})
    found = discovered.get("identity") or {}
    changed = False
    for key in ("mac", "hostname", "model", "version"):
        value = found.get(key)
        if value and identity.get(key) != value:
            identity[key] = value
            changed = True
    if identity and miner.get("identity") != identity:
        miner["identity"] = identity
        changed = True
    return changed

def discovery_display_name(item):
    identity = item.get("identity") or {}
    return identity.get("hostname") or identity.get("model") or f"Miner at {item.get('ip')}"

def filter_configured_pending_discovery(pending, miners):
    """Hide pending LAN devices already represented by configured Local API miners."""
    local_api_miners = [
        miner for miner in miners
        if configured_miner(miner)["telemetry_source"] == "LOCAL_API"
    ]
    configured_macs = {
        miner_identity_mac(miner) for miner in local_api_miners if miner_identity_mac(miner)
    }
    configured_by_ip = {
        str(miner.get("ip") or "").strip(): miner
        for miner in local_api_miners if str(miner.get("ip") or "").strip()
    }
    visible = []
    for item in pending:
        mac = miner_identity_mac(item)
        if mac and mac in configured_macs:
            continue
        ip = str(item.get("ip") or "").strip()
        configured = configured_by_ip.get(ip) if ip else None
        if configured is not None and (not mac or not miner_identity_mac(configured)):
            continue
        visible.append(item)
    return visible

def reconcile_discovered_miners(discovered):
    updates = []
    known = []
    pending = []

    with CONFIG_LOCK:
        miners = load_miners()
        local_api_miners = [miner for miner in miners if configured_miner(miner)["telemetry_source"] == "LOCAL_API"]
        by_mac = {
            miner_identity_mac(miner): miner
            for miner in local_api_miners
            if miner_identity_mac(miner)
        }
        by_ip = {miner.get("ip"): miner for miner in local_api_miners if miner.get("ip")}
        changed = False

        for item in discovered:
            identity = item.get("identity") or {}
            mac = normalize_mac(identity.get("mac"))
            miner = by_mac.get(mac) if mac else None
            matched_by = "mac" if miner else ""

            if miner is None:
                miner = by_ip.get(item["ip"])
                matched_by = "ip" if miner else ""

            if miner is not None:
                old_ip = miner.get("ip")
                if old_ip != item["ip"]:
                    miner["ip"] = item["ip"]
                    changed = True
                    updates.append({
                        "name": miner.get("name", ""),
                        "old_ip": old_ip,
                        "new_ip": item["ip"],
                        "matched_by": matched_by,
                    })
                if merge_identity(miner, item):
                    changed = True
                known.append({
                    "name": miner.get("name", ""),
                    "ip": miner.get("ip", item["ip"]),
                    "type": miner.get("type", item.get("type", "")),
                    "identity": miner.get("identity", {}),
                    "matched_by": matched_by,
                })
                continue

            pending.append({
                "name": discovery_display_name(item),
                "ip": item["ip"],
                "type": item.get("type", "axeos"),
                "identity": identity,
            })

        if changed:
            write_miners(miners)

    write_json_file(PENDING_DISCOVERY_PATH, pending)
    return {
        "ok": True,
        "discovered": len(discovered),
        "known": known,
        "updated": updates,
        "pending": pending,
    }

def discover_and_reconcile(cidr=None):
    return reconcile_discovered_miners(scan_for_miners(cidr))

def startup_discovery():
    try:
        result = discover_and_reconcile()
        if result["updated"] or result["pending"]:
            print(
                "Miner discovery: "
                f"{len(result['updated'])} IP update(s), "
                f"{len(result['pending'])} pending miner(s)",
                flush=True,
            )
    except Exception as error:
        print(f"Miner discovery error: {error}", flush=True)

THERMAL_FIELDS = (
    "base_freq",
    "base_volt",
    "hot_freq",
    "hot_volt",
    "critical_freq",
    "critical_volt",
    "warn_temp",
    "critical_temp",
    "recover_temp",
)

FREQUENCY_FIELDS = ("base_freq", "hot_freq", "critical_freq")
VOLTAGE_FIELDS = ("base_volt", "hot_volt", "critical_volt")
TEMPERATURE_FIELDS = ("warn_temp", "critical_temp", "recover_temp")

DEFAULT_THERMAL_SETTINGS = {
    "axeos": {
        "base_freq": 550,
        "base_volt": 1150,
        "hot_freq": 525,
        "hot_volt": 1150,
        "critical_freq": 500,
        "critical_volt": 1150,
        "warn_temp": 68,
        "critical_temp": 70,
        "recover_temp": 64,
    },
    "nerdos": {
        "base_freq": 700,
        "base_volt": 1200,
        "hot_freq": 650,
        "hot_volt": 1200,
        "critical_freq": 560,
        "critical_volt": 1200,
        "warn_temp": 66,
        "critical_temp": 68,
        "recover_temp": 64,
    },
}

LOCATION_SCOPES = ("LOCAL", "OFF-SITE")
TELEMETRY_SOURCES = ("LOCAL_API", "BRAIINS")
POOL_COINS = {"Braiins": "BTC", "Umbrel Solo": "BTC", "BCH SoloPool": "BCH"}

def configured_miner(miner):
    """Return a backward-compatible configured identity without mutating disk."""
    item = dict(miner)
    item["location_scope"] = str(item.get("location_scope") or "LOCAL").upper()
    item["telemetry_source"] = str(item.get("telemetry_source") or "LOCAL_API").upper()
    item["worker_name"] = str(item.get("worker_name") or item.get("name") or "").strip()
    pool = item.get("pool", "")
    if pool in POOL_COINS:
        item["coin"] = POOL_COINS[pool]
    return item

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

    telemetry_source = str(data.get("telemetry_source") or "LOCAL_API").strip().upper()
    raw_location = data.get("location_scope")
    if telemetry_source == "BRAIINS" and (not isinstance(raw_location, str) or not raw_location.strip()):
        raise ValueError("Location must be explicitly selected for a Braiins worker")
    location_scope = str(raw_location or "LOCAL").strip().upper()
    if location_scope not in LOCATION_SCOPES:
        raise ValueError("Location must be LOCAL or OFF-SITE")
    if telemetry_source not in TELEMETRY_SOURCES:
        raise ValueError("Telemetry source must be LOCAL_API or BRAIINS")

    pool = str(data.get("pool") or "").strip()
    if pool not in POOL_COINS:
        raise ValueError("Pool must be Braiins, Umbrel Solo, or BCH SoloPool")
    coin = POOL_COINS[pool]

    miner_type = str(data.get("type") or "").strip().lower()
    ip = str(data.get("ip") or "").strip()
    worker_name = str(data.get("worker_name") or name).strip()
    if telemetry_source == "LOCAL_API":
        if miner_type not in DEFAULT_THERMAL_SETTINGS:
            raise ValueError("Miner OS must be AxeOS or NerdOS")
        if not ip:
            raise ValueError("IP address is required for Local miner API telemetry")
        try:
            parsed_ip = ip_address(ip)
        except ValueError:
            raise ValueError("IP address is invalid")
        if parsed_ip.version != 4 or parsed_ip.is_unspecified or parsed_ip.is_multicast:
            raise ValueError("IP address must be a usable IPv4 address")
    else:
        if pool != "Braiins":
            raise ValueError("Braiins worker telemetry requires the Braiins pool")
        if not worker_name:
            raise ValueError("Braiins worker name is required")
        miner_type = ""
        ip = ""

    identity = data.get("identity")
    clean_identity = {}
    if isinstance(identity, dict):
        mac = normalize_mac(identity.get("mac"))
        if mac:
            clean_identity["mac"] = mac
        for key in ("hostname", "model", "version"):
            value = identity.get(key)
            if isinstance(value, str) and value.strip():
                clean_identity[key] = value.strip()[:80]

    return {
        "target_name": target_name,
        "name": name,
        "type": miner_type,
        "ip": ip,
        "pool": pool,
        "coin": coin,
        "location_scope": location_scope,
        "telemetry_source": telemetry_source,
        "worker_name": worker_name,
        "identity": clean_identity,
    }

def miner_management_payload():
    with CONFIG_LOCK:
        miners = [configured_miner(miner) for miner in load_miners()]
    pending = filter_configured_pending_discovery(load_pending_discovery(), miners)
    braiins = fetch_braiins_stats()
    adopted = {miner["worker_name"].casefold() for miner in miners
               if miner.get("telemetry_source") == "BRAIINS" or miner.get("pool") == "Braiins"}
    available = [worker for worker in (braiins or {}).get("workers", []) or []
                 if str(worker.get("name", "")).strip().casefold() not in adopted]

    return {
        "pending": pending,
        "miners": [
            {
                "name": miner.get("name", ""),
                "type": miner.get("type", ""),
                "ip": miner.get("ip", ""),
                "pool": miner.get("pool", ""),
                "coin": miner.get("coin", ""),
                "enabled": miner.get("enabled", True),
                "identity": miner.get("identity", {}),
                "location_scope": miner["location_scope"],
                "telemetry_source": miner["telemetry_source"],
                "worker_name": miner["worker_name"],
            }
            for miner in miners
        ],
        "available_braiins_workers": available,
    }

def add_miner(data):
    settings = validate_miner_identity(data)

    with CONFIG_LOCK:
        miners = load_miners()
        if any(miner.get("name") == settings["name"] for miner in miners):
            raise ValueError("Miner name already exists")
        if settings["ip"] and any(miner.get("ip") == settings["ip"] for miner in miners):
            raise ValueError("IP address already exists")

        worker_key = settings["worker_name"].casefold()
        if settings["telemetry_source"] == "BRAIINS" and any(
            configured_miner(miner)["telemetry_source"] == "BRAIINS"
            and configured_miner(miner)["worker_name"].casefold() == worker_key for miner in miners
        ):
            raise ValueError("Braiins worker is already adopted")

        defaults = DEFAULT_THERMAL_SETTINGS.get(settings["type"], {})
        miner = {
            "name": settings["name"],
            "type": settings["type"],
            "ip": settings["ip"],
            "pool": settings["pool"],
            "coin": settings["coin"],
            "enabled": False,
            "location_scope": settings["location_scope"],
            "telemetry_source": settings["telemetry_source"],
            "worker_name": settings["worker_name"],
            **defaults,
        }
        if settings["identity"]:
            miner["identity"] = settings["identity"]
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
            settings["ip"] and miner is not matches[0] and miner.get("ip") == settings["ip"]
            for miner in miners
        ):
            raise ValueError("IP address already exists")
        if settings["telemetry_source"] == "BRAIINS" and any(
            miner is not matches[0]
            and configured_miner(miner)["telemetry_source"] == "BRAIINS"
            and configured_miner(miner)["worker_name"].casefold() == settings["worker_name"].casefold()
            for miner in miners
        ):
            raise ValueError("Braiins worker is already adopted")

        miner = matches[0]
        miner["name"] = settings["name"]
        miner["type"] = settings["type"]
        miner["ip"] = settings["ip"]
        miner["pool"] = settings["pool"]
        miner["coin"] = settings["coin"]
        miner["location_scope"] = settings["location_scope"]
        miner["telemetry_source"] = settings["telemetry_source"]
        miner["worker_name"] = settings["worker_name"]
        if settings["telemetry_source"] == "BRAIINS":
            miner["enabled"] = False
        if settings["identity"]:
            miner["identity"] = settings["identity"]
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
    base_volt = data.get("base_volt")
    for field in THERMAL_FIELDS:
        value = data.get(field)
        if field in ("hot_volt", "critical_volt") and value is None:
            value = base_volt
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        settings[field] = value

    for field in FREQUENCY_FIELDS:
        value = settings[field]
        if not value.is_integer() or not 1 <= value <= 2000:
            raise ValueError(f"{field} must be a whole number between 1 and 2000")
        settings[field] = int(value)

    for field in VOLTAGE_FIELDS:
        value = settings[field]
        if not value.is_integer() or not 1 <= value <= 2000:
            raise ValueError(f"{field} must be a whole number between 1 and 2000")
        settings[field] = int(value)

    for field in TEMPERATURE_FIELDS:
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
        if configured_miner(miner)["telemetry_source"] != "LOCAL_API":
            raise ValueError("Thermal management requires Local miner API telemetry")
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
        miner = configured_miner(miner)
        if miner["telemetry_source"] != "LOCAL_API":
            continue
        current = live_by_name.get(miner.get("name"), {})
        base_volt = miner.get("base_volt")
        miners.append({
            "name": miner.get("name", ""),
            "type": miner.get("type", ""),
            "ip": miner.get("ip", ""),
            "enabled": miner.get("enabled", True),
            "base_freq": miner.get("base_freq"),
            "base_volt": base_volt,
            "hot_freq": miner.get("hot_freq"),
            "hot_volt": miner.get("hot_volt", base_volt),
            "critical_freq": miner.get("critical_freq"),
            "critical_volt": miner.get("critical_volt", base_volt),
            "warn_temp": miner.get("warn_temp"),
            "critical_temp": miner.get("critical_temp"),
            "recover_temp": miner.get("recover_temp"),
            "current_temp": current.get("temp"),
            "current_freq": current.get("freq"),
            "status": current.get("status", "OFFLINE"),
            "online": current.get("online", False),
        })
    return {"miners": miners, "check_interval_seconds": 60}

def find_configured_miner(name):
    with CONFIG_LOCK:
        miners = load_miners()
    matches = [miner for miner in miners if miner.get("name") == name]
    if len(matches) != 1:
        raise LookupError("Miner was not found or its name is not unique")
    miner = configured_miner(matches[0])
    if not benchmark_eligible(miner):
        raise ValueError("Benchmark requires a supported Local miner API")
    return miner

def benchmark_eligible(miner):
    item = configured_miner(miner)
    return item["telemetry_source"] == "LOCAL_API" and item.get("type") in DEFAULT_THERMAL_SETTINGS

def benchmark_timing_payload(profile):
    timing = profile.get("timing", {})
    warmup_seconds = int(timing.get("warmup_seconds", 0) or 0)
    test_seconds = int(timing.get("test_seconds", 0) or 0)
    sample_interval_seconds = int(timing.get("sample_interval_seconds", 0) or 0)
    return {
        "warmup_seconds": warmup_seconds,
        "test_seconds": test_seconds,
        "sample_interval_seconds": sample_interval_seconds,
        "candidate_seconds": warmup_seconds + test_seconds,
    }

def ensure_no_benchmark_recovery_required():
    pending = benchmark_restore.recovery_required_profiles(BENCHMARK_RESTORE_PATH)
    if pending:
        session_id = pending[0].get("session_id", "unknown")
        raise ValueError(
            f"Benchmark restore recovery is required for session {session_id}"
        )

def start_benchmark_session(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    name = data.get("miner")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Miner name is required")
    ensure_no_benchmark_recovery_required()
    miner = find_configured_miner(name.strip())
    session = benchmark_sessions.create_session(
        BENCHMARK_SESSIONS_PATH,
        miner["name"],
    )
    session_id = session["session_id"]
    restore_created = False
    lock_created = False
    try:
        stats = normalized_stats(miner, timeout=REQUEST_TIMEOUT)
        profile = benchmark_profiles.select_profile(miner, stats)
        baseline = {
            "frequency": stats.get("freq"),
            "voltage": stats.get("volt"),
            "base_frequency": miner.get("base_freq"),
            "base_voltage": miner.get("base_volt"),
            "telemetry": benchmark_engine.sample_summary([stats]),
        }
        plan = benchmark_engine.dry_run_plan(profile, baseline=baseline)
        benchmark_results.save_planned_results(
            BENCHMARK_RESULTS_PATH,
            session_id,
            plan["candidates"],
            created_at=session.get("created_at"),
        )
        benchmark_sessions.update_session(
            BENCHMARK_SESSIONS_PATH,
            session_id,
            {
                "device_profile": profile["id"],
                "device_profile_label": profile["label"],
                "benchmark_plan": {
                    "mode": plan["mode"],
                    "writes_enabled": plan["writes_enabled"],
                    "candidate_count": len(plan["candidates"]),
                    "first_candidate": plan["candidates"][0] if plan["candidates"] else None,
                    "baseline": baseline,
                    "timing": benchmark_timing_payload(profile),
                },
            },
        )
        benchmark_restore.save_restore_profile(
            BENCHMARK_RESTORE_PATH,
            session_id,
            miner,
            stats,
            created_at=session.get("created_at"),
        )
        restore_created = True
        thermal_locks.create_lock(
            THERMAL_LOCKS_PATH,
            miner["name"],
            locked_by="benchmark",
            session_id=session_id,
            created_at=session.get("created_at"),
        )
        lock_created = True
        completed = benchmark_sessions.complete_read_only_session(
            BENCHMARK_SESSIONS_PATH,
            session_id,
        )
        benchmark_restore.mark_restore_profile(
            BENCHMARK_RESTORE_PATH,
            session_id,
            "restored",
            completed_at=completed.get("completed_at"),
            reason="read_only_completed",
        )
        thermal_locks.release_lock(
            THERMAL_LOCKS_PATH,
            miner["name"],
            session_id=session_id,
        )
        return completed
    except Exception as error:
        if lock_created:
            thermal_locks.release_lock(
                THERMAL_LOCKS_PATH,
                miner["name"],
                session_id=session_id,
            )
        if restore_created:
            try:
                benchmark_restore.mark_restore_profile(
                    BENCHMARK_RESTORE_PATH,
                    session_id,
                    "failed",
                    reason=str(error),
                )
            except Exception:
                pass
        try:
            benchmark_sessions.transition_session(
                BENCHMARK_SESSIONS_PATH,
                session_id,
                "failed",
                reason=str(error),
            )
        except Exception:
            pass
        raise

def cancel_benchmark_session(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Session ID is required")
    canceled = benchmark_sessions.cancel_session(
        BENCHMARK_SESSIONS_PATH,
        session_id.strip(),
    )
    thermal_locks.release_lock(
        THERMAL_LOCKS_PATH,
        canceled.get("miner", ""),
        session_id=canceled.get("session_id"),
    )
    try:
        benchmark_restore.mark_restore_profile(
            BENCHMARK_RESTORE_PATH,
            canceled["session_id"],
            "canceled",
            completed_at=canceled.get("completed_at"),
            reason="canceled_by_user",
        )
    except LookupError:
        pass
    return canceled

def cancel_active_benchmark_session(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Session ID is required")
    session_id = session_id.strip()

    with BENCHMARK_RUNNER_LOCK:
        cancel_event = BENCHMARK_CANCEL_EVENTS.get(session_id)
        if cancel_event:
            cancel_event.set()

    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") in benchmark_sessions.TERMINAL_STATES:
        raise ValueError("Benchmark session is already finished")

    restore_profile = benchmark_restore.get_restore_profile(
        BENCHMARK_RESTORE_PATH,
        session_id,
    )
    if session.get("settings_written") and not isinstance(restore_profile, dict):
        raise LookupError("Restore profile was not found")

    canceling = benchmark_sessions.transition_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        "canceling",
        reason="canceled_by_user",
    )
    restored = False
    if canceling.get("settings_written"):
        try:
            restore_benchmark_miner(restore_profile)
        except Exception as error:
            mark_benchmark_restore_recovery_required(
                session_id,
                error,
                "cancel",
            )
            raise
        restored = True

    for row in benchmark_results.session_results(BENCHMARK_RESULTS_PATH, session_id):
        if row.get("status") in ("planned", "applied"):
            benchmark_results.update_candidate_result(
                BENCHMARK_RESULTS_PATH,
                session_id,
                row.get("sequence"),
                {
                    "status": "canceled",
                    "safety_decision": "CANCELED_BY_USER",
                },
            )

    lock_released = thermal_locks.release_lock(
        THERMAL_LOCKS_PATH,
        canceling.get("miner", ""),
        session_id=session_id,
    )
    if restored and not lock_released:
        error = RuntimeError("matching benchmark thermal lock could not be released")
        mark_benchmark_restore_recovery_required(session_id, error, "cancel")
        raise error
    canceled = benchmark_sessions.transition_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        "canceled",
        reason="canceled_by_user",
    )
    if isinstance(restore_profile, dict):
        benchmark_restore.mark_restore_profile(
            BENCHMARK_RESTORE_PATH,
            session_id,
            "restored" if restored else "canceled",
            completed_at=canceled.get("completed_at"),
            reason="canceled_by_user",
        )
    return {
        "session": canceled,
        "restored": restored,
    }

def prepare_benchmark_session(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    name = data.get("miner")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Miner name is required")
    ensure_no_benchmark_recovery_required()
    miner = find_configured_miner(name.strip())
    session = benchmark_sessions.create_session(
        BENCHMARK_SESSIONS_PATH,
        miner["name"],
    )
    session_id = session["session_id"]
    restore_created = False
    lock_created = False
    try:
        stats = normalized_stats(miner, timeout=REQUEST_TIMEOUT)
        profile = benchmark_profiles.select_profile(miner, stats)
        baseline = {
            "frequency": stats.get("freq"),
            "voltage": stats.get("volt"),
            "base_frequency": miner.get("base_freq"),
            "base_voltage": miner.get("base_volt"),
            "telemetry": benchmark_engine.sample_summary([stats]),
        }
        plan = benchmark_engine.dry_run_plan(profile, baseline=baseline)
        benchmark_results.save_planned_results(
            BENCHMARK_RESULTS_PATH,
            session_id,
            plan["candidates"],
            created_at=session.get("created_at"),
        )
        benchmark_sessions.update_session(
            BENCHMARK_SESSIONS_PATH,
            session_id,
            {
                "mode": "active_prepare",
                "device_profile": profile["id"],
                "device_profile_label": profile["label"],
                "benchmark_plan": {
                    "mode": plan["mode"],
                    "writes_enabled": False,
                    "candidate_count": len(plan["candidates"]),
                    "first_candidate": plan["candidates"][0] if plan["candidates"] else None,
                    "baseline": baseline,
                    "timing": benchmark_timing_payload(profile),
                },
            },
        )
        benchmark_restore.save_restore_profile(
            BENCHMARK_RESTORE_PATH,
            session_id,
            miner,
            stats,
            created_at=session.get("created_at"),
        )
        restore_created = True
        thermal_locks.create_lock(
            THERMAL_LOCKS_PATH,
            miner["name"],
            locked_by="benchmark",
            session_id=session_id,
            created_at=session.get("created_at"),
        )
        lock_created = True
        return benchmark_sessions.transition_session(
            BENCHMARK_SESSIONS_PATH,
            session_id,
            "benchmarking",
            reason="prepared_for_manual_candidate_runs",
        )
    except Exception as error:
        if lock_created:
            thermal_locks.release_lock(
                THERMAL_LOCKS_PATH,
                miner["name"],
                session_id=session_id,
            )
        if restore_created:
            try:
                benchmark_restore.mark_restore_profile(
                    BENCHMARK_RESTORE_PATH,
                    session_id,
                    "failed",
                    reason=str(error),
                )
            except Exception:
                pass
        try:
            benchmark_sessions.transition_session(
                BENCHMARK_SESSIONS_PATH,
                session_id,
                "failed",
                reason=str(error),
            )
        except Exception:
            pass
        raise

def run_benchmark_candidate(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Session ID is required")
    sequence = data.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("Candidate sequence must be a positive integer")
    session_id = session_id.strip()
    if data.get("blocking") is True:
        with BENCHMARK_RUNNER_LOCK:
            existing = BENCHMARK_RUNNERS.get(session_id)
            if isinstance(existing, dict) and existing.get("status") == "running":
                raise ValueError("Benchmark runner is already running")
            BENCHMARK_RUNNERS[session_id] = {
                "session_id": session_id, "sequence": sequence,
                "status": "running", "mode": "manual",
            }
        try:
            return sample_benchmark_candidate(session_id, sequence)
        finally:
            with BENCHMARK_RUNNER_LOCK:
                runner = BENCHMARK_RUNNERS.get(session_id)
                if isinstance(runner, dict):
                    runner["status"] = "completed"
    return start_benchmark_candidate_runner(session_id, sequence)

def run_full_benchmark(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("Session ID is required")
    return start_full_benchmark_runner(session_id)

def benchmark_runner_payload(session_id=None):
    with BENCHMARK_RUNNER_LOCK:
        if session_id:
            runner = BENCHMARK_RUNNERS.get(session_id)
            return dict(runner) if isinstance(runner, dict) else None
        return {
            key: dict(value)
            for key, value in BENCHMARK_RUNNERS.items()
            if isinstance(value, dict)
        }

def persistent_benchmark_runner(session_id, runner):
    return benchmark_sessions.update_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        {"runner": dict(runner)},
    )

def update_persistent_benchmark_runner(session_id, updates):
    session = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH).get(
        session_id, {}
    )
    runner = session.get("runner") if isinstance(session, dict) else None
    if not isinstance(runner, dict):
        return None
    runner = dict(runner)
    runner.update(updates)
    persistent_benchmark_runner(session_id, runner)
    return runner

def start_benchmark_candidate_runner(session_id, sequence):
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") not in benchmark_sessions.ACTIVE_STATES:
        raise ValueError("Benchmark session is not active")
    benchmark_results.candidate_result(
        BENCHMARK_RESULTS_PATH,
        session_id,
        int(sequence),
    )

    with BENCHMARK_RUNNER_LOCK:
        existing = BENCHMARK_RUNNERS.get(session_id)
        if isinstance(existing, dict) and existing.get("status") == "running":
            raise ValueError("Benchmark candidate is already running")
        runner = {
            "session_id": session_id,
            "sequence": int(sequence),
            "status": "running",
            "started_at": datetime.now(TZ).isoformat(),
        }
        persistent_benchmark_runner(session_id, runner)
        BENCHMARK_RUNNERS[session_id] = runner

    def worker():
        try:
            result = sample_benchmark_candidate(session_id, sequence)
            update = {
                "status": "completed",
                "completed_at": datetime.now(TZ).isoformat(),
                "result": result,
            }
        except Exception as error:
            failed_session = benchmark_sessions.load_sessions(
                BENCHMARK_SESSIONS_PATH
            ).get(session_id, {})
            restore_profile = benchmark_restore.get_restore_profile(
                BENCHMARK_RESTORE_PATH,
                session_id,
            ) or {}
            update = {
                "status": "failed",
                "completed_at": datetime.now(TZ).isoformat(),
                "error": str(error),
                "session_reason": failed_session.get("reason"),
                "restore_status": restore_profile.get("status"),
                "recovery_required": bool(
                    restore_profile.get("recovery_required")
                ),
            }
        persisted_runner = dict(runner)
        persisted_runner.update(update)
        persistent_benchmark_runner(session_id, persisted_runner)
        with BENCHMARK_RUNNER_LOCK:
            current = BENCHMARK_RUNNERS.get(session_id)
            if isinstance(current, dict):
                current.update(update)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return {
        "ok": True,
        "runner": benchmark_runner_payload(session_id),
    }

def verify_benchmark_restore(profile, sample_provider=None, sleep_fn=time.sleep, attempts=6):
    restore = profile.get("restore", {})
    miner = {
        "name": profile.get("miner"),
        "ip": profile.get("ip"),
        "type": profile.get("device_type"),
    }
    provider = sample_provider or (lambda: normalized_stats(miner, timeout=REQUEST_TIMEOUT))
    last_error = "restore verification telemetry unavailable"
    for attempt in range(max(1, attempts)):
        try:
            sample = provider()
            frequency = sample.get("freq") if isinstance(sample, dict) else None
            voltage = sample.get("volt") if isinstance(sample, dict) else None
            if frequency is None or abs(float(frequency) - float(restore.get("frequency"))) > FREQUENCY_TOLERANCE_MHZ:
                last_error = "restored frequency could not be verified"
            elif voltage is None or abs(float(voltage) - float(restore.get("voltage"))) > 10:
                last_error = "restored voltage could not be verified"
            else:
                return sample
        except Exception as error:
            last_error = str(error)
        if attempt + 1 < attempts:
            sleep_fn(5)
    raise RuntimeError(last_error)

def restore_and_verify_benchmark_miner(profile, sample_provider=None):
    response = restore_benchmark_miner(profile)
    verification = verify_benchmark_restore(profile, sample_provider=sample_provider)
    return {"response": response, "verification": verification}

def restore_candidate_for_continuation(session_id, profile, benchmark_profile):
    try:
        restored = restore_and_verify_benchmark_miner(profile)
        decision = benchmark_engine.safety_failure(
            benchmark_profile, restored.get("verification")
        )
        if decision:
            raise RuntimeError(f"restored baseline is unsafe: {decision}")
        benchmark_sessions.update_session(
            BENCHMARK_SESSIONS_PATH, session_id, {"settings_written": False}
        )
        return restored
    except Exception as error:
        mark_benchmark_restore_recovery_required(
            session_id, error, "candidate_abort_continuation"
        )
        raise

def complete_full_benchmark(session_id, restore_profile):
    benchmark_sessions.transition_session(BENCHMARK_SESSIONS_PATH, session_id, "restoring")
    try:
        restore_and_verify_benchmark_miner(restore_profile)
        if not thermal_locks.release_lock(
            THERMAL_LOCKS_PATH, restore_profile.get("miner", ""), session_id=session_id
        ):
            raise RuntimeError("matching benchmark thermal lock could not be released")
    except Exception as error:
        mark_benchmark_restore_recovery_required(session_id, error, "full_run_completion")
        raise
    benchmark_restore.mark_restore_profile(
        BENCHMARK_RESTORE_PATH, session_id, "restored", reason="full_run_completed"
    )
    completed = benchmark_sessions.transition_session(
        BENCHMARK_SESSIONS_PATH, session_id, "completed", reason="full_run_completed"
    )
    recommendations = benchmark_engine.recommendation_summary(
        benchmark_results.session_results(BENCHMARK_RESULTS_PATH, session_id),
        benchmark_profiles.get_profile(completed.get("device_profile")),
        baseline=completed.get("benchmark_plan", {}).get("baseline"),
    )
    return benchmark_sessions.update_session(
        BENCHMARK_SESSIONS_PATH, session_id, {"recommendations": recommendations}
    )

def start_full_benchmark_runner(session_id):
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") != "benchmarking":
        raise ValueError("Benchmark session is not ready for a full run")
    all_rows = benchmark_results.session_results(BENCHMARK_RESULTS_PATH, session_id)
    planned = [
        row for row in all_rows
        if row.get("status") == "planned"
    ]
    if not planned:
        raise ValueError("No planned benchmark candidates remain")
    with BENCHMARK_RUNNER_LOCK:
        existing = BENCHMARK_RUNNERS.get(session_id)
        if isinstance(existing, dict) and existing.get("status") == "running":
            raise ValueError("Benchmark runner is already running")
        cancel_event = threading.Event()
        BENCHMARK_CANCEL_EVENTS[session_id] = cancel_event
        runner = {
            "session_id": session_id,
            "sequence": planned[0]["sequence"],
            "status": "running",
            "mode": "full",
            "completed_candidates": len(all_rows) - len(planned),
            "total_candidates": len(all_rows),
            "started_at": datetime.now(TZ).isoformat(),
        }
        persistent_benchmark_runner(session_id, runner)
        BENCHMARK_RUNNERS[session_id] = runner

    def worker():
        update = {}
        try:
            initially_finished = len(all_rows) - len(planned)
            for index, row in enumerate(planned, 1):
                if cancel_event.is_set():
                    raise ValueError("Benchmark canceled by user")
                progress = {
                    "sequence": row["sequence"],
                    "completed_candidates": initially_finished + index - 1,
                }
                update_persistent_benchmark_runner(session_id, progress)
                with BENCHMARK_RUNNER_LOCK:
                    BENCHMARK_RUNNERS[session_id].update(progress)
                sample_benchmark_candidate(
                    session_id, row["sequence"], full_run=True, cancel_event=cancel_event
                )
            restore_profile = benchmark_restore.get_restore_profile(
                BENCHMARK_RESTORE_PATH, session_id
            )
            completed = complete_full_benchmark(session_id, restore_profile)
            update = {
                "status": "completed", "completed_candidates": len(all_rows),
                "completed_at": datetime.now(TZ).isoformat(),
                "recommendations": completed.get("recommendations"),
            }
        except Exception as error:
            current = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH).get(session_id, {})
            update = {
                "status": "canceled" if current.get("state") == "canceled" else "failed",
                "completed_at": datetime.now(TZ).isoformat(), "error": str(error),
                "recovery_required": bool(current.get("recovery_required")),
            }
        finally:
            latest_session = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH).get(session_id, {})
            persisted = dict(latest_session.get("runner") or runner)
            persisted.update(update)
            persistent_benchmark_runner(session_id, persisted)
            with BENCHMARK_RUNNER_LOCK:
                current_runner = BENCHMARK_RUNNERS.get(session_id)
                if isinstance(current_runner, dict):
                    current_runner.update(update)
                BENCHMARK_CANCEL_EVENTS.pop(session_id, None)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "runner": benchmark_runner_payload(session_id)}

def restore_benchmark_miner(profile):
    restore = profile.get("restore") if isinstance(profile, dict) else None
    if not isinstance(restore, dict):
        raise ValueError("Restore profile is missing restore settings")
    frequency = restore.get("frequency")
    voltage = restore.get("voltage")
    if frequency is None or voltage is None:
        raise ValueError("Restore profile is missing frequency or voltage")
    miner = {
        "name": profile.get("miner"),
        "ip": profile.get("ip"),
        "type": profile.get("device_type"),
    }
    if not miner["ip"] or not miner["type"]:
        raise ValueError("Restore profile is missing miner identity")
    return apply_settings(
        miner,
        frequency=frequency,
        voltage=voltage,
        timeout=REQUEST_TIMEOUT,
    )

def mark_benchmark_restore_recovery_required(session_id, error, context):
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    reason = f"{context}_restore_failed: {error}"
    benchmark_restore.mark_recovery_required(
        BENCHMARK_RESTORE_PATH,
        session_id,
        reason,
    )
    failed = benchmark_sessions.transition_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        "failed",
        reason=reason,
    )
    return benchmark_sessions.update_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        {
            "recovery_required": True,
            "restore_error": str(error),
            "restore_context": context,
            "completed_at": failed.get("completed_at"),
        },
    )

def resolve_benchmark_restore_recovery(session_id, reason):
    profile = benchmark_restore.get_restore_profile(BENCHMARK_RESTORE_PATH, session_id)
    if not isinstance(profile, dict) or not profile.get("recovery_required"):
        raise ValueError("Benchmark restore recovery is not required")
    lock = thermal_locks.active_lock_for(
        profile.get("miner", ""),
        thermal_locks.load_locks(THERMAL_LOCKS_PATH),
    )
    if not lock or str(lock.get("session_id", "")).strip() != session_id:
        raise ValueError("Matching benchmark thermal lock was not found")
    released = thermal_locks.release_lock(
        THERMAL_LOCKS_PATH,
        profile.get("miner", ""),
        session_id=session_id,
    )
    if not released:
        raise ValueError("Matching benchmark thermal lock could not be released")
    restored = benchmark_restore.mark_restore_profile(
        BENCHMARK_RESTORE_PATH,
        session_id,
        "restored",
        reason=reason,
    )
    session = benchmark_sessions.update_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        {
            "recovery_required": False,
            "recovery_resolution": reason,
            "recovery_resolved_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    return {"session": session, "restore_profile": restored, "lock_released": True}

def retry_benchmark_restore(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("Session ID is required")
    profile = benchmark_restore.get_restore_profile(BENCHMARK_RESTORE_PATH, session_id)
    if not isinstance(profile, dict) or not profile.get("recovery_required"):
        raise ValueError("Benchmark restore recovery is not required")
    try:
        restore_benchmark_miner(profile)
    except Exception as error:
        mark_benchmark_restore_recovery_required(session_id, error, "retry")
        raise
    return resolve_benchmark_restore_recovery(session_id, "automatic_restore_retry_succeeded")

def confirm_manual_benchmark_restore(data):
    if not isinstance(data, dict):
        raise ValueError("Benchmark request must be a JSON object")
    session_id = str(data.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("Session ID is required")
    return resolve_benchmark_restore_recovery(session_id, "manual_restore_confirmed")

def recover_benchmark_sessions():
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = benchmark_sessions.active_session(sessions)
    if not session:
        pending = benchmark_restore.recovery_required_profiles(BENCHMARK_RESTORE_PATH)
        if pending:
            return {
                "recovered": False,
                "reason": "manual_cleanup_required",
                "session_id": pending[0].get("session_id"),
            }
        return {"recovered": False, "reason": "no_active_session"}

    session_id = session.get("session_id")
    miner_name = session.get("miner", "")
    state = session.get("state")
    reason = f"recovered_after_restart_from_{state}"
    restored_settings = False

    stored_runner = session.get("runner")
    if isinstance(stored_runner, dict) and stored_runner.get("status") == "running":
        interrupted_runner = dict(stored_runner)
        interrupted_runner.update({
            "status": "failed",
            "completed_at": datetime.now(TZ).isoformat(),
            "error": "Benchmark runner interrupted by dashboard restart",
            "session_reason": reason,
        })
        persistent_benchmark_runner(session_id, interrupted_runner)

    try:
        profile = benchmark_restore.get_restore_profile(BENCHMARK_RESTORE_PATH, session_id)
        if session.get("settings_written"):
            if not isinstance(profile, dict):
                raise LookupError("Restore profile was not found")
            restore_benchmark_miner(profile)
            restored_settings = True

        if state == "canceling":
            recovered = benchmark_sessions.transition_session(
                BENCHMARK_SESSIONS_PATH,
                session_id,
                "canceled",
                reason=reason,
            )
            restore_status = "canceled"
        else:
            recovered = benchmark_sessions.transition_session(
                BENCHMARK_SESSIONS_PATH,
                session_id,
                "failed",
                reason=reason,
            )
            restore_status = "restored" if restored_settings else "failed"

        if isinstance(profile, dict):
            try:
                benchmark_restore.mark_restore_profile(
                    BENCHMARK_RESTORE_PATH,
                    session_id,
                    restore_status,
                    completed_at=recovered.get("completed_at"),
                    reason=reason,
                )
            except LookupError:
                pass
        lock_released = thermal_locks.release_lock(
            THERMAL_LOCKS_PATH,
            miner_name,
            session_id=session_id,
        )
        if session.get("settings_written") and not lock_released:
            raise RuntimeError("matching benchmark thermal lock could not be released")
        update_persistent_benchmark_runner(
            session_id,
            {
                "restore_status": restore_status,
                "recovery_required": False,
                "session_reason": reason,
            },
        )
        return {
            "recovered": True,
            "session_id": session_id,
            "state": recovered.get("state"),
            "restored_settings": restored_settings,
        }
    except Exception as error:
        mark_benchmark_restore_recovery_required(
            session_id,
            error,
            "restart_recovery",
        )
        update_persistent_benchmark_runner(
            session_id,
            {
                "restore_status": "failed",
                "recovery_required": True,
                "session_reason": f"restart_recovery_restore_failed: {error}",
            },
        )
        return {
            "recovered": False,
            "reason": "manual_cleanup_required",
            "session_id": session_id,
            "restored_settings": False,
            "error": str(error),
        }

def cleanup_benchmark_reports(retention_days=BENCHMARK_REPORT_RETENTION_DAYS):
    recovery_profiles = benchmark_restore.recovery_required_profiles(
        BENCHMARK_RESTORE_PATH
    )
    pruned = benchmark_sessions.prune_terminal_sessions(
        BENCHMARK_SESSIONS_PATH,
        retention_days=retention_days,
        preserve_session_ids={
            profile.get("session_id")
            for profile in recovery_profiles
            if profile.get("session_id")
        },
    )
    if not pruned:
        return {"pruned": []}

    results = benchmark_results.load_results(BENCHMARK_RESULTS_PATH)
    if isinstance(results, dict):
        for session_id in pruned:
            results.pop(session_id, None)
        benchmark_results.write_results(BENCHMARK_RESULTS_PATH, results)

    restore_profiles = benchmark_restore.load_restore_profiles(BENCHMARK_RESTORE_PATH)
    if isinstance(restore_profiles, dict):
        for session_id in pruned:
            restore_profiles.pop(session_id, None)
        benchmark_restore.write_restore_profiles(BENCHMARK_RESTORE_PATH, restore_profiles)

    return {"pruned": pruned}

def benchmark_status_payload():
    cleanup_benchmark_reports()
    payload = benchmark_sessions.sessions_payload(BENCHMARK_SESSIONS_PATH)
    payload["profiles"] = benchmark_profiles.all_profiles()
    payload["recovery_required"] = benchmark_restore.recovery_required_profiles(
        BENCHMARK_RESTORE_PATH
    )
    payload["runner"] = None
    active = payload.get("active")
    if active:
        payload["runner"] = (
            benchmark_runner_payload(active.get("session_id"))
            or active.get("runner")
        )
    else:
        payload["runner"] = next(
            (
                session.get("runner")
                for session in payload["sessions"]
                if isinstance(session, dict) and isinstance(session.get("runner"), dict)
            ),
            None,
        )
    payload["active_results"] = (
        benchmark_results.report_payload(
            BENCHMARK_RESULTS_PATH,
            active.get("session_id"),
        )
        if active else None
    )
    payload["results"] = {
        session["session_id"]: benchmark_results.report_payload(
            BENCHMARK_RESULTS_PATH,
            session["session_id"],
        )
        for session in payload["sessions"]
        if isinstance(session, dict) and session.get("session_id")
    }
    latest = payload["sessions"][0] if payload["sessions"] else None
    payload["latest_report"] = (
        benchmark_report_payload(latest.get("session_id")) if latest else None
    )
    return payload

def benchmark_report_payload(session_id):
    cleanup_benchmark_reports()
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Session ID is required")
    session_id = session_id.strip()
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    profile = None
    if session.get("device_profile"):
        profile = benchmark_profiles.get_profile(session["device_profile"])
    restore_profile = benchmark_restore.get_restore_profile(
        BENCHMARK_RESTORE_PATH,
        session_id,
    )
    return benchmark_results.export_report(
        BENCHMARK_RESULTS_PATH,
        session,
        restore_profile=restore_profile,
        profile=profile,
        recommendations=session.get("recommendations"),
    )

def guarded_benchmark_setting_write(session_id, sequence, prewrite_sample):
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") not in benchmark_sessions.ACTIVE_STATES:
        raise ValueError("Benchmark session is not active")

    profile_id = session.get("device_profile")
    if not profile_id:
        raise ValueError("Benchmark session is missing device profile")
    profile = benchmark_profiles.get_profile(profile_id)

    candidate = benchmark_results.candidate_result(
        BENCHMARK_RESULTS_PATH,
        session_id,
        int(sequence),
    )
    if not isinstance(candidate, dict):
        raise LookupError("Benchmark candidate result was not found")
    benchmark_profiles.validate_setting(
        profile,
        candidate.get("frequency"),
        candidate.get("voltage"),
    )

    restore_profile = benchmark_restore.get_restore_profile(
        BENCHMARK_RESTORE_PATH,
        session_id,
    )
    if not isinstance(restore_profile, dict):
        raise LookupError("Restore profile was not found")
    if restore_profile.get("status") != "active":
        raise ValueError("Restore profile is not active")

    locks = thermal_locks.load_locks(THERMAL_LOCKS_PATH)
    lock = thermal_locks.active_lock_for(session.get("miner"), locks)
    if not isinstance(lock, dict):
        raise ValueError("Benchmark thermal lock is missing")
    if str(lock.get("session_id", "")).strip() != session_id:
        raise ValueError("Benchmark thermal lock belongs to another session")

    safety_decision = benchmark_engine.safety_failure(profile, prewrite_sample)
    if safety_decision:
        benchmark_results.update_candidate_result(
            BENCHMARK_RESULTS_PATH,
            session_id,
            int(sequence),
            {
                "status": "aborted",
                "safety_decision": safety_decision,
            },
        )
        raise ValueError(f"Pre-write safety check failed: {safety_decision}")

    miner = {
        "name": restore_profile.get("miner"),
        "ip": restore_profile.get("ip"),
        "type": restore_profile.get("device_type"),
    }
    if not miner["ip"] or not miner["type"]:
        raise ValueError("Restore profile is missing miner identity")

    response = apply_settings(
        miner,
        frequency=candidate["frequency"],
        voltage=candidate["voltage"],
        timeout=REQUEST_TIMEOUT,
    )
    benchmark_sessions.update_session(
        BENCHMARK_SESSIONS_PATH,
        session_id,
        {"settings_written": True},
    )
    benchmark_results.update_candidate_result(
        BENCHMARK_RESULTS_PATH,
        session_id,
        int(sequence),
        {
            "status": "applied",
            "safety_decision": None,
            "applied_at": datetime.now(TZ).replace(microsecond=0).isoformat(),
        },
    )
    return {
        "ok": True,
        "session_id": session_id,
        "sequence": int(sequence),
        "frequency": candidate["frequency"],
        "voltage": candidate["voltage"],
        "response": response,
    }

def sample_benchmark_candidate(
    session_id,
    sequence,
    sample_provider=None,
    sleep_fn=time.sleep,
    max_samples=None,
    full_run=False,
    cancel_event=None,
):
    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") not in benchmark_sessions.ACTIVE_STATES:
        raise ValueError("Benchmark session is not active")
    profile = benchmark_profiles.get_profile(session.get("device_profile"))
    restore_profile = benchmark_restore.get_restore_profile(
        BENCHMARK_RESTORE_PATH,
        session_id,
    )
    if not isinstance(restore_profile, dict):
        raise LookupError("Restore profile was not found")

    miner = {
        "name": restore_profile.get("miner"),
        "ip": restore_profile.get("ip"),
        "type": restore_profile.get("device_type"),
    }
    if not miner["ip"] or not miner["type"]:
        raise ValueError("Restore profile is missing miner identity")

    sample_provider = sample_provider or (
        lambda: normalized_stats(miner, timeout=REQUEST_TIMEOUT)
    )
    prewrite_sample = sample_provider()
    try:
        guarded_benchmark_setting_write(session_id, sequence, prewrite_sample)
    except Exception as error:
        if full_run:
            restore_candidate_for_continuation(session_id, restore_profile, profile)
            return {
                "ok": False,
                "aborted": True,
                "session_id": session_id,
                "sequence": int(sequence),
                "reason": str(error),
            }
        now = datetime.now(TZ).isoformat()
        reason = f"candidate_prewrite_aborted: {error}"
        try:
            benchmark_restore.mark_restore_profile(
                BENCHMARK_RESTORE_PATH,
                session_id,
                "failed",
                completed_at=now,
                reason=reason,
            )
        except Exception:
            pass
        try:
            thermal_locks.release_lock(
                THERMAL_LOCKS_PATH,
                session.get("miner", ""),
                session_id=session_id,
            )
        except Exception:
            pass
        try:
            benchmark_sessions.transition_session(
                BENCHMARK_SESSIONS_PATH,
                session_id,
                "failed",
                reason=reason,
            )
        except Exception:
            pass
        raise

    timing = profile["timing"]
    interval = int(timing["sample_interval_seconds"])
    total_samples = max(1, int(timing["test_seconds"]) // interval)
    if max_samples is not None:
        total_samples = min(total_samples, int(max_samples))

    samples = []
    warnings = set()
    api_failures = 0
    zero_hashrate_seconds = 0
    low_hashrate_seconds = 0
    reject_samples = 0
    error_samples = 0
    elapsed_seconds = 0

    def ensure_candidate_active():
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("Benchmark canceled by user")
        current = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH).get(session_id)
        if not isinstance(current, dict) or current.get("state") not in benchmark_sessions.ACTIVE_STATES:
            raise ValueError("Benchmark session is not active")

    def collect_safety_sample(duration):
        nonlocal api_failures, zero_hashrate_seconds, low_hashrate_seconds
        nonlocal reject_samples, error_samples, elapsed_seconds
        ensure_candidate_active()
        try:
            sample = sample_provider()
        except Exception:
            sample = None
        safety_duration = min(duration, interval)
        if sample is None:
            api_failures += 1
        else:
            api_failures = 0
            samples.append(sample)
            warnings.update(benchmark_engine.safety_warnings(profile, sample))
            hashrate = sample.get("th")
            zero_hashrate_seconds = (
                zero_hashrate_seconds + safety_duration
                if hashrate is not None and hashrate <= 0 else 0
            )
            minimum_hashrate = profile["safety"]["min_hashrate_th"]
            low_hashrate_seconds = (
                low_hashrate_seconds + safety_duration
                if hashrate is not None and hashrate < minimum_hashrate else 0
            )
            reject_samples = (
                reject_samples + 1
                if float(sample.get("reject") or 0) >= profile["safety"]["max_reject_pct"] else 0
            )
            error_samples = (
                error_samples + 1
                if float(sample.get("errorPercentage") or 0)
                >= profile["safety"]["max_error_percentage"] else 0
            )
        decision = benchmark_engine.safety_failure(
            profile,
            sample,
            api_failures=api_failures,
            zero_hashrate_seconds=zero_hashrate_seconds,
            low_hashrate_seconds=low_hashrate_seconds,
            reject_samples=reject_samples,
            error_samples=error_samples,
            elapsed_seconds=elapsed_seconds,
        )
        elapsed_seconds += duration
        return decision

    def abort_candidate(safety_decision):
        summary = benchmark_engine.sample_summary(samples)
        summary["warnings"] = sorted(warnings)
        benchmark_results.update_candidate_result(
            BENCHMARK_RESULTS_PATH,
            session_id,
            int(sequence),
            {"status": "aborted", "safety_decision": safety_decision, "sample_summary": summary},
        )
        try:
            if full_run:
                restore_candidate_for_continuation(session_id, restore_profile, profile)
                return {
                    "ok": False, "aborted": True, "session_id": session_id,
                    "sequence": int(sequence), "reason": safety_decision,
                    "result": benchmark_results.candidate_result(
                        BENCHMARK_RESULTS_PATH, session_id, int(sequence)
                    ),
                }
            restore_benchmark_miner(restore_profile)
            reason = f"candidate_aborted: {safety_decision}"
            benchmark_restore.mark_restore_profile(
                BENCHMARK_RESTORE_PATH, session_id, "restored", reason=reason
            )
            if not thermal_locks.release_lock(
                THERMAL_LOCKS_PATH, session.get("miner", ""), session_id=session_id
            ):
                raise RuntimeError("matching benchmark thermal lock could not be released")
        except Exception as restore_error:
            mark_benchmark_restore_recovery_required(
                session_id, restore_error, f"candidate_aborted_{safety_decision}"
            )
            raise ValueError(f"Benchmark candidate aborted: {safety_decision}")
        benchmark_sessions.transition_session(
            BENCHMARK_SESSIONS_PATH, session_id, "failed", reason=reason
        )
        raise ValueError(f"Benchmark candidate aborted: {safety_decision}")

    remaining = int(timing.get("warmup_seconds", 0) or 0)
    while remaining > 0:
        duration = min(interval, remaining) if sleep_fn is time.sleep else remaining
        sleep_fn(duration)
        remaining -= duration
        safety_decision = collect_safety_sample(duration)
        if safety_decision:
            result = abort_candidate(safety_decision)
            if result:
                return result

    for sample_index in range(total_samples):
        safety_decision = collect_safety_sample(interval)
        if safety_decision:
            result = abort_candidate(safety_decision)
            if result:
                return result
        if sample_index + 1 < total_samples:
            sleep_fn(interval)

    sessions = benchmark_sessions.load_sessions(BENCHMARK_SESSIONS_PATH)
    active_session = sessions.get(session_id)
    if (
        not isinstance(active_session, dict)
        or active_session.get("state") not in benchmark_sessions.ACTIVE_STATES
    ):
        raise ValueError("Benchmark session is not active")

    summary = benchmark_engine.sample_summary(samples)
    summary["warnings"] = sorted(warnings)
    row = benchmark_results.update_candidate_result(
        BENCHMARK_RESULTS_PATH,
        session_id,
        int(sequence),
        {
            "status": "sampled",
            "safety_decision": None,
            "sample_summary": summary,
        },
    )
    return {
        "ok": True,
        "session_id": session_id,
        "sequence": int(sequence),
        "samples": len(samples),
        "result": row,
    }

def collect_miners(miners):
    configured = [configured_miner(miner) for miner in miners]
    local = [miner for miner in configured if miner["telemetry_source"] == "LOCAL_API"]
    with ThreadPoolExecutor(max_workers=max(1, len(local))) as executor:
        local_results = list(executor.map(read_miner, local))
    by_name = {str(item.get("name", "")).casefold(): item for item in local_results}
    results = []
    for miner in configured:
        if miner["telemetry_source"] == "LOCAL_API":
            results.append(by_name[miner["name"].casefold()])
        else:
            results.append({
                "name": miner["name"], "pool": miner["pool"], "coin": miner["coin"],
                "online": False, "thermal_enabled": False, "th": 0,
                "temp": None, "vr_temp": None, "freq": None, "volt": None, "reject": None,
                "status": "INACTIVE", "thermal_status": "UNMANAGED",
                "expected_th": miner.get("expected_th", 1.0),
                "location_scope": miner["location_scope"],
                "telemetry_source": "BRAIINS", "worker_name": miner["worker_name"],
            })
    return results

def file_recent(path, max_age_seconds=180):
    try:
        return (time.time() - os.path.getmtime(path)) <= max_age_seconds
    except Exception:
        return False

def file_metadata(path):
    try:
        stat = path.stat()
        return {"updated_epoch": stat.st_mtime, "size_bytes": stat.st_size}
    except OSError:
        return {"updated_epoch": None, "size_bytes": None}

def application_version():
    return APP_VERSION

def iso_timestamp(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None

def application_diagnostics(snapshot=None):
    snapshot = snapshot or get_dashboard_snapshot() or empty_dashboard_snapshot()
    snapshot_epoch = snapshot.get("updated_epoch")
    return {
        "version": application_version(),
        "uptime_seconds": max(0, int(time.time() - APP_START_TIME)),
        "snapshot_updated": iso_timestamp(snapshot_epoch),
        "snapshot_age_seconds": max(0, int(time.time() - snapshot_epoch)) if isinstance(snapshot_epoch, (int, float)) else None,
        "thermal": {**file_metadata(THERMAL_HEARTBEAT_PATH), "updated": iso_timestamp(file_metadata(THERMAL_HEARTBEAT_PATH)["updated_epoch"])},
        "history": {**file_metadata(HISTORY_PATH), "updated": iso_timestamp(file_metadata(HISTORY_PATH)["updated_epoch"])},
        "storage": {
            "history_bytes": file_metadata(HISTORY_PATH)["size_bytes"],
            "thermal_log_bytes": file_metadata(LOG_PATH)["size_bytes"],
            "benchmark_bytes": sum(
                (file_metadata(path)["size_bytes"] or 0)
                for path in (BENCHMARK_SESSIONS_PATH, BENCHMARK_RESULTS_PATH, BENCHMARK_RESTORE_PATH)
            ),
        },
    }

def normalize_fleet(local_miners, braiins):
    """Build canonical configured fleet rows; location never comes from reachability."""
    fleet = []
    worker_index = {}
    for worker in (braiins or {}).get("workers", []) or []:
        key = str(worker.get("name", "")).strip().casefold()
        if key:
            worker_index.setdefault(key, []).append(worker)

    mapped_workers = {}
    requested_workers = {}
    for miner in local_miners:
        item = configured_miner(miner)
        if item["telemetry_source"] == "BRAIINS" or item.get("pool") == "Braiins":
            requested_workers.setdefault(item["worker_name"].casefold(), []).append(item["name"])
    for miner in local_miners:
        item = dict(miner)
        source = str(item.get("telemetry_source") or "LOCAL_API").upper()
        scope = str(item.get("location_scope") or "LOCAL").upper()
        item.update({
            "location_scope": scope,
            "telemetry_source": source,
            "management": "MANAGED" if source == "LOCAL_API" and item.get("thermal_enabled", True) else "UNMANAGED",
        })
        worker_name = str(item.get("worker_name") or item.get("name") or "").strip()
        item["worker_name"] = worker_name
        may_match = source == "BRAIINS" or item.get("pool") == "Braiins"
        matches = worker_index.get(worker_name.casefold(), []) if may_match and len(requested_workers.get(worker_name.casefold(), [])) == 1 else []
        if len(matches) == 1:
            worker = matches[0]
            worker_data = {
                "state": worker.get("state", "unknown"),
                "hash_rate_5m_th": worker.get("hash_rate_5m_th"),
                "hash_rate_60m_th": worker.get("hash_rate_60m_th"),
                "hash_rate_24h_th": worker.get("hash_rate_24h_th"),
            }
            item["braiins_worker"] = worker_data
            mapped_workers[worker_name.casefold()] = item.get("name")
        if source == "BRAIINS":
            worker = matches[0] if len(matches) == 1 else {}
            hash_5m = float(worker.get("hash_rate_5m_th", 0) or 0)
            hash_60m = float(worker.get("hash_rate_60m_th", 0) or 0)
            remote_active = hash_5m > 0 or hash_60m > 0
            state = "OFF-SITE" if scope == "OFF-SITE" and remote_active else (
                "OFF-SITE INACTIVE" if scope == "OFF-SITE" else ("ACTIVE" if remote_active else "INACTIVE")
            )
            item.update({
                "online": remote_active, "status": state,
                "status_class": state.replace(" ", "-"), "thermal_status": "UNMANAGED",
                "th": hash_5m, "temp": None, "vr_temp": None, "freq": None,
                "volt": None, "reject": None,
                "hash_rate_5m_th": worker.get("hash_rate_5m_th"),
                "hash_rate_60m_th": worker.get("hash_rate_60m_th"),
                "hash_rate_24h_th": worker.get("hash_rate_24h_th"),
                "worker_state": worker.get("state", "unknown"),
            })
        elif item["management"] == "UNMANAGED":
            item["thermal_status"] = "UNMANAGED"
        else:
            item["thermal_status"] = item.get("status", "OFFLINE")
        fleet.append(item)
    return fleet

def fleet_summary(fleet):
    """Return authoritative mixed-fleet activity counts."""
    local = [miner for miner in fleet if miner.get("location_scope", "LOCAL") == "LOCAL"]
    offsite = [miner for miner in fleet if miner.get("location_scope") == "OFF-SITE"]

    def local_is_active(miner):
        try:
            return bool(miner.get("online")) and float(miner.get("th", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def offsite_is_active(miner):
        return miner.get("status") == "OFF-SITE"

    local_online = sum(1 for miner in local if local_is_active(miner))
    offsite_mining = sum(1 for miner in offsite if offsite_is_active(miner))
    return {
        "active": local_online + offsite_mining,
        "total": len(fleet),
        "local_online": local_online,
        "local_total": len(local),
        "offsite_mining": offsite_mining,
        "offsite_total": len(offsite),
    }

def solo_pool_summary(fleet, pool):
    """Summarize configured local assignments and current active hashrate."""
    assigned = []
    for miner in fleet:
        if miner.get("location_scope", "LOCAL") != "LOCAL" or miner.get("pool") != pool:
            continue
        try:
            hashrate = float(miner.get("th", 0) or 0)
        except (TypeError, ValueError):
            hashrate = 0.0
        active = bool(miner.get("online")) and hashrate > 0
        assigned.append({
            "name": miner.get("name", ""),
            "active": active,
            "hashrate_th": hashrate if active else 0.0,
        })
    return {
        "assigned_miners": assigned,
        "assigned_count": len(assigned),
        "active_count": sum(1 for miner in assigned if miner["active"]),
        "current_hashrate_th": sum(miner["hashrate_th"] for miner in assigned),
    }

def normalized_braiins_workers(fleet, braiins=None):
    """Build worker rows from normalized fleet identity and match results."""
    workers = []
    for miner in fleet:
        worker = miner.get("braiins_worker")
        if not isinstance(worker, dict):
            continue
        workers.append({"name": miner.get("worker_name") or miner.get("name", ""),
                        "miner_name": miner.get("name", ""), "scope": miner.get("location_scope", "LOCAL"),
                        "membership": "FLEET", **worker})
    claimed = {str(worker.get("name", "")).casefold() for worker in workers}
    for worker in (braiins or {}).get("workers", []) or []:
        name = str(worker.get("name", "")).strip()
        if name and name.casefold() not in claimed:
            workers.append({"name": name, "scope": "POOL-ONLY", "membership": "UNADOPTED", **worker})
    workers.sort(key=lambda worker: float(worker.get("hash_rate_5m_th", 0) or 0), reverse=True)
    return workers

def recent_thermal_events(limit=100, max_bytes=65536):
    """Return meaningful operational miner state transitions, newest first."""
    limit = max(1, min(int(limit), 200))
    try:
        with LOG_PATH.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max_bytes))
            data = stream.read(max_bytes)
    except OSError:
        return []

    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    events = []
    current_time = "—"
    last_state = {}
    header_prefix = "==== THERMAL MODE "
    for line in lines:
        message = line.strip()
        if not message:
            continue
        if message.startswith(header_prefix):
            stamp = message[len(header_prefix):].split(" ====", 1)[0].strip()
            try:
                current_time = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            except ValueError:
                current_time = "—"
            continue
        upper = message.upper()
        parts = [part.strip() for part in message.split("|", 1)]
        if len(parts) != 2:
            continue
        miner, raw_event = parts
        event_upper = raw_event.upper()
        state = None
        description = raw_event
        if "ERROR READING STATS" in event_upper or "MINER UNREACHABLE" in event_upper:
            state, description = "OFFLINE", "Miner unreachable"
        elif "CRITICAL ->" in event_upper or "HOLD (CRITICAL)" in event_upper:
            state, description = "MAX COOLING", (
                "Maximum thermal reduction active" if "HOLD" in event_upper else "Maximum thermal reduction applied"
            )
        elif any(token in event_upper for token in ("HOT ->", "HOLD (HOT)", "HOLD (REDUCED)")):
            state, description = "COOLING", (
                "Cooling reduction active" if "HOLD" in event_upper else "Frequency reduced for cooling"
            )
        elif "COOL -> RESTORING" in event_upper or "RESTORED" in event_upper or "RECOVERED" in event_upper:
            state, description = "STABLE", "Returned to normal operating state"
        elif "BENCHMARK" in event_upper and any(token in event_upper for token in ("START", "RUNNING", "COMPLETE", "RECOVER", "RESTORE", "LOCK")):
            state, description = "BENCHMARK", raw_event
        else:
            # Routine telemetry and generic application log lines are not operational transitions.
            continue
        if last_state.get(miner.casefold()) == state:
            continue
        last_state[miner.casefold()] = state
        events.append({"time": current_time, "state": state, "miner": miner,
                       "message": description, "raw_message": message})
    return list(reversed(events[-limit:]))

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


def dashboard_health(miners):
    """Return the web dashboard's existing weighted health score."""
    miners = [miner for miner in miners if miner.get("telemetry_source", "LOCAL_API") == "LOCAL_API"]
    total_expected = sum(float(miner.get("expected_th") or 1) for miner in miners) or 1
    health = 100.0
    for miner in miners:
        weight = float(miner.get("expected_th") or 1) / total_expected
        if not miner.get("online") or float(miner.get("th") or 0) <= 0:
            health -= 100 * weight
            continue
        thermal_limit = float(miner.get("thermal_limit") or 0)
        risk = (float(miner.get("temp") or 0) / thermal_limit * 100) if thermal_limit else 0
        if risk >= 100:
            health -= 35 * weight
        elif risk >= 97:
            health -= 25 * weight
        elif risk >= 95:
            health -= 18 * weight
        elif risk >= 90:
            health -= 9 * weight
        reject = float(miner.get("reject") or 0)
        if reject >= 2:
            health -= 8 * weight
        elif reject >= 1:
            health -= 4 * weight
    # Math.floor(x + 0.5) matches JavaScript Math.round for this 0..100 score.
    return max(0, min(100, math.floor(health + 0.5)))


def dashboard_alert_count(miners):
    """Count the same actionable miner statuses displayed by the web UI."""
    return sum(
        1 for miner in miners
        if miner.get("location_scope", "LOCAL") == "LOCAL"
        and miner.get("management", "MANAGED") == "MANAGED"
        and miner.get("status") in ("COOLING", "MAX COOLING", "OFFLINE")
    )


def thermal_state_counts(miners):
    """Count thermal states for locally managed miners only."""
    counts = {state: 0 for state in ("STABLE", "HOLDING", "COOLING", "MAX COOLING", "BENCHMARK")}
    for miner in miners:
        if miner.get("location_scope", "LOCAL") != "LOCAL":
            continue
        if miner.get("telemetry_source", "LOCAL_API") != "LOCAL_API":
            continue
        if miner.get("management", "MANAGED") != "MANAGED":
            continue
        state = miner.get("thermal_status", miner.get("status"))
        if state in counts:
            counts[state] += 1
    return counts


def benchmark_status_active(name):
    lock = thermal_locks.active_lock_for(name, thermal_locks.load_locks(THERMAL_LOCKS_PATH))
    return lock and str(lock.get("locked_by", "")).strip().lower() == "benchmark"

def read_miner(miner):
    data = {}
    try:
        data = normalized_stats(miner, timeout=REQUEST_TIMEOUT)
        temp = data["temp"]
        freq = data["freq"]
        volt = data["volt"]
        vr_temp = data["vr_temp"]
        th = data["th"]
        reject = data["reject"]
        base_freq = miner.get("base_freq")
        hot_freq = miner.get("hot_freq")
        critical_freq = miner.get("critical_freq")

        if not miner.get("enabled", True):
            status = "UNMANAGED"
        elif benchmark_status_active(miner["name"]):
            status = "BENCHMARK"
        elif th <= 0:
            status = "OFFLINE"
        elif critical_freq and freq <= critical_freq + FREQUENCY_TOLERANCE_MHZ:
            status = "MAX COOLING"
        elif hot_freq and freq <= hot_freq + FREQUENCY_TOLERANCE_MHZ:
            status = "COOLING"
        elif base_freq and freq < base_freq - FREQUENCY_TOLERANCE_MHZ:
            status = "HOLDING"
        else:
            status = "STABLE"

        return {
            "name": miner["name"],
            "location_scope": miner.get("location_scope", "LOCAL"),
            "telemetry_source": miner.get("telemetry_source", "LOCAL_API"),
            "worker_name": miner.get("worker_name", miner["name"]),
            "pool": miner.get("pool", "Unknown"),
            "coin": miner.get("coin", ""),
            "online": True,
            "thermal_enabled": miner.get("enabled", True),
            "temp": temp,
            "vr_temp": vr_temp,
            "freq": freq,
            "volt": volt,
            "th": th,
            "reject": reject,
            "status": status,
            "status_class": status.replace(" ", "-"),
            "thermal_status": status,
            "temp_color": get_temp_color(temp),
            "expected_th": miner.get("expected_th", EXPECTED_TH.get(miner["name"], max(th, 1.0))),
            "thermal_limit": miner.get("critical_temp", get_thermal_limit(miner["name"])),
            "best_session_diff": float(data.get("bestSessionDiff", 0) or 0),
            "best_diff": float(data.get("bestDiff", data.get("bestSessionDiff", 0)) or 0)
        }

    except Exception:
        return {
            "name": miner["name"],
            "location_scope": miner.get("location_scope", "LOCAL"),
            "telemetry_source": miner.get("telemetry_source", "LOCAL_API"),
            "worker_name": miner.get("worker_name", miner["name"]),
            "pool": miner.get("pool", "Unknown"),
            "coin": miner.get("coin", ""),
            "online": False,
            "thermal_enabled": miner.get("enabled", True),
            "temp": 0,
            "vr_temp": -1,
            "freq": 0,
            "volt": 0,
            "th": 0,
            "reject": 0,
            "status": "OFFLINE",
            "thermal_status": "UNMANAGED" if not miner.get("enabled", True) else "OFFLINE",
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

def get_performance(snapshot=None):
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
        performance = list(result.values())
        return append_offsite_performance(performance, snapshot)

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
                except (TypeError, ValueError):
                    continue
                try:
                    temp = float(row["temp"])
                except (TypeError, ValueError):
                    temp = None

                age = now - epoch

                for label, seconds in windows.items():
                    if age <= seconds:
                        buckets[name][label]["th"].append(th)
                        if temp is not None:
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

    return append_offsite_performance(list(result.values()), snapshot)

def append_offsite_performance(performance, snapshot=None):
    """Enrich configured Braiins telemetry identities using remote windows."""
    rows = list(performance)
    names = {str(item.get("name", "")) for item in rows}
    snapshot = snapshot or get_dashboard_snapshot() or {}
    for miner in snapshot.get("miners", []) or []:
        name = str(miner.get("name", ""))
        if miner.get("telemetry_source") != "BRAIINS" or not name:
            continue
        remote = {
            "name": name,
            "location_scope": miner.get("location_scope", "LOCAL"),
            "telemetry_source": "BRAIINS",
            "th_now": miner.get("hash_rate_5m_th"),
            "th_60m": miner.get("hash_rate_60m_th"),
            "th_12h": None,
            "th_24h": miner.get("hash_rate_24h_th"),
            "temp_60m": None,
            "temp_12h": None,
            "temp_24h": None,
        }
        if name in names:
            for row in rows:
                if row.get("name") == name:
                    row.update({key: value for key, value in remote.items() if value is not None})
                    row["temp_60m"] = row["temp_12h"] = row["temp_24h"] = None
                    break
        else:
            rows.append(remote)
            names.add(name)
    return rows


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

    if th <= 0 or seconds <= 0 or difficulty is None or difficulty <= 0:
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

    solopool = fetch_solopool_stats()
    braiins = fetch_braiins_stats()
    fleet = normalize_fleet(results, braiins)

    with STATE_LOCK:
        record_history(fleet)
        active_runs = update_pool_runs(fleet)

    ALERT_MANAGER.process([m for m in fleet if m.get("telemetry_source") == "LOCAL_API"], solopool.get("blocks", []))
    solo_pools = {
        "Umbrel Solo": solo_pool_summary(fleet, "Umbrel Solo"),
        "BCH SoloPool": solo_pool_summary(fleet, "BCH SoloPool"),
    }

    return {
        "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "updated_epoch": time.time(),
        "miners": fleet,
        "fleet_summary": fleet_summary(fleet),
        "thermal_counts": thermal_state_counts(fleet),
        "solo_pools": solo_pools,
        "braiins_workers": normalized_braiins_workers(fleet, braiins),
        "health": dashboard_health(fleet),
        "alert_count": dashboard_alert_count(fleet),
        "runs": active_runs,
        "odds": build_odds(fleet, active_runs),
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
        "updated_epoch": None,
        "miners": [],
        "fleet_summary": fleet_summary([]),
        "thermal_counts": thermal_state_counts([]),
        "solo_pools": {
            "Umbrel Solo": solo_pool_summary([], "Umbrel Solo"),
            "BCH SoloPool": solo_pool_summary([], "BCH SoloPool"),
        },
        "braiins_workers": [],
        "health": dashboard_health([]),
        "alert_count": dashboard_alert_count([]),
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

    workers = normalized_braiins_workers(miners, braiins)
    solo_summaries = snapshot.get("solo_pools", {}) or {}
    btc_summary = solo_summaries.get("Umbrel Solo") or solo_pool_summary(miners, "Umbrel Solo")
    bch_summary = solo_summaries.get("BCH SoloPool") or solo_pool_summary(miners, "BCH SoloPool")

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
            **btc_summary,
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
            **bch_summary,
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
                "/api/miner-discovery/scan",
                "/api/miner-management/add",
                "/api/miner-management/update",
                "/api/miner-management/delete",
                "/api/benchmark/start",
                "/api/benchmark/prepare",
                "/api/benchmark/cancel",
                "/api/benchmark/cancel-active",
                "/api/benchmark/run-candidate",
                "/api/benchmark/run-full",
                "/api/benchmark/retry-restore",
                "/api/benchmark/confirm-manual-restore",
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

            if self.path in (
                "/api/benchmark/start",
                "/api/benchmark/prepare",
                "/api/benchmark/cancel",
                "/api/benchmark/cancel-active",
                "/api/benchmark/run-candidate",
                "/api/benchmark/run-full",
                "/api/benchmark/retry-restore",
                "/api/benchmark/confirm-manual-restore",
            ):
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > 4096:
                    self.send_json(400, {"ok": False, "error": "Invalid request size"})
                    return

                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(400, {"ok": False, "error": "Invalid JSON body"})
                    return

                try:
                    if self.path == "/api/benchmark/start":
                        session = start_benchmark_session(data)
                    elif self.path == "/api/benchmark/prepare":
                        session = prepare_benchmark_session(data)
                    elif self.path == "/api/benchmark/cancel":
                        session = cancel_benchmark_session(data)
                    elif self.path == "/api/benchmark/cancel-active":
                        session = cancel_active_benchmark_session(data)
                    elif self.path == "/api/benchmark/retry-restore":
                        session = retry_benchmark_restore(data)
                    elif self.path == "/api/benchmark/confirm-manual-restore":
                        session = confirm_manual_benchmark_restore(data)
                    elif self.path == "/api/benchmark/run-full":
                        session = run_full_benchmark(data)
                    else:
                        session = run_benchmark_candidate(data)
                except ValueError as error:
                    self.send_json(400, {"ok": False, "error": str(error)})
                    return
                except LookupError as error:
                    self.send_json(404, {"ok": False, "error": str(error)})
                    return

                self.send_json(200, {"ok": True, "session": session})
                return

            if self.path == "/api/miner-discovery/scan":
                length = int(self.headers.get("Content-Length", 0))
                if length > 4096:
                    self.send_json(400, {"ok": False, "error": "Invalid request size"})
                    return

                data = {}
                if length:
                    try:
                        data = json.loads(self.rfile.read(length).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self.send_json(400, {"ok": False, "error": "Invalid JSON body"})
                        return

                cidr = data.get("cidr") if isinstance(data, dict) else None
                try:
                    result = discover_and_reconcile(cidr)
                except ValueError as error:
                    self.send_json(400, {"ok": False, "error": str(error)})
                    return

                self.send_json(200, result)
                COLLECTOR_WAKE.set()
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

        if self.path == "/benchmark":
            body = (APP_DIR / "static" / "benchmark.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/thermal-settings":
            self.send_json(200, thermal_settings_payload())
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/benchmark/report":
            session_id = (parse_qs(parsed.query).get("session_id") or [""])[0]
            try:
                self.send_json(200, benchmark_report_payload(session_id))
            except ValueError as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            except LookupError as error:
                self.send_json(404, {"ok": False, "error": str(error)})
            return

        if self.path == "/api/benchmark":
            self.send_json(200, benchmark_status_payload())
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

        if parsed.path in PAGE3_PUBLIC_PATHS:
            token = (parse_qs(parsed.query).get("token") or [""])[0]
            if not valid_page3_public_token(token):
                self.send_json(403, {"ok": False, "error": "Forbidden"})
                return
            self.send_json(200, page3_public_payload())
            return

        if self.path == "/api/page3":
            snapshot = get_dashboard_snapshot()
            if snapshot is None:
                snapshot = empty_dashboard_snapshot()
            self.send_json(200, build_page3_payload(snapshot))
            return

        if self.path == "/api/performance":
            with STATE_LOCK:
                snapshot = get_dashboard_snapshot() or empty_dashboard_snapshot()
                payload = {
                    "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "performance": get_performance(snapshot)
                }

            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/diagnostics":
            self.send_json(200, application_diagnostics())
            return

        if parsed.path == "/api/events":
            raw_limit = (parse_qs(parsed.query).get("limit") or ["100"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 100
            self.send_json(200, {
                "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "events": recent_thermal_events(limit),
            })
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
    ensure_page3_public_token()
    startup_discovery()
    try:
        recovery = recover_benchmark_sessions()
        if recovery.get("recovered"):
            print(f"Benchmark recovery: {recovery}", flush=True)
    except Exception as error:
        print(f"Benchmark recovery error: {error}", flush=True)
    try:
        cleanup = cleanup_benchmark_reports()
        if cleanup.get("pruned"):
            print(f"Benchmark report cleanup: {cleanup}", flush=True)
    except Exception as error:
        print(f"Benchmark report cleanup error: {error}", flush=True)
    threading.Thread(target=collector_loop, name="miner-collector", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
