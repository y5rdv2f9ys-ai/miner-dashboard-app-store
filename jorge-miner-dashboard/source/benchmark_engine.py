import time
import math

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
    # A missing sample is a transient API failure unless the caller's
    # consecutive-failure count has reached the configured limit.
    if sample is None:
        return None

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
    hashrates = [float(sample["th"]) for sample in samples if sample.get("th") is not None]
    avg_hashrate = average(hashrates)
    avg_power = average(
        sample.get("watts", sample.get("power"))
        for sample in samples
    )
    efficiency = None
    if avg_hashrate and avg_power is not None:
        efficiency = avg_power / avg_hashrate
    return {
        "sample_count": len(samples),
        "average_hashrate_th": avg_hashrate,
        "min_hashrate_th": min(hashrates) if hashrates else None,
        "max_hashrate_th": max(hashrates) if hashrates else None,
        "hashrate_variability_pct": (
            math.sqrt(sum((value - avg_hashrate) ** 2 for value in hashrates) / len(hashrates))
            / avg_hashrate * 100
            if hashrates and avg_hashrate else None
        ),
        "average_temp": average(sample.get("temp") for sample in samples),
        "max_temp": max(
            (float(sample["temp"]) for sample in samples if sample.get("temp") is not None),
            default=None,
        ),
        "average_vr_temp": average(sample.get("vr_temp") for sample in samples),
        "max_vr_temp": max(
            (float(sample["vr_temp"]) for sample in samples if sample.get("vr_temp") is not None),
            default=None,
        ),
        "average_power_watts": avg_power,
        "efficiency_jth": efficiency,
    }


RECOMMENDATION_WEIGHTS = {
    "hashrate": 0.30,
    "efficiency": 0.25,
    "power": 0.10,
    "chip_headroom": 0.15,
    "vr_headroom": 0.05,
    "stability": 0.15,
}
MIN_STABILITY_SAMPLES = 10


def _normalized(value, values, higher_is_better=True):
    usable = [float(item) for item in values if item is not None]
    if value is None or not usable:
        return 0.0
    low, high = min(usable), max(usable)
    if high == low:
        return 1.0
    score = (float(value) - low) / (high - low)
    return score if higher_is_better else 1.0 - score


def recommendation_summary(results, profile, baseline=None):
    """Select safe sampled candidates and keep multi-factor scoring explicit."""
    eligible = [
        row for row in results
        if row.get("status") == "sampled" and not row.get("safety_decision")
    ]
    if not eligible:
        return None
    summaries = [row.get("sample_summary", {}) for row in eligible]
    safety = (profile or {}).get("safety", {})
    chip_headrooms = [
        safety.get("max_chip_temp") - item.get("max_temp")
        if safety.get("max_chip_temp") is not None and item.get("max_temp") is not None else None
        for item in summaries
    ]
    vr_headrooms = [
        safety.get("max_vr_temp") - item.get("max_vr_temp")
        if safety.get("max_vr_temp") is not None and item.get("max_vr_temp") is not None else None
        for item in summaries
    ]
    metrics = {
        "hashrate": [item.get("average_hashrate_th") for item in summaries],
        "efficiency": [item.get("efficiency_jth") for item in summaries],
        "power": [item.get("average_power_watts") for item in summaries],
        "chip_headroom": chip_headrooms,
        "vr_headroom": vr_headrooms,
        "stability": [item.get("hashrate_variability_pct") for item in summaries],
    }

    def scored(row):
        summary = row.get("sample_summary", {})
        chip_headroom = (
            safety.get("max_chip_temp") - summary.get("max_temp")
            if safety.get("max_chip_temp") is not None and summary.get("max_temp") is not None else None
        )
        vr_headroom = (
            safety.get("max_vr_temp") - summary.get("max_vr_temp")
            if safety.get("max_vr_temp") is not None and summary.get("max_vr_temp") is not None else None
        )
        components = {
            "hashrate": _normalized(summary.get("average_hashrate_th"), metrics["hashrate"]),
            "efficiency": _normalized(summary.get("efficiency_jth"), metrics["efficiency"], False),
            "power": _normalized(summary.get("average_power_watts"), metrics["power"], False),
            "chip_headroom": _normalized(chip_headroom, metrics["chip_headroom"]),
            "vr_headroom": _normalized(vr_headroom, metrics["vr_headroom"]),
            "stability": _normalized(summary.get("hashrate_variability_pct"), metrics["stability"], False),
        }
        score = sum(RECOMMENDATION_WEIGHTS[key] * components[key] for key in RECOMMENDATION_WEIGHTS)
        return score, components

    stability_eligible = [
        row for row in eligible
        if int(row.get("sample_summary", {}).get("sample_count") or 0) >= MIN_STABILITY_SAMPLES
        and row.get("sample_summary", {}).get("hashrate_variability_pct") is not None
    ]

    def recommendation(row, category, score=None, components=None):
        summary = row.get("sample_summary", {})
        baseline_summary = (baseline or {}).get("telemetry") or {}
        comparison = {}
        for key in ("average_hashrate_th", "average_power_watts", "efficiency_jth", "average_temp", "average_vr_temp"):
            current, base = summary.get(key), baseline_summary.get(key)
            comparison[key] = (current - base) if current is not None and base is not None else None
        return {
            "category": category,
            "sequence": row.get("sequence"),
            "frequency": row.get("frequency"),
            "voltage": row.get("voltage"),
            "sample_summary": summary,
            "baseline_delta": comparison,
            "score": score,
            "score_components": components,
        }

    best_overall = max(eligible, key=lambda row: scored(row)[0])
    overall_score, components = scored(best_overall)
    lowest_power = min(eligible, key=lambda row: row.get("sample_summary", {}).get("average_power_watts") if row.get("sample_summary", {}).get("average_power_watts") is not None else float("inf"))
    best_efficiency = min(eligible, key=lambda row: row.get("sample_summary", {}).get("efficiency_jth") if row.get("sample_summary", {}).get("efficiency_jth") is not None else float("inf"))
    return {
        "minimum_stability_samples": MIN_STABILITY_SAMPLES,
        "scoring_weights": dict(RECOMMENDATION_WEIGHTS),
        "eligible_candidates": len(eligible),
        "best_hashrate": recommendation(max(eligible, key=lambda row: row.get("sample_summary", {}).get("average_hashrate_th") or 0), "Best Hashrate"),
        "best_stability": recommendation(min(stability_eligible, key=lambda row: row["sample_summary"]["hashrate_variability_pct"]), "Best Stability") if stability_eligible else None,
        "lowest_power": recommendation(lowest_power, "Lowest Power Consumption"),
        "best_efficiency": recommendation(best_efficiency, "Best Efficiency"),
        "best_overall": recommendation(best_overall, "Best Overall / Recommended Baseline", overall_score, components),
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
