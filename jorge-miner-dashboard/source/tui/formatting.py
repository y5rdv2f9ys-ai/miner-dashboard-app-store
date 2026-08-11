"""Defensive formatting and summary calculations for cached snapshots."""

from __future__ import annotations


PLACEHOLDER = "—"


def number(value, decimals=1, suffix="") -> str:
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return PLACEHOLDER


def integer(value, suffix="") -> str:
    try:
        return f"{int(float(value))}{suffix}"
    except (TypeError, ValueError):
        return PLACEHOLDER


def text(value) -> str:
    return str(value) if value not in (None, "") else PLACEHOLDER


def is_online(miner: dict) -> bool:
    return miner.get("online") is True


def total_hashrate(miners: list[dict]) -> float:
    total = 0.0
    for miner in miners:
        try:
            total += float(miner.get("th") or 0)
        except (TypeError, ValueError):
            pass
    return total
