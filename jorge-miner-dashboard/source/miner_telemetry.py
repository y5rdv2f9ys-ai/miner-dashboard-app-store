import math


def _finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _expected_hashrate_gh(data):
    value = _finite_number(data.get("expectedHashrate"))
    return value if value is not None and value > 0 else None


def _plausible_hashrate_gh(value, expected):
    if value is None or value < 0:
        return False
    if expected is None:
        return True
    # A short AxeOS sample can exceed the nominal rate, but multi-x nominal
    # readings are corrupt counters rather than real hashrate.
    return value <= max(expected * 4.0, 100.0)


def _domain_hashrate_gh(data, expected):
    monitor = data.get("hashrateMonitor")
    asics = monitor.get("asics") if isinstance(monitor, dict) else None
    if not isinstance(asics, list) or not asics:
        return None
    domains = asics[0].get("domains") if isinstance(asics[0], dict) else None
    if not isinstance(domains, list) or not domains or expected is None:
        return None

    per_domain = expected / len(domains)
    valid = []
    for item in domains:
        value = _finite_number(item)
        if value is not None and per_domain * 0.1 <= value <= per_domain * 3.0:
            valid.append(value)
    if len(valid) < max(1, math.ceil(len(domains) / 2)):
        return None
    return sum(valid) / len(valid) * len(domains)


def get_hashrate_th(data):
    # normalized_stats adds `th`; prefer it when a normalized payload is passed
    # back through this helper (for example by the thermal logger).
    normalized = _finite_number(data.get("th"))
    if normalized is not None and normalized >= 0:
        return normalized

    expected = _expected_hashrate_gh(data)
    candidates = (
        data.get("hashRate"), data.get("hashrate"), data.get("hash_rate"),
        data.get("hashRate_1m"), data.get("hashRate_10m"), data.get("hashRate_1h"),
    )
    for candidate in candidates:
        value = _finite_number(candidate)
        if _plausible_hashrate_gh(value, expected):
            if value == 0:
                return 0.0
            return value / 1000.0 if expected is not None or value > 100 else value

    # Some AxeOS releases occasionally corrupt one or more domain counters and
    # then publish their sum as hashRate. Reconstruct only when at least half of
    # the domains agree with the device's own nominal per-domain rate.
    domain_rate = _domain_hashrate_gh(data, expected)
    if domain_rate is not None:
        return domain_rate / 1000.0
    return 0.0


def get_reject_pct(data):
    accepted = data.get("sharesAccepted") or data.get("accepted") or data.get("validShares") or 0
    rejected = data.get("sharesRejected") or data.get("rejected") or data.get("invalidShares") or 0
    try:
        accepted = float(accepted)
        rejected = float(rejected)
    except (TypeError, ValueError):
        return 0.0

    total = accepted + rejected
    return rejected / total * 100.0 if total > 0 else 0.0


def get_voltage_mv(data, miner_type):
    for key in ("coreVoltage", "coreVoltageActual", "defaultCoreVoltage"):
        value = data.get(key)
        if value in (None, ""):
            continue
        return int(round(float(value)))
    return 0


def get_input_voltage(data):
    for key in (
        "inputVoltage",
        "input_voltage",
        "voltage",
        "voltageIn",
        "voltage_in",
        "vin",
        "vIn",
        "supplyVoltage",
        "supply_voltage",
        "psuVoltage",
        "psu_voltage",
    ):
        value = data.get(key)
        if value in (None, ""):
            continue
        value = float(value)
        return value / 1000.0 if value > 100 else value
    return None


def get_vr_temp(data):
    return float(data.get("vrTemp", data.get("vrtemp", data.get("temp2", -1))))
