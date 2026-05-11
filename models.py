"""
Calibration models.

Linear models fit Actual = f(Abs) directly.
4PL / 5PL use the standard immunoassay convention — the inverse logistic
is fitted directly so that residuals are minimised in the concentration
domain:
    Conc = C · ((A − D)/(Abs − D) − 1)^(1/B)          [4PL]
    Conc = C · (((A − D)/(Abs − D))^(1/E) − 1)^(1/B)  [5PL]
Absorbance must lie strictly between A and D for a valid prediction.

Each fit returns a dict with:
    name, coeffs, predict (abs → conc), metrics, curve (abs_grid, conc_grid),
    success, message.  4PL/5PL also include abs_range = (lo, hi).

Also includes Passing-Bablok regression (for the method-comparison plot
between Actual and Predicted, not for the calibration model itself).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    n = len(y_true)
    if n == 0:
        return {"R2": np.nan, "RMSE": np.nan, "MAE": np.nan, "N": 0}
    resid = y_true - y_pred
    ss_res = np.sum(resid ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    rmse = float(np.sqrt(ss_res / n))
    mae = float(np.mean(np.abs(resid)))
    return {"R2": float(r2), "RMSE": rmse, "MAE": mae, "N": n}


def _curve(predict: Callable, x: np.ndarray, n: int = 200):
    if len(x) == 0:
        return np.array([]), np.array([])
    lo, hi = float(np.min(x)), float(np.max(x))
    if lo == hi:
        hi = lo + 1.0
    grid = np.linspace(lo, hi, n)
    return grid, predict(grid)


def _empty(name: str, message: str) -> dict:
    return {
        "name": name,
        "coeffs": OrderedDict(),
        "predict": lambda x: np.full_like(np.asarray(x, dtype=float), np.nan),
        "metrics": {"R2": np.nan, "RMSE": np.nan, "MAE": np.nan, "N": 0},
        "curve": (np.array([]), np.array([])),
        "success": False,
        "message": message,
    }


# ---------------------------------------------------------------------------
# linear models
# ---------------------------------------------------------------------------
def fit_linear(x: np.ndarray, y: np.ndarray, *, intercept: bool = True) -> dict:
    name = "Linear (with intercept)" if intercept else "Linear (no intercept)"
    if len(x) < (2 if intercept else 1):
        return _empty(name, "Need at least 2 points for linear fit.")
    try:
        if intercept:
            A = np.vstack([x, np.ones_like(x)]).T
            (m, b), *_ = np.linalg.lstsq(A, y, rcond=None)
            predict = lambda v, m=m, b=b: m * np.asarray(v, dtype=float) + b
            coeffs = OrderedDict([("slope", float(m)), ("intercept", float(b))])
        else:
            A = x.reshape(-1, 1)
            (m,), *_ = np.linalg.lstsq(A, y, rcond=None)
            predict = lambda v, m=m: m * np.asarray(v, dtype=float)
            coeffs = OrderedDict([("slope", float(m))])
    except Exception as exc:  # numpy.linalg.LinAlgError, ValueError
        return _empty(name, f"Fit failed: {exc}")

    y_hat = predict(x)
    return {
        "name": name,
        "coeffs": coeffs,
        "predict": predict,
        "metrics": _metrics(y, y_hat),
        "curve": _curve(predict, x),
        "success": True,
        "message": "",
    }


# ---------------------------------------------------------------------------
# 4PL / 5PL logistic
# ---------------------------------------------------------------------------
def _4pl(x, A, B, C, D):
    """y = D + (A - D) / (1 + (x/C)**B)"""
    x = np.asarray(x, dtype=float)
    safe_C = C if C != 0 else 1e-12
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        ratio = np.where(x <= 0, 0.0, x / safe_C)
        return D + (A - D) / (1.0 + np.power(ratio, B))


def _5pl(x, A, B, C, D, E):
    """y = D + (A - D) / (1 + (x/C)**B) ** E"""
    x = np.asarray(x, dtype=float)
    safe_C = C if C != 0 else 1e-12
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        ratio = np.where(x <= 0, 0.0, x / safe_C)
        return D + (A - D) / np.power(1.0 + np.power(ratio, B), E)


def _4pl_inverse(y, A, B, C, D):
    """Solve 4PL for x (concentration) given y (absorbance).
    x = C * ((A - D) / (y - D) - 1) ^ (1/B)
    """
    y = np.asarray(y, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (A - D) / (y - D) - 1.0
        valid = ratio > 0
        result = np.where(valid, C * np.power(ratio, 1.0 / B), np.nan)
    return result


def _5pl_inverse(y, A, B, C, D, E):
    """Solve 5PL for x (concentration) given y (absorbance).
    x = C * (((A - D) / (y - D)) ^ (1/E) - 1) ^ (1/B)
    """
    y = np.asarray(y, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        inner = np.power((A - D) / (y - D), 1.0 / E) - 1.0
        valid = inner > 0
        result = np.where(valid, C * np.power(inner, 1.0 / B), np.nan)
    return result


def _4pl_inv_safe(y, A, B, C, D):
    """Inverse 4PL with large penalty instead of NaN (for curve_fit)."""
    r = _4pl_inverse(y, A, B, C, D)
    return np.where(np.isfinite(r), r, 1e8)


def _5pl_inv_safe(y, A, B, C, D, E):
    """Inverse 5PL with large penalty instead of NaN (for curve_fit)."""
    r = _5pl_inverse(y, A, B, C, D, E)
    return np.where(np.isfinite(r), r, 1e8)


def fit_4pl(abs_arr: np.ndarray, conc_arr: np.ndarray) -> dict:
    """Fit inverse 4PL directly: Conc = f_inv(Abs).

    Minimises residuals in the concentration domain.
    Absorbance must lie strictly between A and D for a valid prediction.
    """
    name = "4PL Logistic"
    if len(abs_arr) < 4:
        return _empty(name, "Need at least 4 points for 4PL fit.")
    try:
        order = np.argsort(conc_arr)
        n4 = max(1, len(order) // 4)
        A0 = float(np.mean(abs_arr[order[:n4]]))
        D0 = float(np.mean(abs_arr[order[-n4:]]))
        if abs(A0 - D0) < 1e-12:
            D0 = A0 + 0.1
        C0 = float(np.median(conc_arr[conc_arr > 0])) if np.any(conc_arr > 0) else 1.0
        if C0 == 0:
            C0 = 1.0
        p0 = [A0, 1.0, C0, D0]
        popt, _ = curve_fit(_4pl_inv_safe, abs_arr, conc_arr,
                            p0=p0, maxfev=20000)
        A, B, C, D = popt
        abs_lo, abs_hi = float(min(A, D)), float(max(A, D))
        predict = lambda v, A=A, B=B, C=C, D=D: _4pl_inverse(v, A, B, C, D)
        coeffs = OrderedDict([
            ("A (min response)", float(A)),
            ("B (Hill slope)", float(B)),
            ("C (EC50)", float(C)),
            ("D (max response)", float(D)),
        ])
    except Exception as exc:
        return _empty(name, f"Fit failed: {exc}")

    conc_hat = predict(abs_arr)
    ok = np.isfinite(conc_hat)
    metrics = _metrics(conc_arr[ok], conc_hat[ok]) if ok.any() else _metrics(
        np.array([]), np.array([]))

    lo, hi = float(np.min(conc_arr)), float(np.max(conc_arr))
    if lo == hi:
        hi = lo + 1.0
    conc_grid = np.linspace(lo, hi, 200)
    abs_grid = _4pl(conc_grid, A, B, C, D)
    return {
        "name": name, "coeffs": coeffs, "predict": predict,
        "metrics": metrics, "curve": (abs_grid, conc_grid),
        "abs_range": (abs_lo, abs_hi),
        "success": True, "message": "",
    }


def fit_5pl(abs_arr: np.ndarray, conc_arr: np.ndarray) -> dict:
    """Fit inverse 5PL directly: Conc = f_inv(Abs).

    Minimises residuals in the concentration domain.
    Absorbance must lie strictly between A and D for a valid prediction.
    """
    name = "5PL Logistic"
    if len(abs_arr) < 5:
        return _empty(name, "Need at least 5 points for 5PL fit.")
    try:
        order = np.argsort(conc_arr)
        n4 = max(1, len(order) // 4)
        A0 = float(np.mean(abs_arr[order[:n4]]))
        D0 = float(np.mean(abs_arr[order[-n4:]]))
        if abs(A0 - D0) < 1e-12:
            D0 = A0 + 0.1
        C0 = float(np.median(conc_arr[conc_arr > 0])) if np.any(conc_arr > 0) else 1.0
        if C0 == 0:
            C0 = 1.0
        p0 = [A0, 1.0, C0, D0, 1.0]
        popt, _ = curve_fit(_5pl_inv_safe, abs_arr, conc_arr,
                            p0=p0, maxfev=40000)
        A, B, C, D, E = popt
        abs_lo, abs_hi = float(min(A, D)), float(max(A, D))
        predict = lambda v, A=A, B=B, C=C, D=D, E=E: _5pl_inverse(v, A, B, C, D, E)
        coeffs = OrderedDict([
            ("A (min response)", float(A)),
            ("B (Hill slope)", float(B)),
            ("C (EC50)", float(C)),
            ("D (max response)", float(D)),
            ("E (asymmetry)", float(E)),
        ])
    except Exception as exc:
        return _empty(name, f"Fit failed: {exc}")

    conc_hat = predict(abs_arr)
    ok = np.isfinite(conc_hat)
    metrics = _metrics(conc_arr[ok], conc_hat[ok]) if ok.any() else _metrics(
        np.array([]), np.array([]))

    lo, hi = float(np.min(conc_arr)), float(np.max(conc_arr))
    if lo == hi:
        hi = lo + 1.0
    conc_grid = np.linspace(lo, hi, 200)
    abs_grid = _5pl(conc_grid, A, B, C, D, E)
    return {
        "name": name, "coeffs": coeffs, "predict": predict,
        "metrics": metrics, "curve": (abs_grid, conc_grid),
        "abs_range": (abs_lo, abs_hi),
        "success": True, "message": "",
    }


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
MODEL_LABELS = [
    "Linear (with intercept)",
    "Linear (no intercept)",
    "4PL Logistic",
    "5PL Logistic",
]


def fit_model(label: str, x: np.ndarray, y: np.ndarray) -> dict:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if label == "Linear (with intercept)":
        return fit_linear(x, y, intercept=True)
    if label == "Linear (no intercept)":
        return fit_linear(x, y, intercept=False)
    if label == "4PL Logistic":
        return fit_4pl(x, y)
    if label == "5PL Logistic":
        return fit_5pl(x, y)
    return _empty(label, f"Unknown model: {label}")


# ---------------------------------------------------------------------------
# Passing-Bablok regression (for method-comparison plot)
# ---------------------------------------------------------------------------
def passing_bablok(x: np.ndarray, y: np.ndarray) -> dict:
    """
    Non-parametric Passing-Bablok regression.

    Returns dict with slope, intercept, slope CI, intercept CI, n.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 2:
        return {"slope": np.nan, "intercept": np.nan,
                "slope_ci": (np.nan, np.nan),
                "intercept_ci": (np.nan, np.nan), "n": n}

    slopes = []
    for i in range(n - 1):
        dx = x[i + 1:] - x[i]
        dy = y[i + 1:] - y[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            s = np.where(dx != 0, dy / dx, np.nan)
        slopes.extend(s.tolist())

    slopes = np.array([s for s in slopes if np.isfinite(s) and s != -1])
    if slopes.size == 0:
        return {"slope": np.nan, "intercept": np.nan,
                "slope_ci": (np.nan, np.nan),
                "intercept_ci": (np.nan, np.nan), "n": n}

    slopes_sorted = np.sort(slopes)
    K = int(np.sum(slopes_sorted < -1))
    N = len(slopes_sorted)

    def median_at(idx_float):
        # PB's offset-median rule: median of slopes excluding s = -1, with a
        # K-shift to handle slopes < -1.
        if N % 2 == 1:
            return slopes_sorted[(N - 1) // 2 + K // 2]  # odd
        m1 = slopes_sorted[N // 2 - 1 + K // 2]
        m2 = slopes_sorted[N // 2 + K // 2]
        return 0.5 * (m1 + m2)

    slope = float(median_at(None))
    intercept = float(np.median(y - slope * x))

    # 95% CI on slope (PB approximation)
    try:
        from scipy.stats import norm
        w = norm.ppf(0.975) * np.sqrt(n * (n - 1) * (2 * n + 5) / 18.0)
        m1_idx = int(round((N - w) / 2)) + K // 2
        m2_idx = int(round((N + w) / 2)) + K // 2
        m1_idx = max(0, min(N - 1, m1_idx - 1))
        m2_idx = max(0, min(N - 1, m2_idx - 1))
        slope_ci = (float(slopes_sorted[m1_idx]), float(slopes_sorted[m2_idx]))
        ic_low = float(np.median(y - slope_ci[1] * x))
        ic_high = float(np.median(y - slope_ci[0] * x))
        intercept_ci = (ic_low, ic_high)
    except Exception:
        slope_ci = (np.nan, np.nan)
        intercept_ci = (np.nan, np.nan)

    return {
        "slope": slope,
        "intercept": intercept,
        "slope_ci": slope_ci,
        "intercept_ci": intercept_ci,
        "n": n,
    }
