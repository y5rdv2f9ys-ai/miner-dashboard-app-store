import json
from urllib.request import Request, urlopen

from miner_telemetry import get_hashrate_th, get_reject_pct, get_voltage_mv, get_vr_temp


def request_json(url, method="GET", payload=None, timeout=5):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8").strip()
    return json.loads(body) if body else {}


def get_system_info(ip, timeout=5):
    return request_json(f"http://{ip}/api/system/info", timeout=timeout)


def voltage_payload_field(miner_type, voltage):
    if str(miner_type).lower() == "axeos":
        return {"coreVoltage": int(voltage)}
    return {"voltage": int(voltage) * 10}


def settings_payload(miner_type, frequency=None, voltage=None):
    payload = {}
    if frequency is not None:
        payload["frequency"] = int(frequency)
    if voltage is not None:
        payload.update(voltage_payload_field(miner_type, voltage))
    return payload


def apply_settings(miner, frequency=None, voltage=None, timeout=5):
    payload = settings_payload(miner["type"], frequency=frequency, voltage=voltage)
    return request_json(
        f"http://{miner['ip']}/api/system",
        method="PATCH",
        payload=payload,
        timeout=timeout,
    )


def normalized_stats(miner, timeout=5):
    data = get_system_info(miner["ip"], timeout=timeout)
    stats = {
        "temp": float(data.get("temp", 0)),
        "vr_temp": get_vr_temp(data),
        "freq": int(data.get("frequency", 0)),
        "volt": get_voltage_mv(data, miner["type"]),
        "th": get_hashrate_th(data),
        "reject": get_reject_pct(data),
    }
    merged = dict(data)
    merged.update(stats)
    return merged
