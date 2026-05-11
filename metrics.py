"""
Per-row computations and aggregate diagnostic metrics.

`compute_row_metrics` adds: Predicted, Error%, Abs Error%, Bias, In Range, In CLIA,
plus a Detection Range flag column.

`confusion_counts` and `diagnostic_metrics` use a NORMAL-RANGE based definition:
    positive = result is OUTSIDE the configured normal range (abnormal).
    TP: actual abnormal AND predicted abnormal
    TN: actual normal   AND predicted normal
    FP: actual normal   AND predicted abnormal
    FN: actual abnormal AND predicted normal
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from clia import (
    evaluate_clia,
    in_detection_range,
    in_normal_range,
    normal_for_gender,
)


# ---------------------------------------------------------------------------
# row-level
# ---------------------------------------------------------------------------
def compute_row_metrics(
    df: pd.DataFrame,
    predict: Callable[[np.ndarray], np.ndarray],
    param_cfg: dict | None,
) -> pd.DataFrame:
    out = df.copy()
    if "abs_value" not in out.columns:
        out["abs_value"] = np.nan
    if "actual" not in out.columns:
        out["actual"] = np.nan
    if "gender" not in out.columns:
        out["gender"] = ""

    abs_arr = pd.to_numeric(out["abs_value"], errors="coerce").to_numpy(dtype=float)
    actual_arr = pd.to_numeric(out["actual"], errors="coerce").to_numpy(dtype=float)

    with np.errstate(invalid="ignore"):
        pred_arr = predict(abs_arr)
    pred_arr = np.where(np.isfinite(abs_arr), pred_arr, np.nan)

    out["Predicted"] = pred_arr

    with np.errstate(divide="ignore", invalid="ignore"):
        err_pct = np.where(
            actual_arr != 0,
            (pred_arr - actual_arr) / actual_arr * 100.0,
            np.nan,
        )
    out["Error%"] = err_pct
    out["Abs Error%"] = np.abs(err_pct)
    out["Bias"] = pred_arr - actual_arr

    in_range_col = []
    in_clia_col = []
    detect_flag_col = []
    for actual, pred, gender in zip(actual_arr, pred_arr, out["gender"].astype(str)):
        in_clia, _ = evaluate_clia(actual, pred, (param_cfg or {}).get("clia"))
        in_clia_col.append(in_clia)
        # "In Range" per the user's spec = predicted within CLIA window of actual.
        in_range_col.append(in_clia)

        # Detection range flag (True if actual or predicted fall outside)
        det = (param_cfg or {}).get("detection") or {}
        out_of_det = False
        for v in (actual, pred):
            in_det = in_detection_range(v, det)
            if in_det is False:
                out_of_det = True
                break
        detect_flag_col.append(out_of_det if det else False)

    out["In Range"] = in_range_col
    out["In CLIA"] = in_clia_col
    out["Out of Detection"] = detect_flag_col
    return out


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------
def confusion_counts(df: pd.DataFrame, param_cfg: dict | None) -> dict:
    """
    Confusion-matrix counts using the Normal-Range based positive definition.
    Rows where either actual or predicted are missing/nan, or normal range is
    not configured, are excluded from TP/TN/FP/FN.
    """
    tp = tn = fp = fn = 0
    n_eval = 0
    for _, row in df.iterrows():
        actual = row.get("actual")
        pred = row.get("Predicted")
        gender = row.get("gender")
        if actual is None or pred is None:
            continue
        try:
            actual = float(actual); pred = float(pred)
        except (TypeError, ValueError):
            continue
        if np.isnan(actual) or np.isnan(pred):
            continue
        normal = normal_for_gender(param_cfg or {}, gender)
        a_norm = in_normal_range(actual, normal)
        p_norm = in_normal_range(pred, normal)
        if a_norm is None or p_norm is None:
            continue
        n_eval += 1
        a_pos = not a_norm   # abnormal == positive
        p_pos = not p_norm
        if a_pos and p_pos:
            tp += 1
        elif (not a_pos) and (not p_pos):
            tn += 1
        elif (not a_pos) and p_pos:
            fp += 1
        elif a_pos and (not p_pos):
            fn += 1

    n_total = len(df)
    abs_err = pd.to_numeric(df.get("Abs Error%"), errors="coerce")
    avg_err = float(np.nanmean(abs_err.to_numpy())) if len(abs_err) else float("nan")

    in_clia = df.get("In CLIA")
    if in_clia is not None and len(in_clia) > 0:
        clia_eval = [v for v in in_clia if v is not None and not (isinstance(v, float) and np.isnan(v))]
        pct_in_clia = (sum(bool(v) for v in clia_eval) / len(clia_eval) * 100.0
                       if clia_eval else float("nan"))
    else:
        pct_in_clia = float("nan")

    return {
        "TP": tp, "TN": tn, "FP": fp, "FN": fn,
        "N samples": n_total,
        "N evaluated": n_eval,
        "Avg |Error%|": avg_err,
        "% within CLIA": pct_in_clia,
    }


def diagnostic_metrics(counts: dict) -> dict:
    tp, tn, fp, fn = counts["TP"], counts["TN"], counts["FP"], counts["FN"]
    def safe(num, den):
        return float(num) / den * 100.0 if den else float("nan")
    sens = safe(tp, tp + fn)
    spec = safe(tn, tn + fp)
    ppv = safe(tp, tp + fp)
    npv = safe(tn, tn + fn)
    acc = safe(tp + tn, tp + tn + fp + fn)
    return {
        "Sensitivity %": sens,
        "Specificity %": spec,
        "PPV %": ppv,
        "NPV %": npv,
        "Diagnostic Accuracy %": acc,
    }
