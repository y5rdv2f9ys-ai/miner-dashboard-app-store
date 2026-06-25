from copy import deepcopy


PROFILE_ORDER = (
    "nerdos_nerdoctaxe",
    "nerdos_nerdqaxe",
    "axeos_bitaxe",
)

PROFILES = {
    "axeos_bitaxe": {
        "id": "axeos_bitaxe",
        "label": "AxeOS / Bitaxe",
        "miner_type": "axeos",
        "match_terms": ("bitaxe", "bm1366", "bm1368"),
        "frequency": {"min": 400, "max": 650, "step": 25},
        "voltage": {"min": 1050, "max": 1250, "step": 5},
        "timing": {
            "warmup_seconds": 180,
            "test_seconds": 600,
            "sample_interval_seconds": 10,
        },
        "safety": {
            "min_hashrate_th": 0.1,
            "max_chip_temp": 69,
            "max_vr_temp": 90,
            "max_power_watts": 25,
            "min_input_voltage": 4.8,
            "max_input_voltage": 5.3,
            "api_failure_limit": 3,
            "zero_hashrate_seconds": 60,
            "watchdog_seconds": 900,
        },
        "requires_restart": False,
    },
    "nerdos_nerdqaxe": {
        "id": "nerdos_nerdqaxe",
        "label": "NerdOS / NerdQaxe",
        "miner_type": "nerdos",
        "match_terms": ("nerdqaxe", "nqaxe", "nerd qaxe"),
        "frequency": {"min": 560, "max": 800, "step": 20},
        "voltage": {"min": 1100, "max": 1250, "step": 5},
        "timing": {
            "warmup_seconds": 240,
            "test_seconds": 900,
            "sample_interval_seconds": 10,
        },
        "safety": {
            "min_hashrate_th": 1.0,
            "max_chip_temp": 67,
            "max_vr_temp": 90,
            "max_power_watts": 90,
            "min_input_voltage": 4.8,
            "max_input_voltage": 5.3,
            "api_failure_limit": 3,
            "zero_hashrate_seconds": 60,
            "watchdog_seconds": 1200,
        },
        "requires_restart": False,
    },
    "nerdos_nerdoctaxe": {
        "id": "nerdos_nerdoctaxe",
        "label": "NerdOS / NerdOctAxe",
        "miner_type": "nerdos",
        "match_terms": ("nerdoctaxe", "noctaxe", "nerd octaxe"),
        "frequency": {"min": 500, "max": 700, "step": 5},
        "voltage": {"min": 1100, "max": 1220, "step": 5},
        "timing": {
            "warmup_seconds": 300,
            "test_seconds": 900,
            "sample_interval_seconds": 10,
        },
        "safety": {
            "min_hashrate_th": 2.0,
            "max_chip_temp": 74,
            "max_vr_temp": 90,
            "max_power_watts": 220,
            "min_input_voltage": 4.8,
            "max_input_voltage": 5.3,
            "api_failure_limit": 3,
            "zero_hashrate_seconds": 60,
            "watchdog_seconds": 1200,
        },
        "requires_restart": False,
    },
}


def all_profiles():
    return [get_profile(profile_id) for profile_id in PROFILE_ORDER]


def get_profile(profile_id):
    try:
        return deepcopy(PROFILES[profile_id])
    except KeyError:
        raise LookupError("Benchmark profile was not found")


def profile_text(miner, stats=None):
    parts = []
    for source in (miner or {}, stats or {}):
        for key in (
            "name",
            "type",
            "model",
            "deviceModel",
            "device_model",
            "board",
            "hostname",
            "host",
            "version",
            "firmware",
        ):
            value = source.get(key)
            if value not in (None, ""):
                parts.append(str(value).lower())
    return " ".join(parts)


def profile_matches(profile, miner, stats=None):
    miner_type = str((miner or {}).get("type", "")).lower()
    if miner_type and miner_type != profile["miner_type"]:
        return False
    text = profile_text(miner, stats)
    return any(term in text for term in profile["match_terms"])


def select_profile(miner, stats=None):
    for profile_id in PROFILE_ORDER:
        profile = PROFILES[profile_id]
        if profile_matches(profile, miner, stats):
            return get_profile(profile_id)

    miner_type = str((miner or {}).get("type", "")).lower()
    if miner_type == "axeos":
        return get_profile("axeos_bitaxe")
    if miner_type == "nerdos":
        return get_profile("nerdos_nerdqaxe")
    raise LookupError("Benchmark profile could not be selected")


def validate_setting(profile, frequency, voltage):
    frequency = int(frequency)
    voltage = int(voltage)
    freq = profile["frequency"]
    volt = profile["voltage"]

    if not freq["min"] <= frequency <= freq["max"]:
        raise ValueError("Frequency is outside the benchmark profile range")
    if (frequency - freq["min"]) % freq["step"] != 0:
        raise ValueError("Frequency does not match the benchmark profile step")
    if not volt["min"] <= voltage <= volt["max"]:
        raise ValueError("Voltage is outside the benchmark profile range")
    if (voltage - volt["min"]) % volt["step"] != 0:
        raise ValueError("Voltage does not match the benchmark profile step")
    return {"frequency": frequency, "voltage": voltage}


def safety_cutoffs(profile):
    return deepcopy(profile["safety"])
