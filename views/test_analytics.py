"""
Test Analytics tab.

Layout (top to bottom):
  1. Calibration model selector
  2. Performance summary  (only when sidebar filters down to 1 parameter)
  3. Plots                (only when 1 parameter)
  4. Data entry           (multi-parameter editable grid + computed cols)
  5. Export buttons       (data CSV always; HTML report when 1 parameter)

Sidebar = static left filter rail including a Parameter multiselect.
"""

from __future__ import annotations

import io
from datetime import datetime, date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import db
import models
import metrics as M
import style
from clia import in_detection_range, tolerance_for, in_normal_range


GRID_INPUT_COLS = [
    "Selected", "Parameter", "Device ID", "Sample ID", "Reagent LOT",
    "Date", "Age", "Gender", "Actual", "Abs",
]
GRID_KEY = "all_samples_grid"

INITIAL_EMPTY_ROWS = 5


# ---------------------------------------------------------------------------
# state / dtype helpers
# ---------------------------------------------------------------------------
def _empty_rows(n: int = INITIAL_EMPTY_ROWS) -> pd.DataFrame:
    df = pd.DataFrame({
        "Selected":    [False] * n,
        "Parameter":   [""] * n,
        "Device ID":   [""] * n,
        "Sample ID":   [""] * n,
        "Reagent LOT": [""] * n,
        "Date":        pd.Series([pd.NaT] * n, dtype="datetime64[ns]"),
        "Age":         pd.Series([np.nan] * n, dtype="float64"),
        "Gender":      [""] * n,
        "Actual":      pd.Series([np.nan] * n, dtype="float64"),
        "Abs":         pd.Series([np.nan] * n, dtype="float64"),
    })
    return _coerce_dtypes(df)


def _db_to_grid(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return _empty_rows()
    df = pd.DataFrame([{
        "Selected":    False,
        "Parameter":   r.get("parameter") or "",
        "Device ID":   r.get("device_id") or "",
        "Sample ID":   r.get("sample_id") or "",
        "Reagent LOT": r.get("reagent_lot") or "",
        "Date":        _parse_date(r.get("date")),
        "Age":         r.get("age"),
        "Gender":      r.get("gender") or "",
        "Actual":      r.get("actual"),
        "Abs":         r.get("abs_value"),
    } for r in rows])
    return _coerce_dtypes(df)


def _coerce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    if "Selected" not in df.columns:
        df["Selected"] = False
    df["Selected"] = df["Selected"].fillna(False).astype(bool)
    for c in ("Parameter", "Device ID", "Sample ID", "Reagent LOT", "Gender"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    if "Date" not in df.columns:
        df["Date"] = pd.NaT
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").astype("datetime64[ns]")
    for c in ("Age", "Actual", "Abs"):
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _grid_to_db(df: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        if all(_is_blank(r.get(c)) for c in
               ("Parameter", "Device ID", "Sample ID", "Reagent LOT",
                "Date", "Age", "Gender", "Actual", "Abs")):
            continue
        d = r.get("Date")
        if isinstance(d, (datetime, pd.Timestamp, date)):
            d_str = pd.Timestamp(d).isoformat()
        elif d is None or (isinstance(d, float) and np.isnan(d)) or pd.isna(d):
            d_str = None
        else:
            d_str = str(d)
        out.append({
            "parameter":   _strip(r.get("Parameter")),
            "device_id":   _strip(r.get("Device ID")),
            "sample_id":   _strip(r.get("Sample ID")),
            "reagent_lot": _strip(r.get("Reagent LOT")),
            "date":        d_str,
            "age":         _num(r.get("Age")),
            "gender":      _strip(r.get("Gender")),
            "actual":      _num(r.get("Actual")),
            "abs_value":   _num(r.get("Abs")),
        })
    return out


def _is_blank(v) -> bool:
    if v is None: return True
    if isinstance(v, float) and np.isnan(v): return True
    try:
        if pd.isna(v): return True
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and not v.strip(): return True
    return False


def _strip(v):
    if v is None: return None
    try:
        if pd.isna(v): return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def _num(v):
    if v is None or v == "": return None
    try:
        if pd.isna(v): return None
    except (TypeError, ValueError):
        pass
    try: return float(v)
    except (TypeError, ValueError): return None


def _parse_date(s):
    if not s: return pd.NaT
    try: return pd.to_datetime(s)
    except Exception: return pd.NaT


def _canonical_gender(s) -> str:
    if s is None: return ""
    g = str(s).strip().lower()
    if not g: return ""
    if g.startswith("f"): return "Female"
    if g.startswith("m"): return "Male"
    return "Other"


def _grid_to_dataframe_for_metrics(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "parameter":   df["Parameter"].astype(str),
        "device_id":   df["Device ID"],
        "sample_id":   df["Sample ID"],
        "reagent_lot": df["Reagent LOT"],
        "date":        pd.to_datetime(df["Date"], errors="coerce"),
        "age":         pd.to_numeric(df["Age"], errors="coerce"),
        "gender":      df["Gender"].astype(str),
        "actual":      pd.to_numeric(df["Actual"], errors="coerce"),
        "abs_value":   pd.to_numeric(df["Abs"], errors="coerce"),
    })


# ---------------------------------------------------------------------------
# sidebar filters
# ---------------------------------------------------------------------------
FILTER_KEYS = (
    "flt_param", "flt_device", "flt_sample", "flt_lot", "flt_gender",
    "flt_date", "flt_age", "flt_err_pct", "flt_abs_err",
    "flt_bias", "flt_in_range", "plot_exclude",
)


def _render_filters(grid_df: pd.DataFrame, all_known_params: list[str]) -> dict:
    sb = st.sidebar

    # Pre-compute option lists (needed by Reset to set explicit defaults)
    devices = sorted({s for s in grid_df["Device ID"].dropna().astype(str) if s.strip()})
    samples = sorted({s for s in grid_df["Sample ID"].dropna().astype(str) if s.strip()})
    lots = sorted({s for s in grid_df["Reagent LOT"].dropna().astype(str) if s.strip()})
    raw_genders = [s for s in grid_df["Gender"].dropna().astype(str) if s.strip()]
    gender_buckets = sorted({_canonical_gender(g) for g in raw_genders})

    dates = pd.to_datetime(grid_df["Date"], errors="coerce").dropna()
    if len(dates) >= 1:
        d_min, d_max = dates.min().date(), dates.max().date()
    else:
        d_min = d_max = None

    ages = pd.to_numeric(grid_df["Age"], errors="coerce").dropna()
    if len(ages) >= 1:
        a_min, a_max = float(ages.min()), float(ages.max())
        if a_min == a_max:
            a_max = a_min + 1
    else:
        a_min = a_max = None

    hcol1, hcol2 = sb.columns([2, 1])
    hcol1.markdown("## Filters")
    if hcol2.button("Reset", key="flt_reset", use_container_width=True,
                    help="Clear every filter back to its default."):
        # Clear every filter to empty (no selections).
        # Explicitly set widget keys so Streamlit's internal widget
        # registry doesn't silently re-inject old values.
        st.session_state["flt_param"] = []
        st.session_state["flt_device"] = []
        st.session_state["flt_sample"] = []
        st.session_state["flt_lot"] = []
        st.session_state["flt_gender"] = []
        if d_min is not None:
            st.session_state["flt_date"] = (d_min, d_max)
        else:
            st.session_state.pop("flt_date", None)
        if a_min is not None:
            st.session_state["flt_age"] = (a_min, a_max)
        else:
            st.session_state.pop("flt_age", None)
        st.session_state["flt_err_pct"] = (-200.0, 200.0)
        st.session_state["flt_abs_err"] = (0.0, 200.0)
        st.session_state["flt_bias"] = (-1000.0, 1000.0)
        st.session_state["flt_in_range"] = "Any"
        st.session_state["plot_exclude"] = []
        st.session_state.pop("_prev_plot_exclude", None)
        st.session_state.pop("_prev_filter_fp", None)
        st.rerun()

    # ---- Input-column filters ----
    pick_param = sb.multiselect(
        "Parameter", all_known_params, default=all_known_params,
        key="flt_param",
        help="Pick a single parameter to enable performance metrics, plots, "
             "and the report export.",
    )

    pick_dev = sb.multiselect("Device ID", devices, default=devices, key="flt_device")
    pick_samp = sb.multiselect("Sample ID", samples, default=samples, key="flt_sample")
    pick_lot = sb.multiselect("Reagent LOT", lots, default=lots, key="flt_lot")
    pick_gender = sb.multiselect("Gender", gender_buckets, default=gender_buckets,
                                 key="flt_gender")

    if d_min is not None:
        date_range = sb.date_input("Date range", value=(d_min, d_max),
                                   key="flt_date")
    else:
        date_range = None

    if a_min is not None:
        age_range = sb.slider("Age", a_min, a_max, (a_min, a_max), key="flt_age")
    else:
        age_range = None

    # ---- Computed-column filters (only meaningful when 1 parameter selected) ----
    sb.markdown("## Computed-field filters")
    sb.caption("Apply after the model is fit. Active when one parameter is selected.")
    err_range = sb.slider("Error %", -200.0, 200.0, (-200.0, 200.0),
                          step=1.0, key="flt_err_pct")
    abs_err_range = sb.slider("|Error %|", 0.0, 200.0, (0.0, 200.0),
                              step=1.0, key="flt_abs_err")
    bias_range = sb.slider("Bias", -1000.0, 1000.0, (-1000.0, 1000.0),
                           step=1.0, key="flt_bias")
    in_range_choice = sb.selectbox("In Range", ["Any", "Yes", "No"],
                                   key="flt_in_range")

    sb.markdown("## Plot exclusions")
    plot_exclude = sb.multiselect(
        "Exclude Sample IDs from plots", samples, default=[], key="plot_exclude")

    return {
        "parameters": pick_param,
        "device": pick_dev, "sample": pick_samp, "lot": pick_lot,
        "gender": pick_gender, "date_range": date_range, "age_range": age_range,
        "err_range": err_range, "abs_err_range": abs_err_range,
        "bias_range": bias_range, "in_range_choice": in_range_choice,
        "plot_exclude": plot_exclude,
    }


def _apply_computed_filters(metrics_df: pd.DataFrame, base: pd.Index,
                            f: dict) -> pd.Index:
    """Restrict `base` further by Error%, |Error%|, Bias, In Range filters."""
    if base.empty:
        return base
    sub = metrics_df.loc[base]
    mask = pd.Series(True, index=sub.index)
    if f.get("err_range"):
        lo, hi = f["err_range"]
        v = pd.to_numeric(sub.get("Error%"), errors="coerce")
        mask &= ((v >= lo) & (v <= hi)) | v.isna()
    if f.get("abs_err_range"):
        lo, hi = f["abs_err_range"]
        v = pd.to_numeric(sub.get("Abs Error%"), errors="coerce")
        mask &= ((v >= lo) & (v <= hi)) | v.isna()
    if f.get("bias_range"):
        lo, hi = f["bias_range"]
        v = pd.to_numeric(sub.get("Bias"), errors="coerce")
        mask &= ((v >= lo) & (v <= hi)) | v.isna()
    choice = f.get("in_range_choice")
    if choice == "Yes":
        mask &= sub.get("In Range").map(lambda x: x is True)
    elif choice == "No":
        mask &= sub.get("In Range").map(lambda x: x is False)
    return sub.index[mask]


def _apply_filters(df: pd.DataFrame, f: dict) -> pd.Index:
    mask = pd.Series(True, index=df.index)

    def _isin(col, choices):
        nonlocal mask
        if not choices:
            return
        mask &= df[col].astype(str).isin([str(x) for x in choices])

    if f.get("parameters"):
        mask &= df["parameter"].astype(str).isin(f["parameters"])
    _isin("device_id", f["device"])
    _isin("sample_id", f["sample"])
    _isin("reagent_lot", f["lot"])
    if f["gender"]:
        gnorm = df["gender"].astype(str).map(_canonical_gender)
        mask &= gnorm.isin(f["gender"])
    if f["date_range"]:
        d = pd.to_datetime(df["date"], errors="coerce")
        if isinstance(f["date_range"], tuple) and len(f["date_range"]) == 2:
            lo, hi = (pd.Timestamp(f["date_range"][0]),
                      pd.Timestamp(f["date_range"][1])
                      + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
            mask &= d.between(lo, hi) | d.isna()
    if f["age_range"]:
        a = pd.to_numeric(df["age"], errors="coerce")
        lo, hi = f["age_range"]
        mask &= ((a >= lo) & (a <= hi)) | a.isna()
    return df.index[mask]


# ---------------------------------------------------------------------------
# main render
# ---------------------------------------------------------------------------
def render() -> None:
    user = auth.current_user()

    # All configured parameters (may be empty)
    configured_params = db.list_parameters(user["id"])
    cfg_by_name = {p["name"]: p for p in configured_params}

    # Load multi-parameter grid into session
    if GRID_KEY not in st.session_state:
        st.session_state[GRID_KEY] = _db_to_grid(db.load_all_samples(user["id"]))

    grid_df = _coerce_dtypes(st.session_state[GRID_KEY])
    st.session_state[GRID_KEY] = grid_df

    # Auto-detect parameters: configured + any typed in the grid
    typed = sorted({s for s in grid_df["Parameter"].dropna().astype(str) if s.strip()})
    all_known = sorted(set(cfg_by_name.keys()) | set(typed))

    # Top: model selector
    style.section("Setup",
                  "Pick the calibration model · filter to a single parameter "
                  "in the sidebar to see metrics & plots")
    model_label = st.selectbox(
        "Calibration model", models.MODEL_LABELS, key="ta_model"
    )

    # Apply staged plot-exclude changes (must happen BEFORE the widget renders)
    if "_staged_plot_exclude" in st.session_state:
        st.session_state["plot_exclude"] = st.session_state.pop("_staged_plot_exclude")

    # Sidebar filters
    filters = _render_filters(grid_df, all_known)

    # --- Filter → Selected sync ---
    # When ANY sidebar filter multiselect changes, auto-select matching rows.
    _filter_fp = (
        tuple(sorted(str(s) for s in filters.get("parameters", []))),
        tuple(sorted(str(s) for s in filters.get("device", []))),
        tuple(sorted(str(s) for s in filters.get("sample", []))),
        tuple(sorted(str(s) for s in filters.get("lot", []))),
        tuple(sorted(str(s) for s in filters.get("gender", []))),
    )
    _prev_fp = st.session_state.get("_prev_filter_fp")
    if _filter_fp != _prev_fp:
        st.session_state["_prev_filter_fp"] = _filter_fp
        _has_active = any(t for t in _filter_fp)  # any non-empty filter
        if _has_active:
            _tmp_df = _grid_to_dataframe_for_metrics(grid_df)
            _filt_idx = _apply_filters(_tmp_df, filters)
            new_sel = pd.Series(False, index=grid_df.index)
            new_sel.loc[_filt_idx] = True
        else:
            new_sel = pd.Series(False, index=grid_df.index)
        if not grid_df["Selected"].equals(new_sel):
            grid_df["Selected"] = new_sel
            st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
            db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        # Clear plot exclusions when filter selection changes
        st.session_state["_staged_plot_exclude"] = []
        st.session_state["_prev_plot_exclude"] = []
        st.rerun()

    # Sync: plot exclusions ↔ Selected column in the grid
    _excl_ids = set(str(s) for s in filters.get("plot_exclude", []))
    _prev_excl = set(str(s) for s in st.session_state.get("_prev_plot_exclude", []))
    _need_sync = False
    _newly_excluded = _excl_ids - _prev_excl
    if _newly_excluded:
        mask = grid_df["Sample ID"].astype(str).isin(_newly_excluded)
        if mask.any() and grid_df.loc[mask, "Selected"].any():
            grid_df.loc[mask, "Selected"] = False
            _need_sync = True
    _newly_included = _prev_excl - _excl_ids
    if _newly_included:
        mask = grid_df["Sample ID"].astype(str).isin(_newly_included)
        if mask.any() and not grid_df.loc[mask, "Selected"].all():
            grid_df.loc[mask, "Selected"] = True
            _need_sync = True
    st.session_state["_prev_plot_exclude"] = list(_excl_ids)
    if _need_sync:
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()

    # Build full df + apply filters + Selected mask
    full_df = _grid_to_dataframe_for_metrics(grid_df)
    filtered_indices = _apply_filters(full_df, filters)
    selected_mask = grid_df["Selected"].fillna(False).astype(bool).values
    selected_idx = full_df.index[selected_mask]
    active_indices = filtered_indices.intersection(selected_idx)

    chosen_params = filters["parameters"] or []
    single_param_mode = (len(chosen_params) == 1)
    chosen_name = chosen_params[0] if single_param_mode else None
    param_cfg = cfg_by_name.get(chosen_name) if chosen_name else None
    if single_param_mode and param_cfg is None:
        param_cfg = {"name": chosen_name}

    # Restrict the analysis subset to the chosen parameter (if single)
    if single_param_mode:
        param_rows_idx = full_df.index[full_df["parameter"] == chosen_name]
        active_for_param = active_indices.intersection(param_rows_idx)
    else:
        active_for_param = pd.Index([], dtype="int64")

    # Fit model only when we have a single parameter
    if single_param_mode:
        fit = models.fit_model(
            model_label,
            full_df.loc[active_for_param, "abs_value"].to_numpy(),
            full_df.loc[active_for_param, "actual"].to_numpy(),
        )
    else:
        fit = {"success": False, "name": model_label,
               "coeffs": {}, "metrics": {"R2": float("nan"), "RMSE": float("nan"),
                                          "MAE": float("nan"), "N": 0},
               "predict": lambda x: np.full_like(np.asarray(x, dtype=float), np.nan),
               "curve": (np.array([]), np.array([])),
               "message": "Filter to a single parameter to fit a model."}

    # Compute row-level metrics for ALL rows; then blank out non-active
    metrics_df = M.compute_row_metrics(full_df, fit["predict"], param_cfg or {})

    # Save ALL selected rows for the parameter (before computed-filter blanking)
    # so the PDF report prints the complete table, not just the filtered subset.
    if single_param_mode:
        _all_sel_param_idx = param_rows_idx.intersection(selected_idx)
        report_table_df = metrics_df.loc[_all_sel_param_idx].reset_index(drop=True)
    else:
        report_table_df = pd.DataFrame()

    # Refine the active subset by the computed-column filters (only meaningful
    # in single-param mode where Predicted etc. are real numbers).
    if single_param_mode:
        active_for_param = _apply_computed_filters(
            metrics_df, active_for_param, filters,
        )
        inactive_mask = ~full_df.index.isin(active_for_param)
    else:
        inactive_mask = np.ones(len(full_df), dtype=bool)
    for c in ("Predicted", "Error%", "Abs Error%", "Bias"):
        if c in metrics_df.columns:
            metrics_df.loc[inactive_mask, c] = np.nan
    if "In Range" in metrics_df.columns:
        metrics_df["In Range"] = metrics_df["In Range"].astype("object")
        metrics_df.loc[inactive_mask, "In Range"] = None
    if "Out of Detection" in metrics_df.columns:
        metrics_df["Out of Detection"] = (
            metrics_df["Out of Detection"].fillna(False).astype(bool))

    # Build analysis_df early so the Export block (which is rendered above the
    # data table) can use it. Empty when no single parameter is selected.
    if single_param_mode:
        analysis_df = metrics_df.loc[active_for_param].reset_index(drop=True)
        counts = M.confusion_counts(analysis_df, param_cfg or {})
        diag = M.diagnostic_metrics(counts)
    else:
        analysis_df = pd.DataFrame()
        counts = {}
        diag = {}

    # Build combined view: editable input cols + read-only computed cols
    combined = grid_df.copy()
    combined["Predicted"]        = metrics_df["Predicted"].values
    combined["Error%"]           = metrics_df["Error%"].values
    combined["Abs Error%"]       = metrics_df["Abs Error%"].values
    combined["Bias"]             = metrics_df["Bias"].values

    # Internal arrays for status logic
    _in_range = metrics_df["In Range"].values
    _out_det = metrics_df["Out of Detection"].fillna(False).astype(bool).values

    def _row_status(ir, od):
        if bool(od):
            return "\U0001f534  Out of detection"
        if ir is True:
            return "\U0001f7e2  In CLIA range"
        if ir is False:
            return "\U0001f7e1  Outside CLIA"
        return ""
    combined["Status"] = [_row_status(ir, od) for ir, od in zip(_in_range, _out_det)]

    # Filter out completely empty rows from display (keep data rows only).
    # The editor's num_rows="dynamic" lets the user add/paste new rows.
    _data_mask = ~combined[["Parameter", "Device ID", "Sample ID",
                            "Reagent LOT", "Actual", "Abs"]].apply(
        lambda r: all(_is_blank(v) for v in r), axis=1,
    )
    _display_indices = combined.index[_data_mask].to_numpy()
    combined = combined.loc[_data_mask].reset_index(drop=True)
    # ---- Export buttons ----
    style.section("Export")
    ec1, ec2 = st.columns(2)
    csv_bytes = combined.to_csv(index=False).encode("utf-8")
    ec1.download_button(
        "⬇  Export data (CSV)",
        data=csv_bytes,
        file_name=f"test_data_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
        key="export_data_btn",
    )
    if single_param_mode and fit.get("success"):
        pdf_key = "pending_pdf_bytes"
        if ec2.button(
            "📄  Generate report (PDF)",
            use_container_width=True,
            key="export_report_gen",
            help="Generate a multi-page PDF report (may take a few seconds).",
        ):
            a_arr = pd.to_numeric(analysis_df["actual"], errors="coerce").to_numpy()
            p_arr = pd.to_numeric(analysis_df["Predicted"], errors="coerce").to_numpy()
            ok_pair = np.isfinite(a_arr) & np.isfinite(p_arr)
            pb_stats = models.passing_bablok(a_arr[ok_pair], p_arr[ok_pair])
            if ok_pair.any():
                d_arr = p_arr[ok_pair] - a_arr[ok_pair]
                ba_bias = float(np.mean(d_arr))
                ba_sd = float(np.std(d_arr, ddof=1)) if len(d_arr) > 1 else 0.0
                ba_n = int(len(d_arr))
            else:
                ba_bias = ba_sd = float("nan"); ba_n = 0
            try:
                st.session_state[pdf_key] = _build_report_pdf(
                    chosen_name, param_cfg or {}, counts, diag, fit,
                    analysis_df, pb_stats, ba_bias, ba_sd, ba_n,
                    report_table_df,
                )
            except ImportError:
                st.warning(
                    "PDF report requires **reportlab** and **kaleido**. "
                    "Install with `pip install reportlab kaleido`."
                )
            except Exception as exc:
                st.error(f"Failed to build PDF: {exc}")
            st.rerun()
        if pdf_key in st.session_state and st.session_state[pdf_key]:
            ec2.download_button(
                "⬇  Download report (PDF)",
                data=st.session_state[pdf_key],
                file_name=f"report_{chosen_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="export_report_btn",
            )
    else:
        ec2.button(
            "📄  Generate report (PDF)",
            disabled=True, use_container_width=True,
            help="Filter to a single parameter to enable the report export.",
            key="export_report_disabled",
        )
        st.session_state.pop("pending_pdf_bytes", None)

    # ---- Data entry table (always visible, multi-parameter) ----
    style.section(
        "Data entry",
        "Paste from Excel · edit inline · 'Parameter' is auto-detected · "
        "uncheck ✓ to exclude a row from analysis & plots",
    )

    # Sort controls rendered BEFORE action buttons so their widget state
    # is not lost when a button calls st.rerun().
    sortable_cols = [c for c in combined.columns if c != "Status"]
    sc1, sc2, _ = st.columns([2, 2, 5])
    sort_col = sc1.selectbox("Sort by", ["(none)"] + sortable_cols, key="sort_col")
    sort_dir = sc2.selectbox("Order", ["Ascending", "Descending"], key="sort_dir")
    _sort_perm = None
    if sort_col != "(none)":
        ascending = sort_dir == "Ascending"
        try:
            sorted_combined = combined.sort_values(
                by=sort_col, ascending=ascending,
                na_position="last", key=lambda s: pd.to_numeric(s, errors="coerce")
                if s.dtype == object else s,
            )
        except Exception:
            sorted_combined = combined.sort_values(
                by=sort_col, ascending=ascending,
                na_position="last",
            )
        _sort_perm = sorted_combined.index.to_numpy()
        combined = sorted_combined.reset_index(drop=True)
    st.session_state["_sort_perm_cache"] = _sort_perm

    bcol1, bcol2, bcol3, bcol4, bcol5, bcol6 = st.columns([1, 1, 1, 1, 1, 1.5])
    if bcol1.button("➕  Add 10 rows", key="add_rows"):
        st.session_state[GRID_KEY] = pd.concat(
            [grid_df, _empty_rows(10)], ignore_index=True
        )
        st.rerun()
    if bcol2.button("☑  Select all", key="select_all"):
        grid_df["Selected"] = True
        st.session_state["_staged_plot_exclude"] = []
        st.session_state["_prev_plot_exclude"] = []
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()
    if bcol3.button("☐  Deselect all", key="deselect_all"):
        grid_df["Selected"] = False
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()
    if bcol4.button("🗑  Clear selected", key="clear_sel"):
        sel_mask = grid_df["Selected"].fillna(False).astype(bool)
        excl = set(str(s) for s in st.session_state.get("plot_exclude", []))
        for idx in grid_df.index[sel_mask]:
            sid = str(grid_df.at[idx, "Sample ID"]).strip()
            if sid:
                excl.discard(sid)
        st.session_state["_staged_plot_exclude"] = sorted(excl)
        st.session_state["_prev_plot_exclude"] = sorted(excl)
        kept = grid_df[~sel_mask].reset_index(drop=True)
        st.session_state[GRID_KEY] = kept
        db.replace_all_samples(user["id"], _grid_to_db(kept))
        st.rerun()
    if bcol5.button("🧹  Clear all", key="clear_all"):
        st.session_state["_staged_plot_exclude"] = []
        st.session_state["_prev_plot_exclude"] = []
        st.session_state[GRID_KEY] = _empty_rows()
        db.replace_all_samples(user["id"], [])
        st.rerun()
    _n_ood = int(_out_det.sum())
    if bcol6.button(
        f"⚠  Deselect out of detection ({_n_ood})",
        key="desel_ood",
        disabled=(_n_ood == 0),
    ):
        ood_mask = metrics_df["Out of Detection"].fillna(False).astype(bool)
        grid_df.loc[ood_mask.values, "Selected"] = False
        excl = set(str(s) for s in st.session_state.get("plot_exclude", []))
        for idx in grid_df.index[ood_mask.values]:
            sid = str(grid_df.at[idx, "Sample ID"]).strip()
            if sid:
                excl.add(sid)
        st.session_state["_staged_plot_exclude"] = sorted(excl)
        st.session_state["_prev_plot_exclude"] = sorted(excl)
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()

    st.markdown(
        '<div class="row-legend">'
        '<span class="swatch green"></span>In CLIA range'
        '<span class="swatch amber"></span>Outside CLIA range'
        '<span class="swatch red"></span>Out of detection'
        '<span class="swatch none"></span>Not evaluated'
        '</div>',
        unsafe_allow_html=True,
    )

    computed_cols = [c for c in combined.columns if c not in GRID_INPUT_COLS]
    col_cfg = {c: st.column_config.Column(disabled=True)
               for c in computed_cols}
    for _nc in ("Predicted", "Error%", "Abs Error%", "Bias"):
        if _nc in computed_cols:
            col_cfg[_nc] = st.column_config.NumberColumn(
                _nc, disabled=True, format="%.2f",
            )
    col_cfg["Date"] = st.column_config.DateColumn("Date", format="YYYY-MM-DD")
    _n_display = len(combined)
    _visible = min(_n_display + 1, 15)
    grid_height = _visible * 35 + 50
    try:
        edited = st.data_editor(
            combined,
            key="multi_editor_v4",
            num_rows="dynamic",
            use_container_width=True,
            height=grid_height,
            column_config=col_cfg,
            hide_index=True,
        )
    except Exception as _exc:
        import traceback as _tb
        st.error(f"data_editor failed: {type(_exc).__name__}: {_exc}")
        st.code(_tb.format_exc())
        edited = combined.copy()
    # Persist edits. The editor received only data rows (empty rows stripped).
    # Unsort if needed, then compare with the original data rows to detect
    # real changes (cell edits, new rows from paste, deleted rows).
    input_only = _coerce_dtypes(edited[GRID_INPUT_COLS].copy())
    if _sort_perm is not None and len(input_only) == len(_sort_perm):
        inv_perm = np.argsort(_sort_perm)
        input_only = input_only.iloc[inv_perm].reset_index(drop=True)
    prev_data = grid_df.loc[_display_indices, GRID_INPUT_COLS].reset_index(drop=True)
    prev_data = _coerce_dtypes(prev_data.copy())
    changed = False
    if len(input_only) != len(prev_data):
        changed = True
    else:
        for col in GRID_INPUT_COLS:
            try:
                if not input_only[col].equals(prev_data[col]):
                    changed = True
                    break
            except Exception:
                changed = True
                break
    if changed:
        if len(input_only) == len(prev_data):
            old_sel = prev_data["Selected"].fillna(False).astype(bool)
            new_sel = input_only["Selected"].fillna(False).astype(bool)
            sel_diff = old_sel != new_sel
            if sel_diff.any():
                excl = set(str(s) for s in st.session_state.get("plot_exclude", []))
                for i in input_only.index[sel_diff]:
                    sid = str(input_only.at[i, "Sample ID"]).strip()
                    if not sid:
                        continue
                    if new_sel.iloc[i]:
                        excl.discard(sid)
                    else:
                        excl.add(sid)
                st.session_state["_staged_plot_exclude"] = sorted(excl)
                st.session_state["_prev_plot_exclude"] = sorted(excl)
        st.session_state[GRID_KEY] = input_only
        db.replace_all_samples(user["id"], _grid_to_db(input_only))
        st.rerun()

    # ---- Performance summary (only single param) ----
    if single_param_mode:
        style.section("Performance summary",
                      f"Calibration fit · classification on the configured "
                      f"normal range · {chosen_name}")
        style.performance_panel(counts, diag, fit)
    else:
        style.section("Performance summary")
        if not chosen_params:
            st.info("📌 Select **one parameter** in the sidebar Parameter "
                    "filter to see metrics, plots, and the report export.")
        else:
            st.info(f"📌 You have **{len(chosen_params)} parameters** selected. "
                    "Pick exactly one to enable metrics, plots, and the report export.")

    # ---- Plots (only single param) ----
    if single_param_mode:
        # PB + BA stats panel + collapsible customizer + 3 charts
        _render_charts(analysis_df, fit, param_cfg or {})



# ---------------------------------------------------------------------------
# scatter encoding helper
# ---------------------------------------------------------------------------
_GROUP_COLORS = [
    "#2563EB", "#DC2626", "#059669", "#D97706", "#7C3AED",
    "#DB2777", "#0891B2", "#65A30D", "#EA580C", "#4F46E5",
]

_CLIA_META = {
    "in_clia":      {"symbol": "circle",  "label": "In CLIA range",     "size": 9},
    "outside_clia": {"symbol": "diamond", "label": "Outside CLIA range", "size": 10},
    "out_det":      {"symbol": "x",       "label": "Out of detection",   "size": 11},
}


def _scatter_traces(x, y, ok_mask, sids, clia_cat, groups=None):
    """Build scatter traces.  Shape **always** encodes CLIA status.

    *groups* = None  → colour also encodes CLIA status (default).
    *groups* = 1-D str array → colour encodes the group (device or lot).
    """
    clia_colors = {
        "in_clia":      style.PALETTE["primary"],
        "outside_clia": style.PALETTE["warning"],
        "out_det":      style.PALETTE["danger"],
    }
    idx = np.where(ok_mask)[0]
    if len(idx) == 0:
        return []
    x, y, sids, clia_cat = x[idx], y[idx], sids[idx], clia_cat[idx]

    traces = []

    if groups is None:
        # ── Default: colour + shape = CLIA status ──
        for key, meta in _CLIA_META.items():
            m = clia_cat == key
            if m.any():
                traces.append(go.Scatter(
                    x=x[m], y=y[m], mode="markers",
                    name=meta["label"], text=sids[m],
                    marker=dict(size=meta["size"], color=clia_colors[key],
                                symbol=meta["symbol"],
                                line=dict(width=1, color="white"))))
    else:
        # ── Colour = group, Shape = CLIA status ──
        groups = groups[idx]
        u_groups = sorted({g for g in groups if g.strip()}) or [""]
        grp_color = {g: _GROUP_COLORS[i % len(_GROUP_COLORS)]
                     for i, g in enumerate(u_groups)}
        for grp in u_groups:
            first = True
            for key, meta in _CLIA_META.items():
                m = (groups == grp) & (clia_cat == key)
                if not m.any():
                    continue
                traces.append(go.Scatter(
                    x=x[m], y=y[m], mode="markers",
                    name=f"{grp} · {meta['label']}",
                    legendgroup=grp,
                    legendgrouptitle_text=grp if first else None,
                    text=sids[m],
                    marker=dict(size=meta["size"],
                                color=grp_color.get(grp, "#2563EB"),
                                symbol=meta["symbol"],
                                line=dict(width=1, color="white"))))
                first = False

    return traces


# ---------------------------------------------------------------------------
# charts (PB + BA stats panel + collapsible customizer + 3 plots)
# ---------------------------------------------------------------------------
def _render_charts(df: pd.DataFrame, fit: dict, param_cfg: dict) -> None:
    style.section("Plots", "Click legend to hide series · use sidebar to drop sample IDs")

    # PB + BA top stats
    excl_for_stats = st.session_state.get("plot_exclude", [])
    df_stats = (df[~df["sample_id"].astype(str).isin([str(s) for s in excl_for_stats])]
                if excl_for_stats else df)
    a_stats = pd.to_numeric(df_stats["actual"], errors="coerce").to_numpy()
    p_stats = pd.to_numeric(df_stats["Predicted"], errors="coerce").to_numpy()
    ok_stats = np.isfinite(a_stats) & np.isfinite(p_stats)
    pb_stats = models.passing_bablok(a_stats[ok_stats], p_stats[ok_stats])
    if ok_stats.any():
        diffs_stats = p_stats[ok_stats] - a_stats[ok_stats]
        ba_bias = float(np.mean(diffs_stats))
        ba_sd = float(np.std(diffs_stats, ddof=1)) if len(diffs_stats) > 1 else 0.0
        ba_n = int(len(diffs_stats))
    else:
        ba_bias = ba_sd = float("nan"); ba_n = 0
    style.analysis_panel(pb_stats, ba_bias, ba_sd, ba_n)

    # Collapsible title/axis customizer
    with st.expander("Customize plot titles & axes", expanded=False):
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            st.markdown("**Abs vs Actual**")
            title1 = st.text_input("Title", value="Concentration vs Absorbance", key="chart1_title")
            x1lbl = st.text_input("X-axis", value="Concentration", key="chart1_x")
            y1lbl = st.text_input("Y-axis", value="Absorbance", key="chart1_y")
            inv1 = st.checkbox("Invert axes", key="chart1_inv")
        with cc2:
            st.markdown("**Passing-Bablok**")
            title2 = st.text_input("Title", value="Passing-Bablok", key="chart2_title")
            x2lbl = st.text_input("X-axis", value="Actual", key="chart2_x")
            y2lbl = st.text_input("Y-axis", value="Predicted", key="chart2_y")
            inv2 = st.checkbox("Invert axes", key="chart2_inv")
        with cc3:
            st.markdown("**Bland-Altman**")
            title3 = st.text_input("Title", value="Bland-Altman", key="chart3_title")
            x3lbl = st.text_input("X-axis", value="Mean of Actual & Predicted", key="chart3_x")
            y3lbl = st.text_input("Y-axis", value="Predicted − Actual", key="chart3_y")
            inv3 = st.checkbox("Invert axes", key="chart3_inv")

    excl = st.session_state.get("plot_exclude", [])
    if excl:
        df = df[~df["sample_id"].astype(str).isin([str(s) for s in excl])]

    actual = pd.to_numeric(df["actual"], errors="coerce").to_numpy()
    abs_v = pd.to_numeric(df["abs_value"], errors="coerce").to_numpy()
    pred = pd.to_numeric(df["Predicted"], errors="coerce").to_numpy()
    sids = df["sample_id"].astype(str).to_numpy()
    devices = df["device_id"].astype(str).fillna("").to_numpy()
    lots = df["reagent_lot"].astype(str).fillna("").to_numpy()
    out_of_det = df.get("Out of Detection",
                        pd.Series([False] * len(df))).fillna(False).to_numpy()
    in_range_raw = df.get("In Range",
                          pd.Series([None] * len(df)))
    outside_clia = np.array(
        [(v is False) for v in in_range_raw], dtype=bool) & ~out_of_det

    clia_cat = np.where(out_of_det, "out_det",
                        np.where(outside_clia, "outside_clia", "in_clia"))

    # Colour-by selector (visible only when multiple devices or lots exist)
    _n_devs = len({d for d in devices if d.strip()})
    _n_lots = len({l for l in lots if l.strip()})
    _color_options = ["Status"]
    if _n_lots >= 2:
        _color_options.append("Reagent LOT")
    if _n_devs >= 2:
        _color_options.append("Device ID")
    if len(_color_options) > 1:
        color_mode = st.radio(
            "Color data points by",
            _color_options,
            horizontal=True,
            key="plot_color_mode",
        )
    else:
        color_mode = "Status"

    if color_mode == "Reagent LOT":
        _groups = lots
    elif color_mode == "Device ID":
        _groups = devices
    else:
        _groups = None

    c1, c2 = st.columns(2)
    # Plot 1 — Abs vs Actual
    with c1:
        fig1 = go.Figure()
        ok = np.isfinite(actual) & np.isfinite(abs_v)
        p1x, p1y = (abs_v, actual) if inv1 else (actual, abs_v)
        for t in _scatter_traces(p1x, p1y, ok, sids, clia_cat, _groups):
            fig1.add_trace(t)
        if fit["success"] and len(fit["curve"][0]):
            grid_abs, grid_actual = fit["curve"]
            cx, cy = (grid_abs, grid_actual) if inv1 else (grid_actual, grid_abs)
            fig1.add_trace(go.Scatter(
                x=cx, y=cy, mode="lines",
                name=f"{fit['name']} fit",
                line=dict(color=style.PALETTE["accent"], width=2.5),
            ))
        lbl1x, lbl1y = (y1lbl, x1lbl) if inv1 else (x1lbl, y1lbl)
        fig1.update_layout(title=title1, xaxis_title=lbl1x, yaxis_title=lbl1y,
                           **style.plotly_layout())
        st.plotly_chart(fig1, use_container_width=True)

    # Plot 2 — Passing-Bablok
    with c2:
        ok2 = np.isfinite(actual) & np.isfinite(pred)
        pb = models.passing_bablok(actual[ok2], pred[ok2])
        fig2 = go.Figure()
        p2x, p2y = (pred, actual) if inv2 else (actual, pred)
        for t in _scatter_traces(p2x, p2y, ok2, sids, clia_cat, _groups):
            fig2.add_trace(t)
        if ok2.any():
            lo = float(min(np.nanmin(actual[ok2]), np.nanmin(pred[ok2])))
            hi = float(max(np.nanmax(actual[ok2]), np.nanmax(pred[ok2])))
            grid = np.linspace(lo, hi, 100)
            fig2.add_trace(go.Scatter(
                x=grid, y=grid, mode="lines", name="Identity (y = x)",
                line=dict(color=style.PALETTE["muted"], dash="dot"),
            ))
            if np.isfinite(pb["slope"]) and np.isfinite(pb["intercept"]):
                pb_y = pb["slope"] * grid + pb["intercept"]
                pbx, pby = (pb_y, grid) if inv2 else (grid, pb_y)
                fig2.add_trace(go.Scatter(
                    x=pbx, y=pby, mode="lines", name="Passing-Bablok fit",
                    line=dict(color=style.PALETTE["warning"], width=2.5),
                ))
        lbl2x, lbl2y = (y2lbl, x2lbl) if inv2 else (x2lbl, y2lbl)
        fig2.update_layout(title=title2, xaxis_title=lbl2x, yaxis_title=lbl2y,
                           **style.plotly_layout())
        st.plotly_chart(fig2, use_container_width=True)

    # Plot 3 — Bland-Altman
    ok3 = np.isfinite(actual) & np.isfinite(pred)
    means = (actual[ok3] + pred[ok3]) / 2.0
    diffs = pred[ok3] - actual[ok3]
    p3x, p3y = (diffs, means) if inv3 else (means, diffs)
    _grp3 = _groups[ok3] if _groups is not None else None
    fig3 = go.Figure()
    for t in _scatter_traces(p3x, p3y,
                             np.ones(len(p3x), dtype=bool),
                             sids[ok3], clia_cat[ok3], _grp3):
        fig3.add_trace(t)
    if len(diffs):
        bias = float(np.mean(diffs))
        sd = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
        loa_hi = bias + 1.96 * sd
        loa_lo = bias - 1.96 * sd
        if inv3:
            d_lo = float(np.min(diffs)) if len(diffs) else 0
            d_hi = float(np.max(diffs)) if len(diffs) else 1
            if d_lo == d_hi: d_hi = d_lo + 1
            for y_val, lbl, color in [
                (bias, f"Bias {bias:.3f}", style.PALETTE["success"]),
                (loa_hi, f"+1.96 SD {loa_hi:.3f}", style.PALETTE["danger"]),
                (loa_lo, f"-1.96 SD {loa_lo:.3f}", style.PALETTE["danger"]),
            ]:
                fig3.add_trace(go.Scatter(
                    x=[y_val, y_val], y=[d_lo, d_hi], mode="lines",
                    name=lbl, line=dict(color=color, dash="dash", width=2),
                ))
        else:
            x_lo = float(np.min(means)) if len(means) else 0
            x_hi = float(np.max(means)) if len(means) else 1
            if x_lo == x_hi: x_hi = x_lo + 1
            for y_val, lbl, color in [
                (bias, f"Bias {bias:.3f}", style.PALETTE["success"]),
                (loa_hi, f"+1.96 SD {loa_hi:.3f}", style.PALETTE["danger"]),
                (loa_lo, f"-1.96 SD {loa_lo:.3f}", style.PALETTE["danger"]),
            ]:
                fig3.add_trace(go.Scatter(
                    x=[x_lo, x_hi], y=[y_val, y_val], mode="lines",
                    name=lbl, line=dict(color=color, dash="dash", width=2),
                ))
    lbl3x, lbl3y = (y3lbl, x3lbl) if inv3 else (x3lbl, y3lbl)
    fig3.update_layout(title=title3, xaxis_title=lbl3x, yaxis_title=lbl3y,
                       **style.plotly_layout(height=500))
    st.plotly_chart(fig3, use_container_width=True)



# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------
def _fmt(v) -> str:
    if v is None: return "-"
    try: f = float(v)
    except (TypeError, ValueError): return str(v)
    if not np.isfinite(f): return "-"
    return f"{f:.4g}"


def _fmt_pct(v) -> str:
    s = _fmt(v)
    return s if s == "-" else f"{s} %"


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def _pb_hypothesis(pb: dict) -> tuple[str, str, bool | None, bool | None]:
    """Return (status, message, slope_ok, intercept_ok) for the PB test:
       slope CI must contain 1, intercept CI must contain 0."""
    s_lo, s_hi = pb.get("slope_ci", (None, None))
    i_lo, i_hi = pb.get("intercept_ci", (None, None))

    def _ci_contains(lo, hi, target):
        try:
            lo_f = float(lo); hi_f = float(hi)
        except (TypeError, ValueError):
            return None
        if lo_f != lo_f or hi_f != hi_f:
            return None
        return min(lo_f, hi_f) <= target <= max(lo_f, hi_f)

    slope_ok = _ci_contains(s_lo, s_hi, 1.0)
    interc_ok = _ci_contains(i_lo, i_hi, 0.0)

    if slope_ok is None or interc_ok is None:
        return ("warn", "Insufficient data for hypothesis test",
                slope_ok, interc_ok)
    if slope_ok and interc_ok:
        return ("pass", "Methods agree - no significant bias",
                slope_ok, interc_ok)
    parts = []
    if not slope_ok:  parts.append("proportional bias")
    if not interc_ok: parts.append("constant bias")
    return ("fail", "Disagreement - " + " and ".join(parts) + " detected",
            slope_ok, interc_ok)


_chrome_ensured = False

def _ensure_chrome_for_kaleido():
    """Auto-download Chrome for kaleido image export if not already present."""
    global _chrome_ensured
    if _chrome_ensured:
        return
    _chrome_ensured = True
    import shutil
    for name in ("google-chrome", "google-chrome-stable",
                 "chromium-browser", "chromium"):
        if shutil.which(name):
            return
    import subprocess, sys
    try:
        subprocess.run(["plotly_get_chrome"],
                       capture_output=True, timeout=300)
    except Exception:
        try:
            subprocess.run(
                [sys.executable, "-c",
                 "from choreographer import Browser; "
                 "Browser.find_browser()"],
                capture_output=True, timeout=300)
        except Exception:
            pass


def _build_report_pdf(
    param_name: str, param_cfg: dict, counts: dict, diag: dict,
    fit: dict, analysis_df: pd.DataFrame,
    pb_stats: dict, ba_bias: float, ba_sd: float, ba_n: int,
    report_table_df: pd.DataFrame | None = None,
) -> bytes:
    """Build a multi-page PDF report and return the binary content."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors as rl
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, NextPageTemplate,
        Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
    )
    from reportlab.lib.enums import TA_LEFT

    buf = io.BytesIO()
    _margin = 14 * mm
    _pw, _ph = A4
    _lw, _lh = landscape(A4)
    doc = BaseDocTemplate(
        buf,
        title=f"Test Analytics Report - {param_name}",
        author="Test Analytics Dashboard",
    )
    doc.addPageTemplates([
        PageTemplate(
            id='portrait',
            frames=[Frame(_margin, _margin,
                          _pw - 2 * _margin, _ph - 2 * _margin)],
            pagesize=A4,
        ),
        PageTemplate(
            id='landscape',
            frames=[Frame(_margin, _margin,
                          _lw - 2 * _margin, _lh - 2 * _margin)],
            pagesize=landscape(A4),
        ),
    ])
    base = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=base["Title"],
                                 textColor=rl.HexColor("#1E3A8A"),
                                 alignment=TA_LEFT, fontSize=20, leading=24,
                                 spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base["Heading2"],
                        textColor=rl.HexColor("#0F172A"),
                        fontSize=13, leading=16, spaceBefore=10, spaceAfter=6,
                        leftIndent=0)
    h3 = ParagraphStyle("h3", parent=base["Heading3"],
                        textColor=rl.HexColor("#475569"),
                        fontSize=11, leading=14, spaceBefore=6, spaceAfter=4)
    body = ParagraphStyle("body", parent=base["BodyText"],
                          fontSize=9.5, leading=12,
                          textColor=rl.HexColor("#0F172A"))
    meta = ParagraphStyle("meta", parent=base["BodyText"],
                          fontSize=9, leading=11,
                          textColor=rl.HexColor("#64748B"))
    pill_pass = ParagraphStyle("pill_pass", parent=body,
                               backColor=rl.HexColor("#DCFCE7"),
                               textColor=rl.HexColor("#166534"),
                               borderPadding=4, leftIndent=4, rightIndent=4,
                               fontSize=10, leading=14)
    pill_fail = ParagraphStyle("pill_fail", parent=body,
                               backColor=rl.HexColor("#FEE2E2"),
                               textColor=rl.HexColor("#991B1B"),
                               borderPadding=4, leftIndent=4, rightIndent=4,
                               fontSize=10, leading=14)
    pill_warn = ParagraphStyle("pill_warn", parent=body,
                               backColor=rl.HexColor("#FEF3C7"),
                               textColor=rl.HexColor("#92400E"),
                               borderPadding=4, leftIndent=4, rightIndent=4,
                               fontSize=10, leading=14)

    table_style = TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), rl.HexColor("#0F172A")),
        ("BACKGROUND", (0, 0), (0, -1), rl.HexColor("#F1F5F9")),
        ("TEXTCOLOR", (0, 0), (0, -1), rl.HexColor("#475569")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, rl.HexColor("#E2E8F0")),
        ("BOX", (0, 0), (-1, -1), 0.5, rl.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])

    elements = []

    # ---- Header ----
    elements.append(Paragraph("Test Analytics Report", title_style))
    elements.append(Paragraph(
        f"Parameter: <b>{param_name}</b> &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Calibration model: <b>{fit.get('name','')}</b> &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        meta))
    elements.append(Spacer(1, 6 * mm))

    # ---- Parameter configuration ----
    nm = param_cfg.get("normal_male") or {}
    nf = param_cfg.get("normal_female") or {}
    det = param_cfg.get("detection") or {}
    clia = param_cfg.get("clia") or {}

    def _rng(r):
        if not r: return "-"
        lo, hi = r.get("low"), r.get("high")
        if lo is None and hi is None: return "-"
        return f"{'-inf' if lo is None else lo} ... {'inf' if hi is None else hi}"

    def _clia_str(c):
        if not c: return "-"
        m = c.get("mode")
        if m == "value":      return f"+/- {c.get('value')} (absolute)"
        if m == "percent":    return f"+/- {c.get('percent')} % of TV"
        if m == "greater_of": return f"+/- greater of {c.get('value')} or {c.get('percent')} %"
        if m == "threshold":
            return (f"+/- {c.get('low_value')} when |TV| <= {c.get('threshold')}, "
                    f"else +/- {c.get('high_percent')} %")
        return "-"

    elements.append(Paragraph("Parameter configuration", h2))
    cfg_tbl = Table([
        ["Normal range - Male", _rng(nm)],
        ["Normal range - Female", _rng(nf)],
        ["Detection range", _rng(det)],
        ["CLIA acceptance", _clia_str(clia)],
    ], colWidths=[60 * mm, 110 * mm])
    cfg_tbl.setStyle(table_style)
    elements.append(cfg_tbl)

    # ---- Performance summary ----
    elements.append(Paragraph("Performance summary", h2))
    perf_tbl = Table([
        ["N samples", str(counts.get("N samples", 0))],
        ["N evaluated", str(counts.get("N evaluated", 0))],
        ["True positive (TP)", str(counts.get("TP", 0))],
        ["True negative (TN)", str(counts.get("TN", 0))],
        ["False positive (FP)", str(counts.get("FP", 0))],
        ["False negative (FN)", str(counts.get("FN", 0))],
        ["% within CLIA window", _fmt(counts.get("% within CLIA")) + " %"],
        ["Average |Error %|", _fmt(counts.get("Avg |Error%|"))],
    ], colWidths=[60 * mm, 110 * mm])
    perf_tbl.setStyle(table_style)
    elements.append(perf_tbl)

    elements.append(Paragraph("Diagnostic rates", h3))
    diag_rows = [[k, _fmt(v) + " %"] for k, v in diag.items()]
    if diag_rows:
        diag_tbl = Table(diag_rows, colWidths=[60 * mm, 110 * mm])
        diag_tbl.setStyle(table_style)
        elements.append(diag_tbl)

    # ---- Calibration model fit ----
    elements.append(Paragraph("Calibration model fit", h2))
    elements.append(Paragraph("Coefficients", h3))
    coeffs = fit.get("coeffs") or {}
    if coeffs:
        coeff_rows = [[k, _fmt(v)] for k, v in coeffs.items()]
    else:
        coeff_rows = [["-", "-"]]
    ar = fit.get("abs_range")
    if ar:
        coeff_rows.append(["Valid Abs range",
                           f"{ar[0]:.4g} – {ar[1]:.4g}"])
    cr = fit.get("conc_range")
    if cr:
        coeff_rows.append(["Valid Conc range",
                           f"{cr[0]:.4g} – {cr[1]:.4g}"])
    coeff_tbl = Table(coeff_rows, colWidths=[60 * mm, 110 * mm])
    coeff_tbl.setStyle(table_style)
    elements.append(coeff_tbl)

    elements.append(Paragraph("Goodness of fit", h3))
    m = fit.get("metrics") or {}
    gof_tbl = Table([
        ["R-squared", _fmt(m.get("R2"))],
        ["RMSE", _fmt(m.get("RMSE"))],
        ["MAE", _fmt(m.get("MAE"))],
        ["N", str(m.get("N", 0))],
    ], colWidths=[60 * mm, 110 * mm])
    gof_tbl.setStyle(table_style)
    elements.append(gof_tbl)

    # ---- Method comparison: PB regression + hypothesis ----
    elements.append(PageBreak())
    elements.append(Paragraph("Passing-Bablok regression", h2))
    pb_tbl = Table([
        ["Slope", _fmt(pb_stats.get("slope")),
         f"95 % CI: {_fmt(pb_stats.get('slope_ci', (None, None))[0])} ... "
         f"{_fmt(pb_stats.get('slope_ci', (None, None))[1])}"],
        ["Intercept", _fmt(pb_stats.get("intercept")),
         f"95 % CI: {_fmt(pb_stats.get('intercept_ci', (None, None))[0])} ... "
         f"{_fmt(pb_stats.get('intercept_ci', (None, None))[1])}"],
        ["Pairs (n)", str(pb_stats.get("n", 0)), ""],
    ], colWidths=[35 * mm, 35 * mm, 100 * mm])
    pb_tbl.setStyle(table_style)
    elements.append(pb_tbl)

    elements.append(Paragraph("Hypothesis agreement (95 % CI test)", h3))
    status, message, slope_ok, interc_ok = _pb_hypothesis(pb_stats)
    pill = {"pass": pill_pass, "fail": pill_fail, "warn": pill_warn}[status]
    elements.append(Paragraph(message, pill))
    elements.append(Spacer(1, 2 * mm))

    def _verdict(ok):
        if ok is None: return "n/a"
        return "PASS - CI contains the null value" if ok else "FAIL - CI excludes the null value"

    hypo_tbl = Table([
        ["H0: slope = 1 (no proportional bias)", _verdict(slope_ok)],
        ["H0: intercept = 0 (no constant bias)", _verdict(interc_ok)],
    ], colWidths=[80 * mm, 90 * mm])
    hypo_tbl.setStyle(table_style)
    elements.append(hypo_tbl)

    # ---- Bland-Altman summary ----
    elements.append(Paragraph("Bland-Altman analysis", h2))
    if ba_n:
        loa_hi = ba_bias + 1.96 * ba_sd
        loa_lo = ba_bias - 1.96 * ba_sd
        ba_tbl = Table([
            ["Mean bias (Predicted - Actual)", _fmt(ba_bias)],
            ["SD of differences", _fmt(ba_sd)],
            ["Upper LoA (+1.96 SD)", _fmt(loa_hi)],
            ["Lower LoA (-1.96 SD)", _fmt(loa_lo)],
            ["Span (LoA range)", _fmt(loa_hi - loa_lo)],
            ["Pairs (n)", str(ba_n)],
        ], colWidths=[60 * mm, 110 * mm])
        ba_tbl.setStyle(table_style)
        elements.append(ba_tbl)

    # ---- Plots (matplotlib — no browser/kaleido dependency) ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    elements.append(PageBreak())
    elements.append(Paragraph("Plots", h2))
    actual = pd.to_numeric(analysis_df["actual"], errors="coerce").to_numpy()
    abs_v  = pd.to_numeric(analysis_df["abs_value"], errors="coerce").to_numpy()
    pred   = pd.to_numeric(analysis_df["Predicted"], errors="coerce").to_numpy()

    CLR_PRI = "#2563EB"
    CLR_ACC = "#06B6D4"
    CLR_WARN = "#D97706"
    CLR_OK = "#059669"
    CLR_ERR = "#DC2626"

    def _mpl_to_image(fig_mpl, caption: str):
        buf_img = io.BytesIO()
        fig_mpl.savefig(buf_img, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig_mpl)
        buf_img.seek(0)
        img = Image(buf_img, width=180 * mm, height=84 * mm)
        elements.append(Paragraph(caption, h3))
        elements.append(img)
        elements.append(Spacer(1, 4 * mm))

    # Plot 1 — Abs vs Actual
    ok = np.isfinite(actual) & np.isfinite(abs_v)
    fig1, ax1 = plt.subplots(figsize=(9, 4.2))
    if ok.any():
        ax1.scatter(actual[ok], abs_v[ok], s=50, c=CLR_PRI,
                    edgecolors="white", linewidths=0.6, zorder=3)
    if fit.get("success") and len(fit.get("curve", (None, None))[0]):
        gabs, gact = fit["curve"]
        ax1.plot(gact, gabs, color=CLR_ACC, linewidth=2, label=f"{fit['name']} fit")
        ax1.legend(fontsize=9)
    ax1.set_xlabel("Concentration"); ax1.set_ylabel("Absorbance")
    ax1.grid(True, alpha=0.25)
    _mpl_to_image(fig1, "Concentration vs Absorbance (with fitted calibration curve)")

    # Plot 2 — Passing-Bablok
    ok2 = np.isfinite(actual) & np.isfinite(pred)
    fig2, ax2 = plt.subplots(figsize=(9, 4.2))
    if ok2.any():
        ax2.scatter(actual[ok2], pred[ok2], s=50, c=CLR_PRI,
                    edgecolors="white", linewidths=0.6, zorder=3)
        lo = float(min(np.nanmin(actual[ok2]), np.nanmin(pred[ok2])))
        hi = float(max(np.nanmax(actual[ok2]), np.nanmax(pred[ok2])))
        grid = np.linspace(lo, hi, 100)
        ax2.plot(grid, grid, color="#94A3B8", linestyle=":", linewidth=1.5,
                 label="Identity (y = x)")
        if np.isfinite(pb_stats.get("slope", float("nan"))):
            ax2.plot(grid, pb_stats["slope"] * grid + pb_stats["intercept"],
                     color=CLR_WARN, linewidth=2, label="Passing-Bablok fit")
        ax2.legend(fontsize=9)
    ax2.set_xlabel("Actual"); ax2.set_ylabel("Predicted")
    ax2.grid(True, alpha=0.25)
    _mpl_to_image(fig2, "Passing-Bablok: Actual vs Predicted")

    # Plot 3 — Bland-Altman
    fig3, ax3 = plt.subplots(figsize=(9, 4.6))
    if ok2.any():
        means = (actual[ok2] + pred[ok2]) / 2.0
        diffs = pred[ok2] - actual[ok2]
        ax3.scatter(means, diffs, s=50, c=CLR_PRI,
                    edgecolors="white", linewidths=0.6, zorder=3)
        if len(diffs):
            bias = float(np.mean(diffs))
            sd = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
            loa_hi = bias + 1.96 * sd
            loa_lo = bias - 1.96 * sd
            x_lo, x_hi = float(np.min(means)), float(np.max(means))
            if x_lo == x_hi: x_hi = x_lo + 1
            ax3.axhline(bias, color=CLR_OK, linestyle="--", linewidth=1.5,
                        label=f"Bias {bias:.3f}")
            ax3.axhline(loa_hi, color=CLR_ERR, linestyle="--", linewidth=1.5,
                        label=f"+1.96 SD {loa_hi:.3f}")
            ax3.axhline(loa_lo, color=CLR_ERR, linestyle="--", linewidth=1.5,
                        label=f"-1.96 SD {loa_lo:.3f}")
            ax3.legend(fontsize=9)
    ax3.set_xlabel("Mean of Actual & Predicted")
    ax3.set_ylabel("Predicted − Actual")
    ax3.grid(True, alpha=0.25)
    _mpl_to_image(fig3, "Bland-Altman: Predicted − Actual")

    # ---- Data table (all selected rows, not just filtered) ----
    table_df = (report_table_df
                if report_table_df is not None and len(report_table_df)
                else analysis_df)

    # Compute a Status column from In Range / Out of Detection
    def _pdf_status(row):
        od = row.get("Out of Detection")
        try:
            od = bool(od)
        except (TypeError, ValueError):
            od = False
        if od:
            return "Out of detection"
        ir = row.get("In Range")
        if ir is True:
            return "In CLIA range"
        if ir is False:
            return "Outside CLIA"
        return ""
    table_df = table_df.copy()
    table_df["Status"] = table_df.apply(_pdf_status, axis=1)

    elements.append(NextPageTemplate('landscape'))
    elements.append(PageBreak())
    elements.append(Paragraph(f"Data ({len(table_df)} rows)", h2))
    cols_show = ["sample_id", "device_id", "reagent_lot", "date",
                 "age", "gender", "actual", "abs_value",
                 "Predicted", "Error%", "Abs Error%", "Bias", "Status"]
    cols_show = [c for c in cols_show if c in table_df.columns]
    _col_labels = {
        "sample_id": "Sample ID", "device_id": "Device ID",
        "reagent_lot": "Reagent LOT", "date": "Date",
        "age": "Age", "gender": "Gender", "actual": "Actual",
        "abs_value": "Abs", "Predicted": "Predicted",
        "Error%": "Error %", "Abs Error%": "|Error %|",
        "Bias": "Bias", "Status": "Status",
    }
    # Columns that should be formatted to 2 decimal places
    _2dp_cols = {"Predicted", "Error%", "Abs Error%", "Bias"}
    headers = [_col_labels.get(c, c) for c in cols_show]
    rows = [headers]
    for _, r in table_df.iterrows():
        row = []
        for c in cols_show:
            v = r.get(c)
            if c == "date":
                if pd.notna(v):
                    try:
                        row.append(pd.Timestamp(v).strftime("%Y-%m-%d"))
                    except Exception:
                        row.append(str(v))
                else:
                    row.append("")
            elif c in _2dp_cols:
                try:
                    f = float(v)
                    row.append(f"{f:.2f}" if np.isfinite(f) else "-")
                except (TypeError, ValueError):
                    row.append("-")
            elif isinstance(v, float):
                row.append(_fmt(v))
            else:
                row.append("" if v is None else str(v))
        rows.append(row)
    _col_w = {
        "sample_id": 24, "device_id": 22, "reagent_lot": 22,
        "date": 19, "age": 10, "gender": 14, "actual": 15,
        "abs_value": 15, "Predicted": 18, "Error%": 16,
        "Abs Error%": 16, "Bias": 14, "Status": 24,
    }
    _data_col_widths = [_col_w.get(c, 15) * mm for c in cols_show]
    data_tbl = Table(rows, repeatRows=1, hAlign="CENTER",
                     colWidths=_data_col_widths)
    data_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), rl.HexColor("#1E3A8A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [rl.white, rl.HexColor("#F8FAFC")]),
        ("GRID", (0, 0), (-1, -1), 0.25, rl.HexColor("#E2E8F0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(data_tbl)

    doc.build(elements)
    return buf.getvalue()
