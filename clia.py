"""
CLIA acceptance-window evaluation.

Configuration schema (stored as JSON in parameters.clia):

    {
        "mode": "value" | "percent" | "greater_of" | "threshold",

        # mode = "value":
        "value":   0.3,           # absolute units, ± around TV

        # mode = "percent":
        "percent": 10.0,          # percent, ± around TV

        # mode = "greater_of":  tolerance = max(value, percent% of |TV|)
        "value":   0.3,           # absolute floor
        "percent": 10.0,          # percent component

        # mode = "threshold":  uses value below the threshold, percent above
        "threshold":    5.0,
        "low_value":    0.3,      # used when |TV| <= threshold
        "high_percent": 10.0,     # used when |TV| >  threshold
    }

`evaluate_clia(actual, predicted, cfg)` returns
(in_range: bool, tolerance: float)
where `tolerance` is the absolute window applied around `actual`.
"""

from __future__ import annotations

import math
from typing import Any


def tolerance_for(actual: float, cfg: dict | None) -> float | None:
    """Return the absolute tolerance applied around `actual` for this CLIA cfg."""
    if cfg is None or not cfg or actual is None:
        return None
    try:
        a = float(actual)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a):
        return None
    mode = cfg.get("mode")
    try:
        if mode == "value":
            return float(cfg["value"])
        if mode == "percent":
            return abs(a) * float(cfg["percent"]) / 100.0
        if mode == "greater_of":
            v = float(cfg["value"])
            p = abs(a) * float(cfg["percent"]) / 100.0
            return max(v, p)
        if mode == "threshold":
            t = float(cfg["threshold"])
            if abs(a) <= t:
                return float(cfg["low_value"])
            return abs(a) * float(cfg["high_percent"]) / 100.0
    except (TypeError, ValueError, KeyError):
        return None
    return None


def evaluate_clia(actual: float, predicted: float, cfg: dict | None) -> tuple[bool | None, float | None]:
    """
    Returns (in_range, tolerance).
    in_range = None if cfg is missing or values cannot be evaluated.
    """
    if (actual is None or predicted is None
            or not _isfinite(actual) or not _isfinite(predicted)):
        return None, None
    tol = tolerance_for(float(actual), cfg)
    if tol is None:
        return None, None
    return abs(float(predicted) - float(actual)) <= tol, tol


def in_normal_range(value: float | None, normal: dict | None) -> bool | None:
    """
    Returns True if value is inside [low, high] (inclusive). Either bound may be
    omitted to disable that side. Returns None if the bounds are missing.
    """
    if value is None or not _isfinite(value) or not normal:
        return None
    low = normal.get("low")
    high = normal.get("high")
    if low is None and high is None:
        return None
    v = float(value)
    if low is not None and v < float(low):
        return False
    if high is not None and v > float(high):
        return False
    return True


def in_detection_range(value: float | None, detection: dict | None) -> bool | None:
    return in_normal_range(value, detection)


def normal_for_gender(param_cfg: dict, gender: str | None) -> dict:
    g = (gender or "").strip().lower()
    if g.startswith("f"):
        return param_cfg.get("normal_female") or {}
    return param_cfg.get("normal_male") or {}


def _isfinite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
