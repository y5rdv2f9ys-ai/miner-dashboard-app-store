import time

import benchmark_profiles


def stepped_values(start, stop, step):
    values = []
    current = int(start)
    stop = int(stop)
    step = int(step)
    while current <= stop:
        values.append(current)
        current += step
    return values


def relation(value, baseline):
    if baseline is None:
        return "unknown"
    if value < baseline:
        return "below_base"
    if value > baseline:
        return "above_base"
    return "at_base"


def nearest_step(value, spec):
    minimum = int(spec["min"])
    maximum = int(spec["max"])
    step = int(spec["step"])
    value = max(minimum, min(maximum, int(value)))
    offset = round((value - minimum) / step) * step
    return max(minimum, min(maximum, minimum + offset))


def candidate_voltages(profile, baseline_voltage=None):
    spec = profile["voltage"]
    values = [int(spec["min"])]
    if baseline_voltage is not None:
        base = nearest_step(baseline_voltage, spec)
        lower = max(int(spec["min"]), base - int(spec["step"]) * 4)
        values.extend([lower, base])
    values.append(int(spec["max"]))
    return sorted(set(values))


def annotate_candidate(frequency, voltage, baseline=None, sequence=0):
    baseline = baseline or {}
    return {
        "sequence": sequence,
        "frequency": int(frequency),
        "voltage": int(voltage),
        "frequency_relation": relation(int(frequency), baseline.get("frequency")),
        "voltage_relation": relation(int(voltage), baseline.get("voltage")),
        "is_below_base": (
            baseline.get("frequency") is not None
            and baseline.get("voltage") is not None
            and (int(frequency) < baseline["frequency"] or int(voltage) < baseline["voltage"])
        ),
    }


def generate_matrix(profile, baseline=None, max_candidates=60):
    baseline = baseline or {}
    frequencies = stepped_values(
        profile["frequency"]["min"],
        profile["frequency"]["max"],
        profile["frequency"]["step"],
    )
    voltages = candidate_voltages(profile, baseline.get("voltage"))

    candidates = []
    for voltage in voltages:
        for frequency in frequencies:
            benchmark_profiles.validate_setting(profile, frequency, voltage)
            candidates.append(annotate_candidate(frequency, voltage, baseline))

    candidates.sort(key=lambda item: (item["voltage"], item["frequency"]))
    if max_candidates and len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]
    for sequence, candidate in enumerate(candidates, 1):
        candidate["sequence"] = sequence
    return candidates


def safety_failure(profile, sample, api_failures=0, zero_hashrate_seconds=0, elapsed_seconds=0):
    safety = benchmark_profiles.safety_cutoffs(profile)
    if api_failures >= safety["api_failure_limit"]:
        return "API_FAILURE_LIMIT"
    if elapsed_seconds >= safety["watchdog_seconds"]:
        return "WATCHDOG_TIMEOUT"
    if zero_hashrate_seconds >= safety["zero_hashrate_seconds"]:
        return "ZERO_HASHRATE_TIMEOUT"
    if sample is None:
        return "NO_SAMPLE"

    temp = sample.get("temp")
    if temp is None:
        return "TEMP_MISSING"
    if temp < 5:
        return "TEMP_BELOW_VALID_RANGE"
    if temp >= safety["max_chip_temp"]:
        return "CHIP_TEMP_EXCEEDED"

    vr_temp = sample.get("vr_temp")
    if vr_temp is not None and vr_temp >= safety["max_vr_temp"]:
        return "VR_TEMP_EXCEEDED"

    watts = sample.get("watts", sample.get("power"))
    if watts is not None and watts >= safety["max_power_watts"]:
        return "POWER_EXCEEDED"

    input_voltage = sample.get("input_voltage")
    if input_voltage is not None:
        value = float(input_voltage)
        if value > 100:
            value = value / 1000.0
        if value < safety["min_input_voltage"]:
            return "INPUT_VOLTAGE_LOW"
        if value > safety["max_input_voltage"]:
            return "INPUT_VOLTAGE_HIGH"

    hashrate = sample.get("th")
    if hashrate is not None and hashrate <= 0:
        return "ZERO_HASHRATE"

    return None


def average(values):
    values = [float(value) for value in values if value is not None]
    return sum(values) / len(values) if values else None


def sample_summary(samples):
    avg_hashrate = average(sample.get("th") for sample in samples)
    avg_power = average(
        sample.get("watts", sample.get("power"))
        for sample in samples
    )
    efficiency = None
    if avg_hashrate and avg_power is not None:
        efficiency = avg_power / avg_hashrate
    return {
        "average_hashrate_th": avg_hashrate,
        "average_temp": average(sample.get("temp") for sample in samples),
        "average_vr_temp": average(sample.get("vr_temp") for sample in samples),
        "average_power_watts": avg_power,
        "efficiency_jth": efficiency,
    }


def dry_run_plan(profile, baseline=None, now=None):
    now = now if now is not None else int(time.time())
    return {
        "mode": "dry_run",
        "created_at_epoch": now,
        "profile_id": profile["id"],
        "profile_label": profile["label"],
        "baseline": baseline or {},
        "timing": profile["timing"],
        "safety": benchmark_profiles.safety_cutoffs(profile),
        "candidates": generate_matrix(profile, baseline=baseline),
        "writes_enabled": False,
    }
