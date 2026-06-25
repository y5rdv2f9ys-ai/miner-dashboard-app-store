import json
import os
from datetime import datetime, timezone


SAMPLE_SUMMARY_FIELDS = (
    "average_hashrate_th",
    "average_temp",
    "average_vr_temp",
    "average_power_watts",
    "efficiency_jth",
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_results(path):
    try:
        with path.open("r") as results_file:
            results = json.load(results_file)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return results if isinstance(results, dict) else {}


def write_results(path, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(results, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, path)


def empty_sample_summary():
    return {field: None for field in SAMPLE_SUMMARY_FIELDS}


def planned_result(session_id, candidate, created_at=None):
    return {
        "session_id": session_id,
        "sequence": candidate["sequence"],
        "frequency": candidate["frequency"],
        "voltage": candidate["voltage"],
        "frequency_relation": candidate["frequency_relation"],
        "voltage_relation": candidate["voltage_relation"],
        "is_below_base": candidate["is_below_base"],
        "status": "planned",
        "safety_decision": None,
        "sample_summary": empty_sample_summary(),
        "created_at": created_at or utc_now(),
        "updated_at": created_at or utc_now(),
    }


def save_planned_results(path, session_id, candidates, created_at=None):
    results = load_results(path)
    if session_id in results:
        raise ValueError("Benchmark results already exist")
    session_results = [
        planned_result(session_id, candidate, created_at=created_at)
        for candidate in candidates
    ]
    results[session_id] = session_results
    write_results(path, results)
    return session_results


def session_results(path, session_id):
    results = load_results(path).get(session_id, [])
    return results if isinstance(results, list) else []


def candidate_result(path, session_id, sequence):
    for result in session_results(path, session_id):
        if result.get("sequence") == sequence:
            return result
    return None


def update_candidate_result(path, session_id, sequence, updates):
    if not isinstance(updates, dict):
        raise ValueError("Result updates must be a JSON object")
    results = load_results(path)
    rows = results.get(session_id)
    if not isinstance(rows, list):
        raise LookupError("Benchmark results were not found")
    for row in rows:
        if row.get("sequence") == sequence:
            row.update(updates)
            row["updated_at"] = utc_now()
            write_results(path, results)
            return row
    raise LookupError("Benchmark candidate result was not found")


def report_payload(path, session_id):
    results = session_results(path, session_id)
    sampled = [
        result for result in results
        if result.get("status") == "sampled"
    ]
    top_hashrate = sorted(
        sampled,
        key=lambda result: result.get("sample_summary", {}).get("average_hashrate_th") or 0,
        reverse=True,
    )[:5]
    top_efficiency = sorted(
        [
            result for result in sampled
            if result.get("sample_summary", {}).get("efficiency_jth") is not None
        ],
        key=lambda result: result.get("sample_summary", {}).get("efficiency_jth"),
    )[:5]
    return {
        "session_id": session_id,
        "results": results,
        "top_hashrate": top_hashrate,
        "top_efficiency": top_efficiency,
    }


def export_report(results_path, session, restore_profile=None, profile=None):
    if not isinstance(session, dict):
        raise ValueError("Benchmark session is required")
    session_id = session.get("session_id")
    if not session_id:
        raise ValueError("Benchmark session ID is required")

    payload = report_payload(results_path, session_id)
    rows = payload["results"]
    aborted = [
        row for row in rows
        if row.get("status") == "aborted"
    ]
    canceled = [
        row for row in rows
        if row.get("status") == "canceled"
    ]
    safety_decisions = [
        {
            "sequence": row.get("sequence"),
            "status": row.get("status"),
            "safety_decision": row.get("safety_decision"),
        }
        for row in rows
        if row.get("safety_decision")
    ]
    return {
        "schema": "benchmark_report_v1",
        "generated_at": utc_now(),
        "session": session,
        "profile": profile,
        "restore_profile": restore_profile,
        "restore_baseline": (
            restore_profile.get("restore")
            if isinstance(restore_profile, dict)
            else None
        ),
        "results": rows,
        "top_hashrate": payload["top_hashrate"],
        "top_efficiency": payload["top_efficiency"],
        "aborted": aborted,
        "canceled": canceled,
        "safety_decisions": safety_decisions,
        "counts": {
            "total": len(rows),
            "planned": sum(1 for row in rows if row.get("status") == "planned"),
            "sampled": sum(1 for row in rows if row.get("status") == "sampled"),
            "aborted": len(aborted),
            "canceled": len(canceled),
        },
    }
