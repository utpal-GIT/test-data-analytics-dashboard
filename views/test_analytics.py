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

DEFAULT_EMPTY_ROWS = 10


# ---------------------------------------------------------------------------
# state / dtype helpers
# ---------------------------------------------------------------------------
def _empty_rows(n: int = DEFAULT_EMPTY_ROWS) -> pd.DataFrame:
    df = pd.DataFrame({
        "Selected":    [True] * n,
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
        "Selected":    True,
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
    pad = DEFAULT_EMPTY_ROWS - len(df)
    if pad > 0:
        df = pd.concat([df, _empty_rows(pad)], ignore_index=True)
    return _coerce_dtypes(df)


def _coerce_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    if "Selected" not in df.columns:
        df["Selected"] = False
    df["Selected"] = df["Selected"].fillna(True).astype(bool)
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
    hcol1, hcol2 = sb.columns([2, 1])
    hcol1.markdown("## Filters")
    if hcol2.button("Reset", key="flt_reset", use_container_width=True,
                    help="Clear every filter back to its default."):
        for k in FILTER_KEYS:
            st.session_state.pop(k, None)
        st.rerun()

    # ---- Input-column filters ----
    pick_param = sb.multiselect(
        "Parameter", all_known_params, default=all_known_params,
        key="flt_param",
        help="Pick a single parameter to enable performance metrics, plots, "
             "and the report export.",
    )

    devices = sorted({s for s in grid_df["Device ID"].dropna().astype(str) if s.strip()})
    samples = sorted({s for s in grid_df["Sample ID"].dropna().astype(str) if s.strip()})
    lots = sorted({s for s in grid_df["Reagent LOT"].dropna().astype(str) if s.strip()})
    raw_genders = [s for s in grid_df["Gender"].dropna().astype(str) if s.strip()]
    gender_buckets = sorted({_canonical_gender(g) for g in raw_genders})

    pick_dev = sb.multiselect("Device ID", devices, default=devices, key="flt_device")
    pick_samp = sb.multiselect("Sample ID", samples, default=samples, key="flt_sample")
    pick_lot = sb.multiselect("Reagent LOT", lots, default=lots, key="flt_lot")
    pick_gender = sb.multiselect("Gender", gender_buckets, default=gender_buckets,
                                 key="flt_gender")

    dates = pd.to_datetime(grid_df["Date"], errors="coerce").dropna()
    if len(dates) >= 1:
        d_min, d_max = dates.min().date(), dates.max().date()
        date_range = sb.date_input("Date range", value=(d_min, d_max),
                                   key="flt_date")
    else:
        date_range = None

    ages = pd.to_numeric(grid_df["Age"], errors="coerce").dropna()
    if len(ages) >= 1:
        a_min, a_max = float(ages.min()), float(ages.max())
        if a_min == a_max: a_max = a_min + 1
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

    # Sidebar filters
    filters = _render_filters(grid_df, all_known)

    # Sync: plot exclusions ↔ Selected column in the grid
    _excl_ids = set(str(s) for s in filters.get("plot_exclude", []))
    _prev_excl = set(str(s) for s in st.session_state.get("_prev_plot_exclude", []))
    _need_sync = False
    # Newly excluded → deselect
    _newly_excluded = _excl_ids - _prev_excl
    if _newly_excluded:
        mask = grid_df["Sample ID"].astype(str).isin(_newly_excluded)
        if mask.any() and grid_df.loc[mask, "Selected"].any():
            grid_df.loc[mask, "Selected"] = False
            _need_sync = True
    # Newly un-excluded → re-select
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
        metrics_df.loc[inactive_mask, "Out of Detection"] = False

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
    combined["In Range"]         = metrics_df["In Range"].astype("object").values
    combined["Out of Detection"] = metrics_df["Out of Detection"].fillna(False).astype(bool).values

    # Status column: a single visible cell that colour-codes the row state.
    # Canvas-based st.data_editor doesn't render Styler row backgrounds, so
    # this is the reliable way to surface the state visually in every row.
    def _row_status(row):
        if bool(row.get("Out of Detection")):
            return "🔴  Out of detection"
        ir = row.get("In Range")
        if ir is True:
            return "🟢  In CLIA range"
        if ir is False:
            return "🟡  Outside CLIA"
        return ""
    combined["Status"] = [_row_status(r) for _, r in combined.iterrows()]
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
    bcol1, bcol2, bcol3, bcol4, bcol5, _ = st.columns([1, 1, 1, 1, 1, 2])
    if bcol1.button("➕  Add 10 rows", key="add_rows"):
        st.session_state[GRID_KEY] = pd.concat(
            [grid_df, _empty_rows(10)], ignore_index=True
        )
        st.rerun()
    if bcol2.button("☑  Select all", key="select_all"):
        grid_df["Selected"] = True
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()
    if bcol3.button("☐  Deselect all", key="deselect_all"):
        grid_df["Selected"] = False
        st.session_state[GRID_KEY] = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
        db.replace_all_samples(user["id"], _grid_to_db(grid_df[GRID_INPUT_COLS]))
        st.rerun()
    if bcol4.button("🗑  Clear selected", key="clear_sel"):
        kept = grid_df[~grid_df["Selected"].fillna(False).astype(bool)].reset_index(drop=True)
        if len(kept) < DEFAULT_EMPTY_ROWS:
            kept = pd.concat([kept, _empty_rows(DEFAULT_EMPTY_ROWS - len(kept))],
                             ignore_index=True)
        st.session_state[GRID_KEY] = kept
        db.replace_all_samples(user["id"], _grid_to_db(kept))
        st.rerun()
    if bcol5.button("🧹  Clear all", key="clear_all"):
        st.session_state[GRID_KEY] = _empty_rows()
        db.replace_all_samples(user["id"], [])
        st.rerun()



    def _row_style(row):
        # Priority: out-of-detection (red) > In CLIA range (green) >
        #           outside CLIA range but in detection (amber) > default
        if bool(row.get("Out of Detection")):
            return ["background-color: #FEE2E2"] * len(row)   # red
        ir = row.get("In Range")
        if ir is True:
            return ["background-color: #DCFCE7"] * len(row)   # green
        if ir is False:
            return ["background-color: #FEF3C7"] * len(row)   # amber
        return [""] * len(row)

    try:
        styled = (combined.style
                  .apply(_row_style, axis=1)
                  .set_properties(**{"text-align": "center"})
                  .set_table_styles([{"selector": "th",
                                      "props": [("text-align", "center")]}]))
    except (ImportError, AttributeError):
        styled = combined

    # Color-key legend for the row tints
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
    col_cfg["Date"] = st.column_config.DateColumn("Date", format="YYYY-MM-DD")
    grid_height = min(max(len(combined) * 35 + 50, 200), 800)
    try:
        edited = st.data_editor(
            combined,
            key="multi_editor_v4",
            num_rows="dynamic",
            use_container_width=True,
            height=grid_height,
            column_config=col_cfg,
        )
    except Exception as _exc:
        import traceback as _tb
        st.error(f"data_editor failed: {type(_exc).__name__}: {_exc}")
        st.code(_tb.format_exc())
        edited = combined.copy()
    # Persist input-only edits (use a lenient comparison to avoid
    # infinite reruns from dtype round-trip differences in the editor)
    input_only = _coerce_dtypes(edited[GRID_INPUT_COLS].copy())
    prev = _coerce_dtypes(grid_df[GRID_INPUT_COLS].copy())
    changed = False
    for col in GRID_INPUT_COLS:
        try:
            if not input_only[col].equals(prev[col]):
                changed = True
                break
        except Exception:
            changed = True
            break
    if changed:
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
            title1 = st.text_input("Title", value="Abs vs Actual", key="chart1_title")
            x1lbl = st.text_input("X-axis", value="Actual", key="chart1_x")
            y1lbl = st.text_input("Y-axis", value="Abs", key="chart1_y")
        with cc2:
            st.markdown("**Passing-Bablok**")
            title2 = st.text_input("Title", value="Passing-Bablok", key="chart2_title")
            x2lbl = st.text_input("X-axis", value="Actual", key="chart2_x")
            y2lbl = st.text_input("Y-axis", value="Predicted", key="chart2_y")
        with cc3:
            st.markdown("**Bland-Altman**")
            title3 = st.text_input("Title", value="Bland-Altman", key="chart3_title")
            x3lbl = st.text_input("X-axis", value="Mean of Actual & Predicted", key="chart3_x")
            y3lbl = st.text_input("Y-axis", value="Predicted − Actual", key="chart3_y")

    excl = st.session_state.get("plot_exclude", [])
    if excl:
        df = df[~df["sample_id"].astype(str).isin([str(s) for s in excl])]

    actual = pd.to_numeric(df["actual"], errors="coerce").to_numpy()
    abs_v = pd.to_numeric(df["abs_value"], errors="coerce").to_numpy()
    pred = pd.to_numeric(df["Predicted"], errors="coerce").to_numpy()
    sids = df["sample_id"].astype(str).to_numpy()
    out_of_det = df.get("Out of Detection",
                        pd.Series([False] * len(df))).fillna(False).to_numpy()
    in_range_raw = df.get("In Range",
                          pd.Series([None] * len(df)))
    outside_clia = np.array(
        [(v is False) for v in in_range_raw], dtype=bool) & ~out_of_det
    in_clia = ~out_of_det & ~outside_clia

    # Shared marker styles for the three categories
    _mk_ok = dict(size=9, color=style.PALETTE["primary"],
                  line=dict(width=1, color="white"))
    _mk_clia = dict(size=10, color=style.PALETTE["warning"], symbol="diamond",
                    line=dict(width=1, color="white"))
    _mk_det = dict(size=11, color=style.PALETTE["danger"], symbol="x")

    def _add_categorised_scatter(fig, x, y, ok_mask):
        """Add In CLIA / Outside CLIA / Out of detection traces to `fig`."""
        m_ok = ok_mask & in_clia
        m_clia = ok_mask & outside_clia
        m_det = ok_mask & out_of_det
        if m_ok.any():
            fig.add_trace(go.Scatter(
                x=x[m_ok], y=y[m_ok], mode="markers",
                name="In CLIA range", text=sids[m_ok], marker=_mk_ok))
        if m_clia.any():
            fig.add_trace(go.Scatter(
                x=x[m_clia], y=y[m_clia], mode="markers",
                name="Outside CLIA range", text=sids[m_clia], marker=_mk_clia))
        if m_det.any():
            fig.add_trace(go.Scatter(
                x=x[m_det], y=y[m_det], mode="markers",
                name="Out of detection", text=sids[m_det], marker=_mk_det))

    c1, c2 = st.columns(2)
    # Plot 1 — Abs vs Actual
    with c1:
        fig1 = go.Figure()
        ok = np.isfinite(actual) & np.isfinite(abs_v)
        _add_categorised_scatter(fig1, actual, abs_v, ok)
        if fit["success"] and len(fit["curve"][0]):
            grid_abs, grid_actual = fit["curve"]
            fig1.add_trace(go.Scatter(
                x=grid_actual, y=grid_abs, mode="lines",
                name=f"{fit['name']} fit",
                line=dict(color=style.PALETTE["accent"], width=2.5),
            ))
        fig1.update_layout(title=title1, xaxis_title=x1lbl, yaxis_title=y1lbl,
                           **style.plotly_layout())
        st.plotly_chart(fig1, use_container_width=True)

    # Plot 2 — Passing-Bablok
    with c2:
        ok2 = np.isfinite(actual) & np.isfinite(pred)
        pb = models.passing_bablok(actual[ok2], pred[ok2])
        fig2 = go.Figure()
        _add_categorised_scatter(fig2, actual, pred, ok2)
        if ok2.any():
            lo = float(min(np.nanmin(actual[ok2]), np.nanmin(pred[ok2])))
            hi = float(max(np.nanmax(actual[ok2]), np.nanmax(pred[ok2])))
            grid = np.linspace(lo, hi, 100)
            fig2.add_trace(go.Scatter(
                x=grid, y=grid, mode="lines", name="Identity (y = x)",
                line=dict(color=style.PALETTE["muted"], dash="dot"),
            ))
            if np.isfinite(pb["slope"]) and np.isfinite(pb["intercept"]):
                fig2.add_trace(go.Scatter(
                    x=grid, y=pb["slope"] * grid + pb["intercept"],
                    mode="lines", name="Passing-Bablok fit",
                    line=dict(color=style.PALETTE["warning"], width=2.5),
                ))
        fig2.update_layout(title=title2, xaxis_title=x2lbl, yaxis_title=y2lbl,
                           **style.plotly_layout())
        st.plotly_chart(fig2, use_container_width=True)

    # Plot 3 — Bland-Altman
    ok3 = np.isfinite(actual) & np.isfinite(pred)
    means = (actual[ok3] + pred[ok3]) / 2.0
    diffs = pred[ok3] - actual[ok3]
    sids3 = sids[ok3]
    out_det3 = out_of_det[ok3]
    outside_clia3 = outside_clia[ok3]
    in_clia3 = in_clia[ok3]
    fig3 = go.Figure()
    if in_clia3.any():
        fig3.add_trace(go.Scatter(
            x=means[in_clia3], y=diffs[in_clia3], mode="markers",
            name="In CLIA range", text=sids3[in_clia3], marker=_mk_ok))
    if outside_clia3.any():
        fig3.add_trace(go.Scatter(
            x=means[outside_clia3], y=diffs[outside_clia3], mode="markers",
            name="Outside CLIA range", text=sids3[outside_clia3], marker=_mk_clia))
    if out_det3.any():
        fig3.add_trace(go.Scatter(
            x=means[out_det3], y=diffs[out_det3], mode="markers",
            name="Out of detection", text=sids3[out_det3], marker=_mk_det))
    if len(diffs):
        bias = float(np.mean(diffs))
        sd = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
        loa_hi = bias + 1.96 * sd
        loa_lo = bias - 1.96 * sd
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
    fig3.update_layout(title=title3, xaxis_title=x3lbl, yaxis_title=y3lbl,
                       **style.plotly_layout(height=460))
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
) -> bytes:
    """Build a multi-page PDF report and return the binary content."""
    _ensure_chrome_for_kaleido()
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors as rl
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
        PageBreak,
    )
    from reportlab.lib.enums import TA_LEFT

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Test Analytics Report - {param_name}",
        author="Test Analytics Dashboard",
    )
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

    # ---- Plots ----
    elements.append(PageBreak())
    elements.append(Paragraph("Plots", h2))
    actual = pd.to_numeric(analysis_df["actual"], errors="coerce").to_numpy()
    abs_v  = pd.to_numeric(analysis_df["abs_value"], errors="coerce").to_numpy()
    pred   = pd.to_numeric(analysis_df["Predicted"], errors="coerce").to_numpy()
    sids   = analysis_df["sample_id"].astype(str).to_numpy()

    def _add_plot_image(fig, caption: str):
        try:
            png = fig.to_image(format="png", width=900, height=420, scale=2,
                               engine="kaleido")
        except Exception as exc:
            elements.append(Paragraph(
                f"<i>(plot could not be rendered: {exc})</i>", body))
            return
        img = Image(io.BytesIO(png), width=180 * mm, height=84 * mm)
        elements.append(Paragraph(caption, h3))
        elements.append(img)
        elements.append(Spacer(1, 4 * mm))

    # Plot 1
    fig1 = go.Figure()
    ok = np.isfinite(actual) & np.isfinite(abs_v)
    fig1.add_trace(go.Scatter(x=actual[ok], y=abs_v[ok], mode="markers",
                              name="Sample", text=sids[ok]))
    if fit.get("success") and len(fit.get("curve", (None, None))[0]):
        gabs, gact = fit["curve"]
        fig1.add_trace(go.Scatter(x=gact, y=gabs, mode="lines",
                                  name=f"{fit['name']} fit"))
    fig1.update_layout(title="", xaxis_title="Actual", yaxis_title="Abs",
                       margin=dict(l=40, r=20, t=20, b=40))
    _add_plot_image(fig1, "Abs vs Actual (with fitted calibration curve)")

    # Plot 2 — PB
    ok2 = np.isfinite(actual) & np.isfinite(pred)
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=actual[ok2], y=pred[ok2], mode="markers",
                              name="Sample", text=sids[ok2]))
    if ok2.any():
        lo = float(min(np.nanmin(actual[ok2]), np.nanmin(pred[ok2])))
        hi = float(max(np.nanmax(actual[ok2]), np.nanmax(pred[ok2])))
        grid = np.linspace(lo, hi, 100)
        fig2.add_trace(go.Scatter(x=grid, y=grid, mode="lines",
                                  name="Identity (y = x)",
                                  line=dict(dash="dot")))
        if np.isfinite(pb_stats.get("slope", float("nan"))):
            fig2.add_trace(go.Scatter(
                x=grid, y=pb_stats["slope"] * grid + pb_stats["intercept"],
                mode="lines", name="Passing-Bablok fit"))
    fig2.update_layout(title="", xaxis_title="Actual", yaxis_title="Predicted",
                       margin=dict(l=40, r=20, t=20, b=40))
    _add_plot_image(fig2, "Passing-Bablok: Actual vs Predicted")

    # Plot 3 — Bland-Altman
    means = (actual[ok2] + pred[ok2]) / 2.0
    diffs = pred[ok2] - actual[ok2]
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(x=means, y=diffs, mode="markers",
                              name="Sample", text=sids[ok2]))
    if len(diffs):
        bias = float(np.mean(diffs))
        sd = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
        loa_hi = bias + 1.96 * sd
        loa_lo = bias - 1.96 * sd
        x_lo = float(np.min(means)) if len(means) else 0
        x_hi = float(np.max(means)) if len(means) else 1
        if x_lo == x_hi: x_hi = x_lo + 1
        for y_val, lbl in [(bias, f"Bias {bias:.3f}"),
                           (loa_hi, f"+1.96 SD {loa_hi:.3f}"),
                           (loa_lo, f"-1.96 SD {loa_lo:.3f}")]:
            fig3.add_trace(go.Scatter(
                x=[x_lo, x_hi], y=[y_val, y_val], mode="lines",
                name=lbl, line=dict(dash="dash")))
    fig3.update_layout(title="",
                       xaxis_title="Mean of Actual & Predicted",
                       yaxis_title="Predicted - Actual",
                       margin=dict(l=40, r=20, t=20, b=40))
    _add_plot_image(fig3, "Bland-Altman: Predicted - Actual")

    # ---- Data table ----
    elements.append(PageBreak())
    elements.append(Paragraph(f"Data ({len(analysis_df)} rows)", h2))
    cols_show = ["sample_id", "device_id", "reagent_lot", "actual",
                 "abs_value", "Predicted", "Error%", "Bias"]
    cols_show = [c for c in cols_show if c in analysis_df.columns]
    headers = [{"sample_id": "Sample ID", "device_id": "Device ID",
                "reagent_lot": "Reagent LOT", "actual": "Actual",
                "abs_value": "Abs", "Predicted": "Predicted",
                "Error%": "Error %", "Bias": "Bias"}.get(c, c)
               for c in cols_show]
    rows = [headers]
    for _, r in analysis_df.iterrows():
        row = []
        for c in cols_show:
            v = r.get(c)
            if isinstance(v, float):
                row.append(_fmt(v))
            else:
                row.append("" if v is None else str(v))
        rows.append(row)
    data_tbl = Table(rows, repeatRows=1, hAlign="LEFT")
    data_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
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
