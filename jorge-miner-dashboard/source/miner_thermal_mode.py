#!/usr/bin/env python3
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from miner_telemetry import get_hashrate_th, get_reject_pct, get_voltage_mv, get_vr_temp

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MINER_DASHBOARD_DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHECK_INTERVAL = int(os.environ.get("THERMAL_CHECK_INTERVAL", "60"))
REQUEST_TIMEOUT = 5
CONFIG_PATH = DATA_DIR / "miners_v2.json"
HEARTBEAT_PATH = DATA_DIR / "thermal_heartbeat"
LOG_PATH = DATA_DIR / "miner_thermal_mode.log"
TZ = ZoneInfo("America/Tegucigalpa")

class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


LOG_STREAM = LOG_PATH.open("a", buffering=1)
sys.stdout = Tee(sys.stdout, LOG_STREAM)
sys.stderr = Tee(sys.stderr, LOG_STREAM)


def load_miners():
    with CONFIG_PATH.open("r") as f:
        return json.load(f)


def request_json(url, method="GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read().decode("utf-8").strip()
    return json.loads(body) if body else {}


def human_diff(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    units = ["", "K", "M", "G", "T", "P"]
    unit = 0
    while value >= 1000 and unit < len(units) - 1:
        value /= 1000.0
        unit += 1
    return f"{value:.2f}{units[unit]}"


def get_stats(miner):
    data = request_json(f"http://{miner['ip']}/api/system/info")
    stats = {
        "temp": float(data.get("temp", 0)),
        "vr_temp": get_vr_temp(data),
        "freq": int(data.get("frequency", 0)),
        "volt": get_voltage_mv(data, miner["type"]),
    }
    stats.update(data)
    return stats


def apply_profile(miner, frequency):
    payload = {"frequency": frequency}
    request_json(f"http://{miner['ip']}/api/system", method="PATCH", payload=payload)


def log_stats(miner, stats):
    accepted = stats.get("sharesAccepted", stats.get("accepted", stats.get("validShares", 0)))
    rejected = stats.get("sharesRejected", stats.get("rejected", stats.get("invalidShares", 0)))
    best_diff = stats.get(
        "bestDiff",
        stats.get("bestDifficulty", stats.get("bestShare", stats.get("bestShareDiff", "NA"))),
    )
    session_diff = stats.get("bestSessionDiff", stats.get("bestSessionDifficulty", "NA"))
    print(
        f"{miner['name']} | Temp {stats['temp']:.1f}°C | VR {stats['vr_temp']:.1f}°C "
        f"| Freq {stats['freq']} | Volt {stats['volt']} | TH {get_hashrate_th(stats):.2f} "
        f"| Reject {get_reject_pct(stats):.2f}% | Accepted {accepted} | Rejected {rejected} "
        f"| BestDiff {human_diff(best_diff)} | SessionDiff {human_diff(session_diff)}",
        flush=True,
    )


def manage_miner(miner, states):
    name = miner["name"]
    try:
        stats = get_stats(miner)
        log_stats(miner, stats)
    except Exception as error:
        print(f"{name} | ERROR reading stats: {error}", flush=True)
        return

    temp = stats["temp"]
    frequency = stats["freq"]
    current_state = states.get(name, "base")

    try:
        if temp >= miner["critical_temp"]:
            if frequency > miner["critical_freq"]:
                print(f"{name} | CRITICAL -> lowering", flush=True)
                apply_profile(miner, miner["critical_freq"])
            else:
                print(f"{name} | HOLD (critical)", flush=True)
            states[name] = "critical"
        elif temp >= miner["warn_temp"]:
            if frequency > miner["hot_freq"]:
                print(f"{name} | HOT -> reducing", flush=True)
                apply_profile(miner, miner["hot_freq"])
            else:
                print(f"{name} | HOLD (hot)", flush=True)
            states[name] = "hot"
        elif temp <= miner["recover_temp"] and (
            current_state != "base" or frequency < miner["base_freq"]
        ):
            print(f"{name} | COOL -> restoring", flush=True)
            apply_profile(miner, miner["base_freq"])
            states[name] = "base"
        elif frequency < miner["base_freq"]:
            print(f"{name} | HOLD (reduced)", flush=True)
        else:
            print(f"{name} | HOLD ({current_state})", flush=True)
    except Exception as error:
        print(f"{name} | ERROR applying settings: {error}", flush=True)


def main():
    states = {}
    while True:
        HEARTBEAT_PATH.touch()
        print(f"\n==== THERMAL MODE {datetime.now(TZ):%Y-%m-%d %H:%M:%S} ====", flush=True)
        try:
            miners = load_miners()
        except Exception as error:
            print(f"CONFIG ERROR: {error}", flush=True)
            time.sleep(CHECK_INTERVAL)
            continue

        for miner in miners:
            if miner.get("enabled", True):
                manage_miner(miner, states)
        HEARTBEAT_PATH.touch()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
