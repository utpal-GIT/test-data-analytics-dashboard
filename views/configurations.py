"""
Configurations tab — per-user CRUD over test parameter definitions.

CLIA acceptance modes:
    value       -> TV +/- absolute value
    percent     -> TV +/- percent of |TV|
    greater_of  -> TV +/- max(absolute value, percent of |TV|)
    threshold   -> TV +/- value when |TV| <= T, else TV +/- percent of |TV|

UX:
    - "Add new parameter" and the search box at the top
    - Everything already configured is listed as a table, with edit and
      delete buttons on each row
    - Adding or editing opens a modal dialog over the list
"""

from __future__ import annotations

from html import escape

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
#   None                     -> nothing being edited (cards-only view)
#   ""                       -> creating a new parameter
#   ("Vendor A", "Albumin")  -> editing that vendor's Albumin configuration
EDIT_KEY = "cfg_edit_target"


def _key_of(p: dict) -> tuple[str, str]:
    """A configuration is identified by its vendor and parameter name."""
    return ((p.get("vendor") or "").strip(), p.get("name") or "")


def _slug(*parts) -> str:
    """Widget-key fragment: two vendors can hold the same parameter name."""
    return "__".join(
        "".join(ch if ch.isalnum() else "-" for ch in str(x)) for x in parts)


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

    params = db.parameters_for(user["id"])   # cached until saved or deleted

    # Top toolbar: search + Add new
    tcol1, tcol2 = st.columns([3, 1])
    query = tcol1.text_input(
        "Search parameters", placeholder="Filter by vendor or name…",
        label_visibility="collapsed", key="cfg_search",
    )
    if tcol2.button("➕  Add new parameter", use_container_width=True,
                    type="primary", key="cfg_add_new"):
        st.session_state[EDIT_KEY] = ""   # empty string = creating new
        st.rerun()

    filt = (query or "").strip().lower()
    visible = [p for p in params
               if not filt or filt in p["name"].lower()
               or filt in (p.get("vendor") or "").lower()]

    _render_parameter_table(user, visible, params)

    # Adding or editing happens in a modal over the list, so the fields are
    # the only thing to look at while they are being filled in.
    if st.session_state.get(EDIT_KEY) is not None:
        _edit_dialog(user, params)


def _dismiss_dialog() -> None:
    """Closing the modal with X, Esc or a click outside has to clear the edit
    target too. Without this the target survives the dismissal, and because
    every tab re-runs on any interaction, the next click anywhere in the app
    would pop the dialog straight back up."""
    st.session_state[EDIT_KEY] = None


@st.dialog("Parameter configuration", width="large",
           on_dismiss=_dismiss_dialog)
def _edit_dialog(user: dict, params: list[dict]) -> None:
    _render_edit_panel(user, params)


# ---------------------------------------------------------------------------
# table of existing configurations
# ---------------------------------------------------------------------------
# Vendor, Parameter, Normal (M), Normal (F), Detection, CLIA, edit, delete
_COL_WIDTHS = [1.1, 1.2, 1.0, 1.0, 1.0, 1.9, 0.42, 0.42]
_COL_HEADS = ["Vendor", "Parameter", "Normal (M)", "Normal (F)",
              "Detection", "CLIA acceptance", "", ""]


def _render_parameter_table(
    user: dict, visible: list[dict], all_params: list[dict],
) -> None:
    if not all_params:
        style.section("Existing parameters")
        st.info("No parameters yet. Click **➕ Add new parameter** to create "
                "your first one.")
        return

    style.section(
        "Existing parameters",
        f"{len(visible)} of {len(all_params)} shown"
        if visible != all_params else f"{len(all_params)} configured",
    )
    if not visible:
        st.info("No parameters match your search.")
        return

    head = st.columns(_COL_WIDTHS, vertical_alignment="center")
    for col, label in zip(head, _COL_HEADS):
        col.markdown(f'<div class="cfg-th">{label}</div>',
                     unsafe_allow_html=True)

    for p in visible:
        vendor = (p.get("vendor") or "").strip()
        name = p["name"]
        sfx = _slug(vendor, name)
        cells = [
            vendor or "—",
            name,
            _range_str(p.get("normal_male") or {}),
            _range_str(p.get("normal_female") or {}),
            _range_str(p.get("detection") or {}),
            _clia_str(p.get("clia") or {}),
        ]
        row = st.columns(_COL_WIDTHS, vertical_alignment="center")
        for col, value in zip(row, cells):
            col.markdown(f'<div class="cfg-td">{escape(str(value))}</div>',
                         unsafe_allow_html=True)

        if row[6].button("✏", key=f"cfg_row_edit__{sfx}",
                         help=f"Edit {name} ({vendor or 'no vendor'})"):
            st.session_state[EDIT_KEY] = (vendor, name)
            st.rerun()

        # two-click delete: the first click arms this row, the second removes it
        del_key = f"cfg_row_del_armed__{sfx}"
        armed = st.session_state.get(del_key, False)
        if row[7].button("✓" if armed else "🗑", key=f"cfg_row_del__{sfx}",
                         help=("Click again to delete" if armed
                               else f"Delete {name} ({vendor or 'no vendor'})")):
            if armed:
                db.delete_parameter(user["id"], name, vendor)
                st.session_state.pop(del_key, None)
                if st.session_state.get(EDIT_KEY) == (vendor, name):
                    st.session_state[EDIT_KEY] = None
                st.toast(f"Deleted '{name}' for {vendor or 'no vendor'}.")
            else:
                st.session_state[del_key] = True
            st.rerun()
        if armed:
            st.warning(f"Click ✓ again to delete '{name}' for "
                       f"{vendor or 'no vendor'}.", icon="⚠")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# edit / add panel  (no st.form so the preview updates live as you type)
# ---------------------------------------------------------------------------
def _render_edit_panel(user: dict, params: list[dict]) -> None:
    target = st.session_state[EDIT_KEY]
    is_new = (target == "")
    seed: dict = {}
    if not is_new:
        key = tuple(target) if isinstance(target, (tuple, list)) else ("", target)
        seed = next((p for p in params if _key_of(p) == key), {})
        if not seed:
            st.session_state[EDIT_KEY] = None
            st.rerun()

    _seed_vendor = (seed.get("vendor") or "").strip()
    title = ("Add new parameter" if is_new
             else f"Edit · {_seed_vendor or 'no vendor'} · {seed.get('name')}")
    style.section(title, "Fill in the ranges and CLIA acceptance rule")

    # Use a per-target widget key suffix so switching between rows doesn't
    # carry over typed values from a prior edit session.
    sfx = "new" if is_new else _slug(_seed_vendor, seed.get("name"))

    with st.container(border=True):
        ncol1, ncol2 = st.columns(2)
        vendor = ncol1.text_input(
            "Reagent vendor", value=_seed_vendor,
            placeholder="e.g. Wiz Bio",
            key=f"cfg_vendor__{sfx}",
            help="Configurations are per vendor: the same parameter can be "
                 "configured once for each reagent vendor.",
        )
        name = ncol2.text_input("Parameter name", value=seed.get("name", ""),
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
        _v = vendor.strip()
        if is_new and any(_key_of(p) == (_v, name.strip()) for p in params):
            st.error(f"'{name.strip()}' is already configured for "
                     f"{_v or 'no vendor'}.")
            return
        cfg = {
            "name": name.strip(),
            "vendor": _v,
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
        if not is_new and seed.get("name") and _key_of(seed) != (_v, cfg["name"]):
            # renamed or moved to another vendor: drop the old row
            db.delete_parameter(user["id"], seed["name"], _seed_vendor)
        db.upsert_parameter(user["id"], cfg)
        st.session_state[EDIT_KEY] = None
        st.success(f"Saved '{cfg['name']}' for {_v or 'no vendor'}.")
        st.rerun()

    if delete:
        db.delete_parameter(user["id"], seed.get("name"), _seed_vendor)
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
