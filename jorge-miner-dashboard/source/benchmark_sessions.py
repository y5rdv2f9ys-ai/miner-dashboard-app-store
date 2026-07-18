import json
import os
import secrets
from datetime import datetime, timedelta, timezone


ACTIVE_STATES = {"preparing", "benchmarking", "canceling", "restoring"}
TERMINAL_STATES = {"completed", "failed", "canceled"}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_sessions(path):
    try:
        with path.open("r") as session_file:
            sessions = json.load(session_file)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return sessions if isinstance(sessions, dict) else {}


def write_sessions(path, sessions):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w") as output:
        json.dump(sessions, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp_path, path)


def active_session(sessions):
    for session in sessions.values():
        if isinstance(session, dict) and session.get("state") in ACTIVE_STATES:
            return session
    return None


def make_session_id(created_at=None):
    stamp = (created_at or utc_now()).replace(":", "").replace("-", "")
    return f"bench_{stamp}_{secrets.token_hex(3)}"


def create_session(path, miner_name, created_at=None, session_id=None):
    if not isinstance(miner_name, str) or not miner_name.strip():
        raise ValueError("Miner name is required")
    sessions = load_sessions(path)
    current = active_session(sessions)
    if current:
        raise ValueError("A benchmark session is already active")

    created_at = created_at or utc_now()
    session_id = session_id or make_session_id(created_at)
    session = {
        "session_id": session_id,
        "miner": miner_name.strip(),
        "state": "preparing",
        "created_at": created_at,
        "updated_at": created_at,
        "completed_at": None,
        "mode": "read_only_skeleton",
        "steps": [],
    }
    sessions[session_id] = session
    write_sessions(path, sessions)
    return session


def transition_session(path, session_id, state, updated_at=None, reason=None):
    sessions = load_sessions(path)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if state not in ACTIVE_STATES and state not in TERMINAL_STATES:
        raise ValueError("Invalid benchmark state")
    updated_at = updated_at or utc_now()
    session["state"] = state
    session["updated_at"] = updated_at
    if state in TERMINAL_STATES:
        session["completed_at"] = updated_at
    if reason:
        session["reason"] = reason
    write_sessions(path, sessions)
    return session


def update_session(path, session_id, updates):
    if not isinstance(updates, dict):
        raise ValueError("Session updates must be a JSON object")
    sessions = load_sessions(path)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    session.update(updates)
    session["updated_at"] = utc_now()
    write_sessions(path, sessions)
    return session


def complete_read_only_session(path, session_id, updated_at=None):
    sessions = load_sessions(path)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    session["steps"].append({
        "status": "completed",
        "notes": "Read-only skeleton session. No miner settings were changed.",
    })
    write_sessions(path, sessions)
    return transition_session(path, session_id, "completed", updated_at=updated_at)


def cancel_session(path, session_id, updated_at=None):
    sessions = load_sessions(path)
    session = sessions.get(session_id)
    if not isinstance(session, dict):
        raise LookupError("Benchmark session was not found")
    if session.get("state") in TERMINAL_STATES:
        raise ValueError("Benchmark session is already finished")
    return transition_session(
        path,
        session_id,
        "canceled",
        updated_at=updated_at,
        reason="canceled_by_user",
    )


def sessions_payload(path):
    sessions = load_sessions(path)
    return {
        "active": active_session(sessions),
        "sessions": sorted(
            sessions.values(),
            key=lambda session: session.get("created_at", ""),
            reverse=True,
        ),
    }


def prune_terminal_sessions(path, retention_days=7, now=None, preserve_session_ids=None):
    sessions = load_sessions(path)
    preserve_session_ids = set(preserve_session_ids or ())
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    pruned = []
    kept = {}
    for session_id, session in sessions.items():
        if session_id in preserve_session_ids:
            kept[session_id] = session
            continue
        if not isinstance(session, dict):
            kept[session_id] = session
            continue
        completed_at = parse_timestamp(session.get("completed_at") or session.get("updated_at"))
        if (
            session.get("state") in TERMINAL_STATES
            and completed_at is not None
            and completed_at < cutoff
        ):
            pruned.append(session_id)
            continue
        kept[session_id] = session
    if pruned:
        write_sessions(path, kept)
    return pruned
