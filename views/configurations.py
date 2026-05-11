"""
Configurations tab — per-user CRUD over test parameter definitions.

CLIA acceptance modes:
    value       -> TV +/- absolute value
    percent     -> TV +/- percent of |TV|
    greater_of  -> TV +/- max(absolute value, percent of |TV|)
    threshold   -> TV +/- value when |TV| <= T, else TV +/- percent of |TV|

UX:
    - Cards grid for existing parameters with per-card Edit / Delete buttons
    - "Add new parameter" button at the top
    - Edit form appears below when editing or adding (Cancel returns to list)
"""

from __future__ import annotations

import math

import streamlit as st

import auth
import db
import style
from clia import tolerance_for


CLIA_MODES = {
    "value":      "TV +/- absolute value",
    "percent":    "TV +/- percent",
    "greater_of": "TV +/- greater of (value, percent)",
    "threshold":  "Threshold split (value below, percent above)",
}

# session-state key holds the current edit target:
#   None       -> nothing being edited (cards-only view)
#   ""         -> creating a new parameter
#   "Albumin"  -> editing the parameter named Albumin
EDIT_KEY = "cfg_edit_target"


# ---------------------------------------------------------------------------
# parsing / formatting helpers
# ---------------------------------------------------------------------------
def _text(v) -> str:
    return "" if v is None else str(v)


def _to_float(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _range_dict(low_s, high_s) -> dict:
    return {"low": _to_float(low_s), "high": _to_float(high_s)}


def _mode_index(mode_key: str | None) -> int:
    keys = list(CLIA_MODES.keys())
    if not mode_key or mode_key not in CLIA_MODES:
        return 0
    return keys.index(mode_key)


def _mode_key(label: str) -> str:
    for k, v in CLIA_MODES.items():
        if v == label:
            return k
    return "value"


def _build_clia(mode, value, percent, threshold, low_value, high_percent) -> dict:
    out: dict = {"mode": mode}
    if mode == "value":
        out["value"] = _to_float(value)
    elif mode == "percent":
        out["percent"] = _to_float(percent)
    elif mode == "greater_of":
        out["value"] = _to_float(value)
        out["percent"] = _to_float(percent)
    elif mode == "threshold":
        out["threshold"] = _to_float(threshold)
        out["low_value"] = _to_float(low_value)
        out["high_percent"] = _to_float(high_percent)
    return out


def _validate_clia(clia: dict) -> tuple[bool, str]:
    mode = clia.get("mode")
    if mode == "value" and clia.get("value") is None:
        return False, "CLIA: '+/- absolute value' is required."
    if mode == "percent" and clia.get("percent") is None:
        return False, "CLIA: '+/- percent of target' is required."
    if mode == "greater_of":
        if clia.get("value") is None or clia.get("percent") is None:
            return False, "CLIA: both '+/- absolute value' and '+/- percent' are required."
    if mode == "threshold":
        for k, label in (("threshold", "Threshold T"),
                         ("low_value", "+/- value below threshold"),
                         ("high_percent", "+/- percent above threshold")):
            if clia.get(k) is None:
                return False, f"CLIA: '{label}' is required."
    return True, ""


def _range_str(r: dict) -> str:
    if not r:
        return "—"
    lo, hi = r.get("low"), r.get("high")
    if lo is None and hi is None:
        return "—"
    lo_s = "−∞" if lo is None else f"{lo:g}"
    hi_s = "∞" if hi is None else f"{hi:g}"
    return f"{lo_s} … {hi_s}"


def _clia_str(clia: dict) -> str:
    if not clia:
        return "—"
    mode = clia.get("mode")
    if mode == "value":
        return f"± {clia.get('value')} (absolute)"
    if mode == "percent":
        return f"± {clia.get('percent')} % of TV"
    if mode == "greater_of":
        return f"± greater of {clia.get('value')} or {clia.get('percent')} %"
    if mode == "threshold":
        return (f"± {clia.get('low_value')} when |TV| ≤ {clia.get('threshold')}, "
                f"else ± {clia.get('high_percent')} %")
    return "—"


# ---------------------------------------------------------------------------
# main render
# ---------------------------------------------------------------------------
def render() -> None:
    user = auth.current_user()
    style.section(
        "Test parameter configurations",
        "Each parameter is selectable in the Test Analytics tab · private to your account",
    )

    params = db.list_parameters(user["id"])

    # Top toolbar: search + Add new
    tcol1, tcol2 = st.columns([3, 1])
    query = tcol1.text_input(
        "Search parameters", placeholder="Filter by name…", label_visibility="collapsed",
        key="cfg_search",
    )
    if tcol2.button("➕  Add new parameter", use_container_width=True,
                    type="primary", key="cfg_add_new"):
        st.session_state[EDIT_KEY] = ""   # empty string = creating new
        st.rerun()

    # Filter cards by search
    filt = (query or "").strip().lower()
    visible = [p for p in params if not filt or filt in p["name"].lower()]

    _render_parameter_cards(user, visible, params)

    # If editing or adding, show the form panel below
    if EDIT_KEY in st.session_state and st.session_state[EDIT_KEY] is not None:
        _render_edit_panel(user, params)


# ---------------------------------------------------------------------------
# cards grid
# ---------------------------------------------------------------------------
def _render_parameter_cards(
    user: dict, visible: list[dict], all_params: list[dict],
) -> None:
    if not all_params:
        style.section("Existing parameters")
        st.info("No parameters yet. Click **➕ Add new parameter** to create your first one.")
        return

    style.section(
        "Existing parameters",
        f"{len(visible)} of {len(all_params)} shown" if visible != all_params
        else f"{len(all_params)} configured",
    )

    if not visible:
        st.info("No parameters match your search.")
        return

    n_cols = 3
    rows = math.ceil(len(visible) / n_cols)
    for r in range(rows):
        cols = st.columns(n_cols)
        for c in range(n_cols):
            i = r * n_cols + c
            if i >= len(visible):
                continue
            p = visible[i]
            with cols[c]:
                _render_card(user, p)


def _render_card(user: dict, p: dict) -> None:
    """Render a single parameter card with Edit / Delete buttons."""
    name = p["name"]
    nm = p.get("normal_male") or {}
    nf = p.get("normal_female") or {}
    det = p.get("detection") or {}
    clia = p.get("clia") or {}

    with st.container(border=True):
        st.markdown(
            f'<div class="cfg-card-name">{name}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="cfg-card-body">'
            f'<div><span class="lbl">Normal (M)</span><span class="val">{_range_str(nm)}</span></div>'
            f'<div><span class="lbl">Normal (F)</span><span class="val">{_range_str(nf)}</span></div>'
            f'<div><span class="lbl">Detection</span><span class="val">{_range_str(det)}</span></div>'
            f'<div><span class="lbl">CLIA</span><span class="val">{_clia_str(clia)}</span></div>'
            '</div>',
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        if b1.button("✏  Edit", key=f"edit_{name}", use_container_width=True):
            st.session_state[EDIT_KEY] = name
            st.rerun()
        # two-click delete: first click arms, second click confirms
        del_key = f"del_armed_{name}"
        armed = st.session_state.get(del_key, False)
        label = "✓  Confirm delete" if armed else "🗑  Delete"
        if b2.button(label, key=f"delete_{name}", use_container_width=True):
            if armed:
                db.delete_parameter(user["id"], name)
                st.session_state.pop(del_key, None)
                st.success(f"Deleted '{name}'.")
                st.rerun()
            else:
                st.session_state[del_key] = True
                st.warning(f"Click 'Confirm delete' again to remove '{name}'.")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# edit / add panel  (no st.form so the preview updates live as you type)
# ---------------------------------------------------------------------------
def _render_edit_panel(user: dict, params: list[dict]) -> None:
    target = st.session_state[EDIT_KEY]
    is_new = (target == "")
    seed: dict = {}
    if not is_new:
        seed = next((p for p in params if p["name"] == target), {})
        if not seed:
            st.session_state[EDIT_KEY] = None
            st.rerun()

    title = "Add new parameter" if is_new else f"Edit · {seed.get('name')}"
    style.section(title, "Fill in the ranges and CLIA acceptance rule")

    # Use a per-target widget key suffix so switching between rows doesn't
    # carry over typed values from a prior edit session.
    sfx = "new" if is_new else target

    with st.container(border=True):
        name = st.text_input("Parameter name", value=seed.get("name", ""),
                             placeholder="e.g. Albumin",
                             key=f"cfg_name__{sfx}")

        st.markdown("**Reference ranges**")
        rng = st.columns(3)
        with rng[0]:
            st.caption("Normal — Male")
            nm_low = st.text_input("Low", key=f"nm_low__{sfx}",
                value=_text((seed.get("normal_male") or {}).get("low")))
            nm_high = st.text_input("High", key=f"nm_high__{sfx}",
                value=_text((seed.get("normal_male") or {}).get("high")))
        with rng[1]:
            st.caption("Normal — Female")
            nf_low = st.text_input("Low", key=f"nf_low__{sfx}",
                value=_text((seed.get("normal_female") or {}).get("low")))
            nf_high = st.text_input("High", key=f"nf_high__{sfx}",
                value=_text((seed.get("normal_female") or {}).get("high")))
        with rng[2]:
            st.caption("Detection range")
            det_low = st.text_input("Low", key=f"det_low__{sfx}",
                value=_text((seed.get("detection") or {}).get("low")))
            det_high = st.text_input("High", key=f"det_high__{sfx}",
                value=_text((seed.get("detection") or {}).get("high")))

        st.markdown("**CLIA acceptance window**")
        st.caption("Defines how close the predicted value must be to the actual "
                   "(target) value to count as 'In Range'.")

        seed_clia = seed.get("clia") or {}
        mode_label = st.selectbox(
            "Rule", list(CLIA_MODES.values()),
            index=_mode_index(seed_clia.get("mode")),
            key=f"clia_mode__{sfx}",
        )
        mode_key = _mode_key(mode_label)

        clia_value = clia_percent = clia_threshold = ""
        clia_low_value = clia_high_percent = ""

        if mode_key == "value":
            clia_value = st.text_input(
                "± absolute value", key=f"clia_value__{sfx}",
                value=_text(seed_clia.get("value")),
                help="Tolerance is fixed at this absolute amount, regardless of TV. "
                     "E.g. 0.3 means the predicted value must be within ± 0.3 of TV.",
            )
        elif mode_key == "percent":
            clia_percent = st.text_input(
                "± percent of target", key=f"clia_percent__{sfx}",
                value=_text(seed_clia.get("percent")),
                help="Enter as a percentage value: 8 means 8 %. "
                     "Tolerance = (percent / 100) × |TV|.",
            )
        elif mode_key == "greater_of":
            cgr1, cgr2 = st.columns(2)
            clia_value = cgr1.text_input(
                "± absolute value", key=f"clia_value__{sfx}",
                value=_text(seed_clia.get("value")),
                help="Acts as a floor for low TVs.",
            )
            clia_percent = cgr2.text_input(
                "± percent of target", key=f"clia_percent__{sfx}",
                value=_text(seed_clia.get("percent")),
                help="Enter as a percentage value: 10 means 10 %.",
            )
            st.caption("Tolerance = max(absolute value, (percent / 100) · |TV|). "
                       "Standard CLIA 2025 pattern.")
        elif mode_key == "threshold":
            ct1, ct2, ct3 = st.columns(3)
            clia_threshold = ct1.text_input(
                "Threshold T", key=f"clia_threshold__{sfx}",
                value=_text(seed_clia.get("threshold")))
            clia_low_value = ct2.text_input(
                "± value (when |TV| ≤ T)", key=f"clia_low_value__{sfx}",
                value=_text(seed_clia.get("low_value")))
            clia_high_percent = ct3.text_input(
                "± percent (when |TV| > T)", key=f"clia_high_percent__{sfx}",
                value=_text(seed_clia.get("high_percent")))

        # ---- Live tolerance preview (renders fresh on every rerun) ----
        st.markdown("**Live tolerance preview**")
        prv1, prv2 = st.columns([1, 3])
        preview_tv = prv1.text_input(
            "Sample TV", value="5.0", key=f"preview_tv__{sfx}",
            help="Type any target value to see the resulting acceptance window.",
        )
        preview_cfg = _build_clia(
            mode_key, clia_value, clia_percent, clia_threshold,
            clia_low_value, clia_high_percent,
        )
        tv = _to_float(preview_tv)
        tol = tolerance_for(tv, preview_cfg) if tv is not None else None
        formula = _formula_for(mode_key, preview_cfg, tv)

        if tol is not None and tv is not None:
            preview_html = (
                '<div class="cfg-preview ok">'
                '<span class="lbl">Acceptance window</span>'
                f'<span class="big">[ {tv - tol:g} , {tv + tol:g} ]</span>'
                f'<span class="sub">tolerance ± {tol:g} (for TV = {tv:g})</span>'
                f'<span class="sub" style="margin-top:6px">'
                f'<b>How:</b> {formula}</span>'
                f'<span class="sub">'
                f'In Range when |Predicted − Actual| ≤ {tol:g}</span>'
                '</div>'
            )
        else:
            preview_html = (
                '<div class="cfg-preview warn">'
                '<span class="lbl">Preview unavailable</span>'
                '<span class="sub">Fill in all CLIA fields and a numeric '
                'sample TV to see the window.</span>'
                '</div>'
            )
        prv2.markdown(preview_html, unsafe_allow_html=True)

        st.markdown("")
        bcols = st.columns(3)
        cancel = bcols[0].button("Cancel", use_container_width=True,
                                 key=f"cfg_cancel__{sfx}")
        save_label = "Create parameter" if is_new else "Save changes"
        save = bcols[1].button(save_label, type="primary",
                               use_container_width=True,
                               key=f"cfg_save__{sfx}")
        delete = (bcols[2].button("Delete", use_container_width=True,
                                  key=f"cfg_delete__{sfx}")
                  if not is_new else False)

    # ---- Handlers ----
    if cancel:
        st.session_state[EDIT_KEY] = None
        st.rerun()

    if save:
        if not name.strip():
            st.error("Parameter name is required.")
            return
        if is_new and any(p["name"] == name.strip() for p in params):
            st.error(f"A parameter named '{name.strip()}' already exists.")
            return
        cfg = {
            "name": name.strip(),
            "normal_male":   _range_dict(nm_low, nm_high),
            "normal_female": _range_dict(nf_low, nf_high),
            "detection":     _range_dict(det_low, det_high),
            "clia":          _build_clia(
                mode_key, clia_value, clia_percent, clia_threshold,
                clia_low_value, clia_high_percent,
            ),
        }
        ok, msg = _validate_clia(cfg["clia"])
        if not ok:
            st.error(msg)
            return
        if not is_new and seed.get("name") and seed["name"] != cfg["name"]:
            db.delete_parameter(user["id"], seed["name"])
        db.upsert_parameter(user["id"], cfg)
        st.session_state[EDIT_KEY] = None
        st.success(f"Saved '{cfg['name']}'.")
        st.rerun()

    if delete:
        db.delete_parameter(user["id"], seed.get("name"))
        st.session_state[EDIT_KEY] = None
        st.success(f"Deleted '{seed.get('name')}'.")
        st.rerun()


def _formula_for(mode: str, cfg: dict, tv) -> str:
    """Return the human-readable tolerance formula breakdown for the preview."""
    if tv is None:
        return ""
    try:
        if mode == "value":
            v = float(cfg.get("value"))
            return f"fixed absolute = {v:g}"
        if mode == "percent":
            p = float(cfg.get("percent"))
            return (f"({p:g} / 100) × |{tv:g}| = "
                    f"{p / 100:g} × {abs(tv):g} = {abs(tv) * p / 100:g}")
        if mode == "greater_of":
            v = float(cfg.get("value"))
            p = float(cfg.get("percent"))
            pct_part = abs(tv) * p / 100
            return (f"max( {v:g} , ({p:g} / 100) × |{tv:g}| ) = "
                    f"max( {v:g} , {pct_part:g} ) = {max(v, pct_part):g}")
        if mode == "threshold":
            t = float(cfg.get("threshold"))
            if abs(tv) <= t:
                lv = float(cfg.get("low_value"))
                return (f"|{tv:g}| ≤ T={t:g}, so use absolute = {lv:g}")
            hp = float(cfg.get("high_percent"))
            return (f"|{tv:g}| > T={t:g}, so use ({hp:g} / 100) × |{tv:g}| = "
                    f"{abs(tv) * hp / 100:g}")
    except (TypeError, ValueError, KeyError):
        return ""
    return ""
