"""Operator-entered corridor width and the points shown on the dashboard.

1000 points == 1 m of the width the operator typed. There is no default.
"""

from __future__ import annotations

from typing import Any, Optional

WIDTH_MIN_M = 0.1
WIDTH_MAX_M = 100.0


def round_half_up(value: float) -> int:
    """Match ``Math.floor(x + 0.5)`` for x >= 0."""
    return int(float(value) + 0.5)


def parse_nominal_width(value: Any) -> Optional[float]:
    """Return meters, or None when the operator clears the field.

    Raises ValueError('invalid_width') for anything that is not a finite
    width in [0.1, 100].
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_width")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("invalid_width")
    if number < WIDTH_MIN_M or number > WIDTH_MAX_M:
        raise ValueError("invalid_width")
    return number


def stored_width(value: Any) -> Optional[float]:
    """Read a saved width. Invalid leftovers become 'not entered'."""
    try:
        return parse_nominal_width(value)
    except ValueError:
        return None


def unit_percent(value: Any, limit: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number < 0.0 or number > limit:
        return None
    return number


def width_points(percent: Optional[float], width_m: Optional[float]) -> Optional[int]:
    if percent is None or width_m is None:
        return None
    return round_half_up(percent / 100.0 * width_m * 1000.0)


def apply_points(row: dict[str, Any], width_m: Optional[float]) -> None:
    """Fill width_m / max_points / width_points / flow_points on one link row."""
    row["width_m"] = width_m
    stale = bool(row.get("stale")) or str(row.get("state") or "").upper() == "STALE"
    if stale or width_m is None:
        row["max_points"] = None
        row["width_points"] = None
        row["flow_points"] = None
        return
    row["max_points"] = round_half_up(width_m * 1000.0)
    row["width_points"] = width_points(unit_percent(row.get("w_cap"), 100.5), width_m)
    if str(row.get("state") or "").upper() == "UNKNOWN":
        row["flow_points"] = None
        return
    score = row.get("score")
    if score is None:
        score = row.get("local_passability_score")
    row["flow_points"] = width_points(unit_percent(score, 100.0), width_m)
