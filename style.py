"""
Visual theming: global CSS + a shared Plotly template + small UI helpers
(KPI cards, status badges, section headers).

Call `style.apply()` once at the top of app.py.
Call `style.kpi_grid([...])` and `style.section(title)` from the views.
Call `style.plotly_layout()` to get the shared layout dict for every figure.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st


# ---------------------------------------------------------------------------
# colour palette (kept in one place so charts and CSS stay in sync)
# ---------------------------------------------------------------------------
PALETTE = {
    "primary":   "#2563EB",
    "primary_50": "#EFF6FF",
    "accent":    "#06B6D4",
    "success":   "#059669",
    "warning":   "#D97706",
    "danger":    "#DC2626",
    "bg":        "#F8FAFC",
    "surface":   "#FFFFFF",
    "border":    "#E2E8F0",
    "text":      "#0F172A",
    "muted":     "#64748B",
    "chart": [
        "#2563EB", "#06B6D4", "#059669", "#D97706",
        "#DC2626", "#7C3AED", "#DB2777", "#0891B2",
    ],
}


# ---------------------------------------------------------------------------
# global CSS
# ---------------------------------------------------------------------------
_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"]  {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: {PALETTE['text']};
}}

.block-container {{
    padding-top: 2.4rem;
    padding-bottom: 4rem;
    max-width: 1500px;
}}

h1, h2, h3, h4 {{
    font-weight: 600;
    letter-spacing: -0.01em;
    color: {PALETTE['text']};
}}

/* ---------- top header band ---------- */
.app-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 22px 14px 60px;   /* extra left padding clears sidebar toggle */
    background: linear-gradient(135deg, #1E3A8A 0%, #2563EB 60%, #06B6D4 100%);
    border-radius: 14px;
    color: white;
    margin-top: 8px;
    margin-bottom: 1rem;
    box-shadow: 0 6px 16px -8px rgba(37, 99, 235, 0.45);
}}
.app-header .brand {{
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: -0.01em;
}}
.app-header .brand .dot {{
    display: inline-block;
    width: 8px; height: 8px; border-radius: 999px;
    background: #FBBF24;
    margin-right: 10px;
    box-shadow: 0 0 0 4px rgba(251, 191, 36, 0.25);
}}
.app-header .meta {{
    font-size: 0.85rem; opacity: 0.92;
    display: flex; gap: 22px; align-items: center;
}}
.app-header .meta b {{ font-weight: 600; }}

/* ---------- KPI card grid ---------- */
.kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin: 8px 0 18px;
}}
.kpi-card {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}}
.kpi-card .label {{
    font-size: 0.72rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: {PALETTE['muted']};
    margin-bottom: 6px;
}}
.kpi-card .value {{
    font-size: 1.45rem;
    font-weight: 700;
    color: {PALETTE['text']};
    line-height: 1.1;
}}
.kpi-card .sub {{
    font-size: 0.78rem;
    color: {PALETTE['muted']};
    margin-top: 4px;
}}
.kpi-card.accent     {{ border-top: 3px solid {PALETTE['primary']}; }}
.kpi-card.success    {{ border-top: 3px solid {PALETTE['success']}; }}
.kpi-card.warning    {{ border-top: 3px solid {PALETTE['warning']}; }}
.kpi-card.danger     {{ border-top: 3px solid {PALETTE['danger']}; }}

/* ---------- section header ---------- */
.section {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 24px 0 8px;
}}
.section .bar {{
    width: 4px; height: 22px; border-radius: 2px;
    background: {PALETTE['primary']};
}}
.section .title {{
    font-size: 1.05rem;
    font-weight: 600;
}}
.section .hint {{
    font-size: 0.82rem;
    color: {PALETTE['muted']};
    margin-left: 6px;
}}

/* ---------- pill / badge ---------- */
.pill {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 600;
    line-height: 1.6;
}}
.pill.success  {{ background: #DCFCE7; color: #047857; }}
.pill.danger   {{ background: #FEE2E2; color: #B91C1C; }}
.pill.muted    {{ background: #F1F5F9; color: #475569; }}
.pill.warning  {{ background: #FEF3C7; color: #B45309; }}

/* ---------- card / surface block ---------- */
.surface {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}}

/* ---------- login card ---------- */
.login-card-top {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-radius: 16px 16px 0 0;
    border-bottom: none;
    padding: 22px 22px 14px;
    margin-top: 6vh;
}}
.login-card-top .brand {{
    display: flex; align-items: center; gap: 10px;
    font-size: 1.05rem; font-weight: 700;
}}
.login-card-top .brand .logo {{
    width: 30px; height: 30px; border-radius: 8px;
    background: linear-gradient(135deg, #2563EB, #06B6D4);
    display: inline-flex; align-items: center; justify-content: center;
    color: white; font-size: 1rem;
}}
.login-card-top .sub {{
    color: {PALETTE['muted']};
    font-size: 0.85rem;
    margin-top: 8px;
    line-height: 1.4;
}}
/* the form sits in the same center column as the card-top; restyle it
   so it visually continues the card and so its inputs feel compact */
div[data-testid="stForm"] {{
    background: {PALETTE['surface']};
    border: 1px solid {PALETTE['border']};
    border-top: none;
    border-radius: 0 0 16px 16px;
    padding: 4px 22px 18px;
    box-shadow: 0 12px 30px -16px rgba(15, 23, 42, 0.18);
}}
div[data-testid="stForm"] .stTextInput input {{
    font-size: 0.9rem;
    padding: 7px 10px;
}}
div[data-testid="stForm"] .stTextInput label {{
    font-size: 0.78rem;
    color: {PALETTE['muted']};
    margin-bottom: 2px;
}}
.login-hint {{
    margin-top: 14px;
    padding: 10px 14px;
    background: #F1F5F9;
    border: 1px solid {PALETTE['border']};
    border-radius: 10px;
    font-size: 0.82rem;
    color: #475569;
}}

/* ---------- streamlit widget tweaks ---------- */
button[kind="primary"] {{
    background: {PALETTE['primary']} !important;
    border: none !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 2px rgba(37, 99, 235, 0.2);
}}
button[kind="primary"]:hover {{
    filter: brightness(0.95);
}}

div[data-testid="stMetricValue"] {{ font-weight: 700; }}

[data-testid="stSidebar"] {{
    background: {PALETTE['surface']};
    border-right: 1px solid {PALETTE['border']};
}}
[data-testid="stSidebar"] .stMarkdown h2 {{
    font-size: 0.95rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {PALETTE['muted']};
    font-weight: 600;
    border-bottom: 1px solid {PALETTE['border']};
    padding-bottom: 6px;
    margin-bottom: 10px;
}}

div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {{
    border: 1px solid {PALETTE['border']};
    border-radius: 10px;
    overflow: hidden;
}}

/* tabs */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background: transparent;
    border-bottom: 1px solid {PALETTE['border']};
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    border-radius: 8px 8px 0 0;
    padding: 10px 16px;
    font-weight: 500;
}}
.stTabs [aria-selected="true"] {{
    background: {PALETTE['surface']};
    color: {PALETTE['primary']};
    border-bottom: 2px solid {PALETTE['primary']};
}}


/* ===== Performance summary panel ===== */
.perf-panel {{ display:flex; flex-direction:column; gap:14px; margin:8px 0 18px; }}
.perf-hero {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
.perf-hero-card {{
    background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px;
    padding:18px 20px; box-shadow:0 1px 2px rgba(15,23,42,0.04);
}}
.perf-hero-card .hero-label {{
    font-size:0.72rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#64748B; font-weight:600; margin-bottom:6px;
}}
.perf-hero-card .hero-value {{ font-size:2rem; font-weight:700; color:#0F172A; line-height:1.1; }}
.perf-hero-card .hero-sub {{ font-size:0.78rem; color:#64748B; margin-top:6px; }}
.perf-hero-card.primary {{
    background: linear-gradient(135deg,#2563EB 0%,#06B6D4 100%); border:none;
}}
.perf-hero-card.primary .hero-label,
.perf-hero-card.primary .hero-sub {{ color:rgba(255,255,255,0.88); }}
.perf-hero-card.primary .hero-value {{ color:#FFFFFF; }}

.perf-grid {{ display:grid; grid-template-columns:1fr 1.2fr; gap:14px; }}

.cm-card,.rates-card,.model-card {{
    background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px;
    padding:16px 18px; box-shadow:0 1px 2px rgba(15,23,42,0.04);
}}
.cm-card-title,.rates-title {{
    font-size:0.85rem; font-weight:600; color:#0F172A; margin-bottom:12px;
}}
.cm-grid {{ display:grid; grid-template-columns:110px 1fr 1fr; gap:6px; }}
.cm-corner {{
    font-size:0.68rem; color:#94A3B8; text-align:right;
    align-self:center; padding-right:6px;
}}
.cm-col-head {{ font-size:0.74rem; color:#64748B; font-weight:600; text-align:center; align-self:center; }}
.cm-row-head {{ font-size:0.78rem; color:#0F172A; font-weight:600; text-align:right; padding-right:6px; align-self:center; }}
.cm-cell {{ border-radius:10px; padding:14px 12px; text-align:center; }}
.cm-cell .cm-tag {{
    font-size:0.68rem; font-weight:700; text-transform:uppercase;
    letter-spacing:0.08em; margin-bottom:4px;
}}
.cm-cell .cm-num {{ font-size:1.75rem; font-weight:700; }}
.cm-cell.tp,.cm-cell.tn {{ background:#DCFCE7; color:#166534; }}
.cm-cell.fp,.cm-cell.fn {{ background:#FEE2E2; color:#991B1B; }}

.rate {{ margin:8px 0 14px; }}
.rate-row-top {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px; }}
.rate-label {{ font-size:0.85rem; font-weight:600; color:#0F172A; }}
.rate-value {{ font-size:0.95rem; font-weight:700; color:#0F172A; }}
.rate-bar {{ background:#F1F5F9; border-radius:999px; height:7px; overflow:hidden; }}
.rate-fill {{ height:100%; border-radius:999px; }}
.rate-formula {{ font-size:0.7rem; color:#94A3B8; margin-top:3px; }}

.model-card {{
    display:grid; grid-template-columns:1.5fr 1fr 1.2fr;
    gap:0; align-items:stretch;
}}
.model-col {{ padding:6px 18px; border-right:1px solid #E2E8F0; }}
.model-col:first-child {{ padding-left:6px; }}
.model-col:last-child  {{ border-right:none; padding-right:6px; }}
.model-eyebrow {{
    font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#94A3B8; font-weight:600; margin-bottom:8px;
}}
.model-title {{ font-size:1rem; font-weight:700; color:#0F172A; margin-bottom:8px; }}
.model-equation {{
    font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size:0.92rem; color:#2563EB;
    background:#EFF6FF; border:1px solid #BFDBFE;
    padding:7px 12px; border-radius:8px;
    display:inline-block;
}}
.model-equation .var {{ color:#0F172A; font-weight:700; }}
.model-coeff-chips {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }}
.model-coeff-chips .chip {{
    background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px;
    padding:3px 8px; font-size:0.78rem; display:inline-flex; gap:5px;
}}
.model-coeff-chips .chip span {{ color:#64748B; }}
.model-coeff-chips .chip b {{ color:#0F172A; font-weight:700; }}

.model-r2-big {{ font-size:2.2rem; font-weight:700; color:#0F172A; line-height:1; }}
.model-r2-bar {{
    margin-top:10px; background:#F1F5F9; border-radius:999px;
    height:6px; overflow:hidden;
}}
.model-r2-fill {{ height:100%; border-radius:999px; }}
.model-r2-rating {{ font-size:0.78rem; color:#64748B; margin-top:6px; }}

.model-stats {{
    display:grid; grid-template-columns:repeat(3,1fr); gap:8px;
}}
.model-stats .stat {{
    background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px;
    padding:8px 10px; text-align:center;
}}
.model-stats .stat-label {{
    font-size:0.66rem; text-transform:uppercase; letter-spacing:0.05em;
    color:#94A3B8; font-weight:600;
}}
.model-stats .stat-val {{ font-size:1.05rem; font-weight:700; color:#0F172A; margin-top:2px; }}
@media (max-width:1100px) {{
    .model-card {{ grid-template-columns:1fr; }}
    .model-col {{ border-right:none; border-bottom:1px solid #E2E8F0; padding:10px 6px; }}
    .model-col:last-child {{ border-bottom:none; }}
}}

@media (max-width:1100px) {{
    .perf-grid,.perf-hero {{ grid-template-columns:1fr; }}
}}

/* ===== Analysis panel (Passing-Bablok + Bland-Altman side by side) ===== */
.analysis-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:8px 0 18px; }}
.analysis-card {{
    background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px;
    padding:18px 20px; box-shadow:0 1px 2px rgba(15,23,42,0.04);
    display:flex; flex-direction:column; gap:14px;
}}
.analysis-header {{ border-bottom:1px solid #F1F5F9; padding-bottom:10px; }}
.analysis-title {{ font-size:0.95rem; font-weight:700; color:#0F172A; }}
.analysis-sub   {{ font-size:0.78rem; color:#64748B; margin-top:2px; }}
.pb-equation {{
    align-self:flex-start;
    font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size:0.95rem; color:#2563EB;
    background:#EFF6FF; border:1px solid #BFDBFE;
    padding:7px 12px; border-radius:8px;
}}
.pb-equation .var {{ color:#0F172A; font-weight:700; }}
.analysis-row {{
    display:grid; grid-template-columns:1fr 1fr; gap:12px;
}}
.analysis-stat {{
    background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px;
    padding:12px 14px;
}}
.analysis-stat .stat-eyebrow {{
    font-size:0.66rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#94A3B8; font-weight:600; margin-bottom:4px;
}}
.analysis-stat .stat-big {{
    font-size:1.4rem; font-weight:700; color:#0F172A; line-height:1.1;
}}
.analysis-stat .stat-sub {{
    font-size:0.74rem; color:#64748B; margin-top:4px;
}}
.analysis-stat.upper {{ border-top:3px solid #DC2626; }}
.analysis-stat.lower {{ border-top:3px solid #DC2626; }}
.ba-bias {{
    background:linear-gradient(135deg,#ECFDF5 0%,#FFFFFF 100%);
    border:1px solid #A7F3D0; border-radius:12px;
    padding:14px 16px; text-align:center;
}}
.ba-bias .stat-eyebrow {{
    font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#059669; font-weight:600; margin-bottom:4px;
}}
.ba-bias .stat-big-hero {{
    font-size:2rem; font-weight:700; color:#065F46; line-height:1;
}}
.ba-bias .stat-sub {{ font-size:0.74rem; color:#047857; margin-top:6px; }}
@media (max-width:1100px) {{ .analysis-grid {{ grid-template-columns:1fr; }} }}

/* PB hypothesis-test block */
.pb-hypo {{
    background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px;
    padding:12px 14px; display:flex; flex-direction:column; gap:8px;
}}
.pb-hypo .hypo-title {{
    font-size:0.7rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#94A3B8; font-weight:600;
}}
.pb-hypo .hypo-conclusion {{
    font-size:0.92rem; font-weight:700;
    padding:6px 10px; border-radius:8px; display:inline-block;
}}
.pb-hypo .hypo-conclusion.pass {{ background:#DCFCE7; color:#166534; }}
.pb-hypo .hypo-conclusion.fail {{ background:#FEE2E2; color:#991B1B; }}
.pb-hypo .hypo-conclusion.warn {{ background:#FEF3C7; color:#92400E; }}
.pb-hypo .hypo-row {{
    display:flex; justify-content:space-between; align-items:center;
    font-size:0.82rem;
}}
.pb-hypo .hypo-row .name {{ color:#475569; }}
.pb-hypo .hypo-row .verdict {{ font-weight:700; }}
.pb-hypo .hypo-row .verdict.pass {{ color:#166534; }}
.pb-hypo .hypo-row .verdict.fail {{ color:#991B1B; }}

/* ===== Configurations tab ===== */
.cfg-empty {{
    padding:14px 18px; background:#F8FAFC; border:1px dashed #CBD5E1;
    border-radius:12px; color:#64748B; font-size:0.88rem;
}}
.cfg-grid {{
    display:grid; grid-template-columns:repeat(auto-fit, minmax(280px,1fr));
    gap:12px; margin:8px 0 18px;
}}
.cfg-card {{
    background:#FFFFFF; border:1px solid #E2E8F0; border-radius:12px;
    padding:14px 16px; box-shadow:0 1px 2px rgba(15,23,42,0.04);
    border-left:3px solid #2563EB;
}}
.cfg-card-name {{
    font-size:1rem; font-weight:700; color:#0F172A; margin-bottom:8px;
    padding-bottom:6px; border-bottom:1px solid #F1F5F9;
}}
.cfg-row {{
    display:flex; justify-content:space-between; gap:10px;
    font-size:0.82rem; padding:3px 0;
}}
.cfg-row .cfg-label {{
    color:#64748B; font-weight:500; min-width:80px;
}}
.cfg-row .cfg-val {{ color:#0F172A; text-align:right; }}
.cfg-row .cfg-val .muted {{ color:#94A3B8; }}
.cfg-row .cfg-val b {{ color:#2563EB; font-weight:700; }}

.cfg-preview {{
    border:1px solid #E2E8F0; border-radius:10px; padding:10px 14px;
    background:#F8FAFC; display:flex; flex-direction:column; gap:2px;
}}
.cfg-preview .lbl {{
    font-size:0.66rem; text-transform:uppercase; letter-spacing:0.06em;
    color:#94A3B8; font-weight:600;
}}
.cfg-preview .big {{
    font-size:1.1rem; font-weight:700;
    font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
}}
.cfg-preview .sub {{ font-size:0.78rem; color:#64748B; }}
.cfg-preview.ok  {{ border-color:#A7F3D0; background:#ECFDF5; }}
.cfg-preview.ok  .lbl {{ color:#059669; }}
.cfg-preview.ok  .big {{ color:#065F46; }}
.cfg-preview.ok  .sub {{ color:#047857; }}
.cfg-preview.warn {{ border-color:#FCD34D; background:#FFFBEB; }}
.cfg-preview.warn .lbl {{ color:#B45309; }}
.cfg-preview.warn .sub {{ color:#92400E; }}

/* ===== Configurations cards (v2 — used inside st.container) ===== */
.cfg-card-name {{
    font-size:1rem; font-weight:700; color:#0F172A;
    padding-bottom:8px; margin-bottom:8px;
    border-bottom:1px solid #F1F5F9;
}}
.cfg-card-body {{
    display:flex; flex-direction:column; gap:5px;
    font-size:0.82rem; margin-bottom:8px;
}}
.cfg-card-body > div {{
    display:flex; justify-content:space-between; gap:10px; align-items:baseline;
}}
.cfg-card-body .lbl {{ color:#64748B; font-weight:500; min-width:90px; }}
.cfg-card-body .val {{ color:#0F172A; text-align:right; font-weight:500; }}

/* row tint legend above the data table */
.row-legend {{
    display:flex; flex-wrap:wrap; gap:18px; align-items:center;
    margin:6px 0 10px; font-size:0.78rem; color:#475569;
}}
.row-legend .swatch {{
    display:inline-block; width:14px; height:14px; border-radius:4px;
    margin-right:6px; vertical-align:middle; border:1px solid #E2E8F0;
}}
.row-legend .swatch.green {{ background:#DCFCE7; border-color:#A7F3D0; }}
.row-legend .swatch.amber {{ background:#FEF3C7; border-color:#FCD34D; }}
.row-legend .swatch.red   {{ background:#FEE2E2; border-color:#FECACA; }}
.row-legend .swatch.none  {{ background:#FFFFFF; }}

/* User-management cards */
.usr-card-head {{
    display:flex; justify-content:space-between; align-items:center;
    padding-bottom:8px; margin-bottom:8px;
    border-bottom:1px solid #F1F5F9;
}}
.usr-card-name {{ font-size:1rem; font-weight:700; color:#0F172A; }}
.usr-card-pills {{ display:flex; gap:6px; }}
.usr-pill {{
    display:inline-block; padding:2px 10px; border-radius:999px;
    font-size:0.7rem; font-weight:600; text-transform:uppercase;
    letter-spacing:0.04em;
}}
.usr-pill.primary {{ background:#EFF6FF; color:#2563EB; border:1px solid #BFDBFE; }}
.usr-pill.muted   {{ background:#F1F5F9; color:#475569; border:1px solid #E2E8F0; }}
.usr-card-body {{
    display:flex; flex-direction:column; gap:5px;
    font-size:0.82rem; margin-bottom:8px;
}}
.usr-row {{
    display:flex; justify-content:space-between; gap:10px; align-items:baseline;
}}
.usr-row .lbl {{ color:#64748B; font-weight:500; min-width:80px; }}
.usr-row .val {{ color:#0F172A; text-align:right; font-weight:500; }}
.usr-row .val.mono {{
    font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size:0.9rem;
}}
</style>
"""


# ---------------------------------------------------------------------------
# Plotly template
# ---------------------------------------------------------------------------
def _register_template() -> None:
    grid = "#E2E8F0"
    text = PALETTE["text"]
    muted = PALETTE["muted"]
    tpl = go.layout.Template(
        layout=go.Layout(
            font=dict(family="Inter, sans-serif", size=12, color=text),
            paper_bgcolor=PALETTE["surface"],
            plot_bgcolor=PALETTE["surface"],
            colorway=PALETTE["chart"],
            title=dict(font=dict(size=15, color=text), x=0.0, xanchor="left",
                       y=0.98, yanchor="top", yref="container",
                       pad=dict(l=2, b=4, t=4)),
            xaxis=dict(
                gridcolor=grid, zerolinecolor=grid,
                linecolor=grid, ticks="outside",
                tickfont=dict(color=muted, size=11),
                title=dict(font=dict(color=text, size=12)),
            ),
            yaxis=dict(
                gridcolor=grid, zerolinecolor=grid,
                linecolor=grid, ticks="outside",
                tickfont=dict(color=muted, size=11),
                title=dict(font=dict(color=text, size=12)),
            ),
            margin=dict(l=20, r=20, t=100, b=40),
            legend=dict(
                bgcolor="rgba(255,255,255,0.6)",
                bordercolor=grid, borderwidth=1,
                font=dict(color=text, size=11),
                orientation="h",
                x=0, xanchor="left",
                # sits in the top margin, well clear of the title
                y=1.06, yanchor="bottom",
            ),
            hoverlabel=dict(
                bgcolor=PALETTE["surface"], bordercolor=grid,
                font=dict(family="Inter, sans-serif", color=text),
            ),
        )
    )
    pio.templates["modern"] = tpl
    pio.templates.default = "modern"


def plotly_layout(**overrides) -> dict:
    """Return overrides for `fig.update_layout(**plotly_layout(...))`.
    Top margin reserves space for the title (top) and legend (just under it)."""
    base = {"height": 460, "margin": dict(l=20, r=20, t=100, b=40)}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def apply() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    _register_template()


def section(title: str, hint: str | None = None) -> None:
    hint_html = f'<span class="hint">{hint}</span>' if hint else ""
    st.markdown(
        f'<div class="section"><span class="bar"></span>'
        f'<span class="title">{title}</span>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def kpi_grid(cards: list[dict]) -> None:
    """
    cards: list of {"label": str, "value": str, "sub": str (optional),
                    "tone": "accent"|"success"|"warning"|"danger"|None}
    """
    parts = ['<div class="kpi-grid">']
    for c in cards:
        tone = c.get("tone") or "accent"
        sub = f'<div class="sub">{c["sub"]}</div>' if c.get("sub") else ""
        parts.append(
            f'<div class="kpi-card {tone}">'
            f'<div class="label">{c["label"]}</div>'
            f'<div class="value">{c["value"]}</div>'
            f'{sub}</div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def pill(text: str, tone: str = "muted") -> str:
    return f'<span class="pill {tone}">{text}</span>'


def header_band(brand: str, items: list[tuple[str, str]]) -> None:
    """items = [(label, value), ...]"""
    metas = " ".join(f"<div><b>{lbl}:</b> {val}</div>" for lbl, val in items)
    st.markdown(
        f'<div class="app-header">'
        f'<div class="brand"><span class="dot"></span>{brand}</div>'
        f'<div class="meta">{metas}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _fmt_v(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f:.4g}"


def _fmt_pct_v(v) -> str:
    s = _fmt_v(v)
    return s if s == "—" else f"{s} %"


def _equation_html(name: str, coeffs: dict) -> str:
    """Render the fitted equation as inline HTML for the model card."""
    nm = (name or "").lower()
    if "no intercept" in nm:
        s = coeffs.get("slope", 0)
        return f'<span class="var">y</span> = {s:.4g} · <span class="var">x</span>'
    if "linear" in nm:
        s = coeffs.get("slope", 0)
        b = coeffs.get("intercept", 0)
        sign = "+" if b >= 0 else "−"
        return (f'<span class="var">y</span> = {s:.4g} · <span class="var">x</span> '
                f'{sign} {abs(b):.4g}')
    if "4pl" in nm:
        return ('<span class="var">Conc</span> = C · '
                '((A − D) / (<span class="var">Abs</span> − D) − 1)'
                '<sup>1/B</sup>')
    if "5pl" in nm:
        return ('<span class="var">Conc</span> = C · '
                '(((A − D) / (<span class="var">Abs</span> − D))'
                '<sup>1/E</sup> − 1)<sup>1/B</sup>')
    return ""


def _rate_color(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "#CBD5E1"
    if f != f:
        return "#CBD5E1"
    if f >= 90:
        return "#10B981"  # green
    if f >= 75:
        return "#F59E0B"  # amber
    return "#EF4444"      # red


def performance_panel(counts: dict, diag: dict, fit: dict) -> None:
    """Render the unified Performance summary block.

    counts: dict from metrics.confusion_counts(...)
    diag:   dict from metrics.diagnostic_metrics(...)
    fit:    dict from models.fit_model(...)
    """
    n_eval = int(counts.get("N evaluated") or 0)
    n_correct = int(counts.get("TP", 0)) + int(counts.get("TN", 0))
    n_samples = int(counts.get("N samples", 0))
    accuracy  = diag.get("Diagnostic Accuracy %")
    pct_clia  = counts.get("% within CLIA")
    avg_err   = counts.get("Avg |Error%|")

    # ---- HERO ----
    hero = f"""
    <div class="perf-hero">
      <div class="perf-hero-card">
        <div class="hero-label">Total samples</div>
        <div class="hero-value">{n_samples}</div>
        <div class="hero-sub">{n_eval} evaluated against normal range</div>
      </div>
      <div class="perf-hero-card primary">
        <div class="hero-label">Diagnostic accuracy</div>
        <div class="hero-value">{_fmt_pct_v(accuracy)}</div>
        <div class="hero-sub">{n_correct} of {n_eval} classified correctly</div>
      </div>
      <div class="perf-hero-card">
        <div class="hero-label">% within CLIA window</div>
        <div class="hero-value">{_fmt_pct_v(pct_clia)}</div>
        <div class="hero-sub">Predicted vs Actual</div>
      </div>
      <div class="perf-hero-card">
        <div class="hero-label">Avg |Error %|</div>
        <div class="hero-value">{_fmt_v(avg_err)}</div>
        <div class="hero-sub">Across all evaluated rows</div>
      </div>
    </div>
    """

    # ---- CONFUSION MATRIX 2x2 ----
    cm = f"""
    <div class="cm-card">
      <div class="cm-card-title">Confusion matrix · positive = abnormal (outside normal range)</div>
      <div class="cm-grid">
        <div class="cm-corner">Actual&nbsp;↓&nbsp;/&nbsp;Predicted&nbsp;→</div>
        <div class="cm-col-head">Abnormal</div>
        <div class="cm-col-head">Normal</div>
        <div class="cm-row-head">Abnormal</div>
        <div class="cm-cell tp"><div class="cm-tag">TP</div><div class="cm-num">{counts.get('TP',0)}</div></div>
        <div class="cm-cell fn"><div class="cm-tag">FN</div><div class="cm-num">{counts.get('FN',0)}</div></div>
        <div class="cm-row-head">Normal</div>
        <div class="cm-cell fp"><div class="cm-tag">FP</div><div class="cm-num">{counts.get('FP',0)}</div></div>
        <div class="cm-cell tn"><div class="cm-tag">TN</div><div class="cm-num">{counts.get('TN',0)}</div></div>
      </div>
    </div>
    """

    # ---- DIAGNOSTIC RATES ----
    rates_data = [
        ("Sensitivity",     diag.get("Sensitivity %"),         "TP / (TP + FN)"),
        ("Specificity",     diag.get("Specificity %"),         "TN / (TN + FP)"),
        ("PPV (Precision)", diag.get("PPV %"),                 "TP / (TP + FP)"),
        ("NPV",             diag.get("NPV %"),                 "TN / (TN + FN)"),
    ]
    rate_rows = ""
    for label, val, formula in rates_data:
        try:
            f = float(val)
        except (TypeError, ValueError):
            f = 0.0
        if f != f:
            f = 0.0
        f_clamped = max(0.0, min(100.0, f))
        col = _rate_color(val)
        rate_rows += (
            f'<div class="rate">'
            f'<div class="rate-row-top">'
            f'<div class="rate-label">{label}</div>'
            f'<div class="rate-value">{_fmt_pct_v(val)}</div>'
            f'</div>'
            f'<div class="rate-bar"><div class="rate-fill" style="width:{f_clamped}%; background:{col}"></div></div>'
            f'<div class="rate-formula">{formula}</div>'
            f'</div>'
        )
    rates = f'<div class="rates-card"><div class="rates-title">Diagnostic rates</div>{rate_rows}</div>'

    # ---- MODEL CARD ----
    if fit.get("success"):
        m = fit["metrics"]
        equation = _equation_html(fit.get("name",""), fit.get("coeffs",{}))
        # For non-linear models, also show coefficient chips
        coef_chips = ""
        abs_range_note = ""
        name_l = (fit.get("name") or "").lower()
        if "pl" in name_l:  # 4PL / 5PL
            coef_chips = '<div class="model-coeff-chips">' + "".join(
                f'<div class="chip"><span>{k.split(" ")[0]}</span><b>{_fmt_v(v)}</b></div>'
                for k, v in fit["coeffs"].items()
            ) + '</div>'
            ar = fit.get("abs_range")
            if ar:
                abs_range_note = (
                    f'<div style="font-size:0.78rem;color:#64748B;margin-top:4px">'
                    f'Valid absorbance range: {ar[0]:.4g} – {ar[1]:.4g}</div>'
                )
        try:
            r2_val = float(m["R2"])
            r2_pct = max(0.0, min(100.0, r2_val * 100.0))
            r2_color = ("#10B981" if r2_val >= 0.95
                        else "#22C55E" if r2_val >= 0.85
                        else "#F59E0B" if r2_val >= 0.70
                        else "#EF4444")
            r2_rating = ("Excellent fit" if r2_val >= 0.95
                         else "Good fit" if r2_val >= 0.85
                         else "Fair fit" if r2_val >= 0.70
                         else "Poor fit")
            r2_str = f"{r2_val:.4f}"
        except (TypeError, ValueError):
            r2_pct = 0; r2_color = "#CBD5E1"; r2_rating = "n/a"; r2_str = "—"

        model = f"""
        <div class="model-card">
          <div class="model-col">
            <div class="model-eyebrow">Calibration model</div>
            <div class="model-title">{fit['name']}</div>
            <div class="model-equation">{equation}</div>
            {coef_chips}
            {abs_range_note}
          </div>
          <div class="model-col">
            <div class="model-eyebrow">R² · variance explained</div>
            <div class="model-r2-big">{r2_str}</div>
            <div class="model-r2-bar">
              <div class="model-r2-fill" style="width:{r2_pct}%;background:{r2_color}"></div>
            </div>
            <div class="model-r2-rating">{r2_rating}</div>
          </div>
          <div class="model-col">
            <div class="model-eyebrow">Goodness of fit</div>
            <div class="model-stats">
              <div class="stat"><div class="stat-label">RMSE</div><div class="stat-val">{_fmt_v(m["RMSE"])}</div></div>
              <div class="stat"><div class="stat-label">MAE</div><div class="stat-val">{_fmt_v(m["MAE"])}</div></div>
              <div class="stat"><div class="stat-label">N</div><div class="stat-val">{m["N"]}</div></div>
            </div>
          </div>
        </div>
        """
    else:
        model = f"""
        <div class="model-card">
          <div class="model-col">
            <div class="model-eyebrow">Calibration model</div>
            <div class="model-title">{fit.get('name','Model')}</div>
            <div class="model-equation" style="background:#FEE2E2;border-color:#FECACA;color:#B91C1C">Could not fit</div>
          </div>
          <div class="model-col" style="grid-column: span 2;">
            <div class="model-eyebrow">Reason</div>
            <div style="font-size:0.88rem;color:#475569;margin-top:4px">{fit.get('message','')}</div>
          </div>
        </div>
        """

    full = (
        '<div class="perf-panel">'
        + hero
        + '<div class="perf-grid">' + cm + rates + '</div>'
        + model
        + '</div>'
    )
    # Streamlit's markdown engine has two traps for this pattern:
    #   1) lines starting with 4+ spaces become fenced code blocks
    #   2) blank lines close any open HTML block early
    # Collapse all inter-tag whitespace so it stays one HTML chunk.
    import re as _re
    full = _re.sub(r'>\s+<', '><', full).strip()
    st.markdown(full, unsafe_allow_html=True)


def _fmt_signed(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    if abs(f) < 1e-9:
        return "0"
    sign = "+" if f >= 0 else "−"
    return f"{sign}{abs(f):.4g}"


def _pb_equation_html(slope, intercept) -> str:
    try:
        s = float(slope); b = float(intercept)
    except (TypeError, ValueError):
        return ""
    if s != s or b != b:
        return ""
    sign = "+" if b >= 0 else "−"
    return (f'<span class="var">y</span> = {s:.4g} · <span class="var">x</span> '
            f'{sign} {abs(b):.4g}')


def analysis_panel(pb: dict, ba_bias, ba_sd, ba_n: int) -> None:
    """Render the Passing-Bablok + Bland-Altman summary as two grouped cards.

    pb: dict from models.passing_bablok(...)
    ba_bias / ba_sd / ba_n: precomputed Bland-Altman stats
    """
    slope = pb.get("slope")
    intercept = pb.get("intercept")
    s_lo, s_hi = pb.get("slope_ci", (None, None))
    i_lo, i_hi = pb.get("intercept_ci", (None, None))
    n_pairs = int(pb.get("n", 0) or 0)

    eq = _pb_equation_html(slope, intercept)
    eq_html = f'<div class="pb-equation">{eq}</div>' if eq else ""

    # Hypothesis tests:
    #   Slope = 1     ↔ no proportional bias  (1 must lie in slope 95 % CI)
    #   Intercept = 0 ↔ no constant bias      (0 must lie in intercept 95 % CI)
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
        conclusion_class = "warn"
        conclusion_text = "Insufficient data for hypothesis test"
    elif slope_ok and interc_ok:
        conclusion_class = "pass"
        conclusion_text = "✓ Methods agree — no significant bias"
    else:
        conclusion_class = "fail"
        parts = []
        if not slope_ok:  parts.append("proportional bias")
        if not interc_ok: parts.append("constant bias")
        conclusion_text = "✗ Disagreement — " + " and ".join(parts) + " detected"

    def _v(ok):
        if ok is None: return ('warn', '—', 'n/a')
        if ok: return ('pass', '✓', 'CI contains the null value')
        return ('fail', '✗', 'CI excludes the null value')
    s_cls, s_mark, s_msg = _v(slope_ok)
    i_cls, i_mark, i_msg = _v(interc_ok)

    hypo_html = (
        '<div class="pb-hypo">'
        '<div class="hypo-title">Hypothesis agreement (95 % CI)</div>'
        f'<div class="hypo-conclusion {conclusion_class}">{conclusion_text}</div>'
        f'<div class="hypo-row"><span class="name">H₀: slope = 1 · {s_msg}</span>'
        f'<span class="verdict {s_cls}">{s_mark}</span></div>'
        f'<div class="hypo-row"><span class="name">H₀: intercept = 0 · {i_msg}</span>'
        f'<span class="verdict {i_cls}">{i_mark}</span></div>'
        '</div>'
    )

    pb_card = (
        '<div class="analysis-card">'
        '<div class="analysis-header">'
        '<div class="analysis-title">Passing-Bablok regression</div>'
        f'<div class="analysis-sub">Method comparison · n = {n_pairs} pairs</div>'
        '</div>'
        + eq_html +
        '<div class="analysis-row">'
        '<div class="analysis-stat">'
        '<div class="stat-eyebrow">Slope</div>'
        f'<div class="stat-big">{_fmt_v(slope)}</div>'
        f'<div class="stat-sub">95 % CI {_fmt_v(s_lo)} – {_fmt_v(s_hi)}</div>'
        '</div>'
        '<div class="analysis-stat">'
        '<div class="stat-eyebrow">Intercept</div>'
        f'<div class="stat-big">{_fmt_v(intercept)}</div>'
        f'<div class="stat-sub">95 % CI {_fmt_v(i_lo)} – {_fmt_v(i_hi)}</div>'
        '</div>'
        '</div>'
        + hypo_html +
        '</div>'
    )

    try:
        bias_v = float(ba_bias); sd_v = float(ba_sd)
    except (TypeError, ValueError):
        bias_v = 0.0; sd_v = 0.0
    if bias_v != bias_v: bias_v = 0.0
    if sd_v != sd_v: sd_v = 0.0
    loa_hi = bias_v + 1.96 * sd_v
    loa_lo = bias_v - 1.96 * sd_v
    span = loa_hi - loa_lo

    ba_card = (
        '<div class="analysis-card">'
        '<div class="analysis-header">'
        '<div class="analysis-title">Bland-Altman analysis</div>'
        f'<div class="analysis-sub">Predicted − Actual · n = {ba_n} pairs · LoA span = {_fmt_v(span)}</div>'
        '</div>'
        '<div class="ba-bias">'
        '<div class="stat-eyebrow">Mean bias (Predicted − Actual)</div>'
        f'<div class="stat-big-hero">{_fmt_signed(bias_v)}</div>'
        f'<div class="stat-sub">SD = {_fmt_v(sd_v)}</div>'
        '</div>'
        '<div class="analysis-row">'
        '<div class="analysis-stat upper">'
        '<div class="stat-eyebrow">+1.96 SD</div>'
        f'<div class="stat-big">{_fmt_signed(loa_hi)}</div>'
        '<div class="stat-sub">Upper limit of agreement</div>'
        '</div>'
        '<div class="analysis-stat lower">'
        '<div class="stat-eyebrow">−1.96 SD</div>'
        f'<div class="stat-big">{_fmt_signed(loa_lo)}</div>'
        '<div class="stat-sub">Lower limit of agreement</div>'
        '</div>'
        '</div>'
        '</div>'
    )

    full = '<div class="analysis-grid">' + pb_card + ba_card + '</div>'
    import re as _re
    full = _re.sub(r'>\s+<', '><', full).strip()
    st.markdown(full, unsafe_allow_html=True)
