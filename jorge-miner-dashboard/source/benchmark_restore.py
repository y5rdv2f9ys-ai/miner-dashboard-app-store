import json
import os
from datetime import datetime, timezone


THERMAL_PROFILE_FIELDS = (
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


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_restore_profiles(path):
    try:
        with path.open("r") as restore_file:
            profiles = json.load(restore_file)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return profiles if isinstance(profiles, dict) else {}


def write_restore_profiles(path, profiles):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(profiles, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, path)


def thermal_profile(miner):
    base_volt = miner.get("base_volt")
    profile = {}
    for field in THERMAL_PROFILE_FIELDS:
        if field in ("hot_volt", "critical_volt"):
            profile[field] = miner.get(field, base_volt)
        else:
            profile[field] = miner.get(field)
    return profile


def build_restore_profile(session_id, miner, stats, created_at=None):
    return {
        "session_id": session_id,
        "status": "active",
        "recovery_required": False,
        "created_at": created_at or utc_now(),
        "completed_at": None,
        "miner": miner.get("name"),
        "ip": miner.get("ip"),
        "device_type": miner.get("type"),
        "restore": {
            "frequency": stats.get("freq"),
            "voltage": stats.get("volt"),
            "thermal_profile": thermal_profile(miner),
        },
    }


def save_restore_profile(path, session_id, miner, stats, created_at=None):
    profiles = load_restore_profiles(path)
    if session_id in profiles:
        raise ValueError("Restore profile already exists")
    profile = build_restore_profile(session_id, miner, stats, created_at=created_at)
    profiles[session_id] = profile
    write_restore_profiles(path, profiles)
    return profile


def get_restore_profile(path, session_id):
    return load_restore_profiles(path).get(session_id)


def mark_restore_profile(path, session_id, status, completed_at=None, reason=None):
    profiles = load_restore_profiles(path)
    profile = profiles.get(session_id)
    if not isinstance(profile, dict):
        raise LookupError("Restore profile was not found")
    profile["status"] = status
    profile["completed_at"] = completed_at or utc_now()
    if status in ("restored", "canceled"):
        profile["recovery_required"] = False
    if reason:
        profile["reason"] = reason
    write_restore_profiles(path, profiles)
    return profile


def mark_recovery_required(path, session_id, reason):
    profiles = load_restore_profiles(path)
    profile = profiles.get(session_id)
    if not isinstance(profile, dict):
        raise LookupError("Restore profile was not found")
    now = utc_now()
    profile.update({
        "status": "failed",
        "recovery_required": True,
        "completed_at": now,
        "reason": reason,
        "last_restore_error": reason,
    })
    profile["restore_attempts"] = int(profile.get("restore_attempts", 0) or 0) + 1
    write_restore_profiles(path, profiles)
    return profile


def recovery_required_profiles(path):
    return [
        profile
        for profile in load_restore_profiles(path).values()
        if isinstance(profile, dict) and profile.get("recovery_required") is True
    ]
