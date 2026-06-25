import json
import os
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_locks(path):
    try:
        with path.open("r") as lock_file:
            locks = json.load(lock_file)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return locks if isinstance(locks, dict) else {}


def write_locks(path, locks):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(locks, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, path)


def create_lock(path, miner_name, locked_by, session_id=None, created_at=None):
    if not isinstance(miner_name, str) or not miner_name.strip():
        raise ValueError("Miner name is required")
    if not isinstance(locked_by, str) or not locked_by.strip():
        raise ValueError("Lock owner is required")

    miner_name = miner_name.strip()
    locks = load_locks(path)
    current = active_lock_for(miner_name, locks)
    if current:
        raise ValueError("Miner already has an active thermal lock")

    lock = {
        "locked_by": locked_by.strip(),
        "session_id": str(session_id or "").strip(),
        "created_at": created_at or utc_now(),
    }
    locks[miner_name] = lock
    write_locks(path, locks)
    return lock


def release_lock(path, miner_name, session_id=None):
    locks = load_locks(path)
    lock = active_lock_for(miner_name, locks)
    if not lock:
        return False

    expected_session = str(session_id or "").strip()
    if expected_session and str(lock.get("session_id", "")).strip() != expected_session:
        return False

    locks.pop(miner_name, None)
    write_locks(path, locks)
    return True


def active_lock_for(miner_name, locks):
    lock = locks.get(miner_name)
    return lock if isinstance(lock, dict) and lock.get("locked_by") else None


def lock_reason(lock):
    locked_by = str(lock.get("locked_by", "unknown"))
    session_id = str(lock.get("session_id", "")).strip()
    return f"{locked_by}:{session_id}" if session_id else locked_by
