"""
Streamlit dashboard — Zinnia Axion (2-state model).

Single-viewport enterprise layout:
  Row 1 — Metric cards (productive, non-productive, total)
  Row 2 — State % bar (left) + Daily trend (right)
  Row 3 — App-wise breakdown (full width)

Run with:
    streamlit run frontend/dashboard.py
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")

# ── Page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="Zinnia Axion Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Enterprise light-mode styling ────────────────────────────────────
st.markdown(
    """
    <style>
    :root {
        --ud-canvas: #f7f9fc;
        --ud-surface: #ffffff;
        --ud-border: #e2e8f0;
        --ud-text: #0f172a;
        --ud-text-body: #334155;
        --ud-text-muted: #64748b;
        --ud-text-faint: #94a3b8;
        --ud-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02);
        --ud-green: #059669;
        --ud-red: #dc2626;
    }
    [data-testid="stAppViewContainer"] {
        background: var(--ud-canvas) !important;
    }
    .block-container {
        padding: 2rem 3rem 1rem 3rem !important;
        max-width: 100% !important;
    }
    .element-container { margin-bottom: -0.15rem; }
    .stPlotlyChart { margin-top: -0.5rem; margin-bottom: -0.5rem; }

    /* ── Metric cards ── */
    [data-testid="stMetric"] {
        background: var(--ud-surface);
        border: 1px solid var(--ud-border);
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: var(--ud-shadow);
    }
    [data-testid="stMetricValue"] {
        font-size: 1.5rem; font-weight: 700; color: var(--ud-text);
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.72rem; font-weight: 600; color: var(--ud-text-muted);
        text-transform: uppercase; letter-spacing: 0.8px;
    }
    [data-testid="stMetricDelta"] {
        font-size: 0.82rem; font-weight: 600;
    }

    /* ── Headers ── */
    [data-testid="stHeader"] {
        background: transparent !important;
    }
    h1, h2, h3 {
        color: var(--ud-text) !important;
        font-weight: 700 !important;
        letter-spacing: -0.3px;
    }
    .stCaption, [data-testid="stCaptionContainer"] {
        color: var(--ud-text-muted) !important;
        font-size: 0.72rem !important;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-weight: 600 !important;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: var(--ud-surface) !important;
        border-right: 1px solid var(--ud-border) !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.15s !important;
    }

    /* ── Footer text ── */
    .ud-footer {
        font-size: 0.72rem; color: var(--ud-text-faint);
        padding-top: 16px; margin-top: 24px;
        border-top: 1px solid var(--ud-border);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Colour palette ──────────────────────────────────────────────────
STATE_COLORS = {
    "productive": "#059669",        # emerald
    "non_productive": "#dc2626",    # red
}
STATE_LABELS = {
    "productive": "Productive",
    "non_productive": "Non-Productive",
}


# ── API helpers ─────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> dict | list | None:
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        st.error(f"API error ({path}): {exc}")
        return None


def _fmt(seconds: int) -> str:
    """Format seconds → '2h 15m' or '45s'."""
    if seconds < 60:
        return f"{seconds}s"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s and not h:
        parts.append(f"{s}s")
    return " ".join(parts)


# ── Sidebar (collapsed by default — settings only) ──────────────────
with st.sidebar:
    st.markdown("**Settings**")
    trend_days = st.slider("Trend days", 1, 30, 7)
    auto_refresh = st.checkbox("Auto-refresh (30 s)")

# ── Read user_id from URL query params (set by admin dashboard link) ─
_qp = st.query_params
USER_ID = _qp.get("user_id", None)

# Build common params dict for API calls
_api_params: dict[str, str] = {}
if USER_ID:
    _api_params["user_id"] = USER_ID

# ── Fetch all data upfront ──────────────────────────────────────────
summary = _get("/summary/today", _api_params)
apps_data = _get("/apps", _api_params)
daily_data = _get("/daily", {**_api_params, "days": str(trend_days)})

# ── Header ──────────────────────────────────────────────────────────
_title = f"Zinnia Axion — {USER_ID}" if USER_ID else "Zinnia Axion — Today's Overview"
st.header(_title)

if not summary or summary.get("total_seconds", 0) == 0:
    st.info("No data for today yet. Start the Zinnia Axion Agent to begin collecting.")
    st.stop()

total = summary.get("total_seconds", 1)
productive = summary.get("productive", 0)
non_productive = summary.get("non_productive", 0)
prod_pct = round(productive / total * 100, 1) if total else 0
non_prod_pct = round(non_productive / total * 100, 1) if total else 0

# ═══════════════════════════════════════════════════════════════════
#  ROW 1 — Metric cards
# ═══════════════════════════════════════════════════════════════════
m1, m2, m3 = st.columns(3)
m1.metric("Productive", _fmt(productive), f"{prod_pct}%")
m2.metric("Non-Productive", _fmt(non_productive), f"{non_prod_pct}%")
m3.metric("Total Tracked", _fmt(total), f"{summary.get('total_buckets', 0)} buckets")

# ═══════════════════════════════════════════════════════════════════
#  ROW 2 — State % bar (full width)
# ═══════════════════════════════════════════════════════════════════
st.caption("State Distribution")
bar_data = pd.DataFrame([
    {"State": "Productive", "Pct": prod_pct},
    {"State": "Non-Productive", "Pct": non_prod_pct},
])
fig_pct = px.bar(
    bar_data, x="Pct", y="State", orientation="h",
    color="State", text="Pct",
    color_discrete_map={v: STATE_COLORS[k] for k, v in STATE_LABELS.items()},
)
fig_pct.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
fig_pct.update_layout(
    xaxis=dict(range=[0, 110], title="", showticklabels=False),
    yaxis_title="", showlegend=False,
    margin=dict(t=5, b=5, l=5, r=30), height=140,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="system-ui, -apple-system, sans-serif", color="#64748b", size=12),
)
st.plotly_chart(fig_pct, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════
#  ROW 3 — Daily line chart (full width)
# ═══════════════════════════════════════════════════════════════════
st.caption(f"Productive vs Non-Productive — last {trend_days} days")

if daily_data and isinstance(daily_data, list) and len(daily_data) > 0:
    line_rows = []
    for day in daily_data:
        d = day.get("date", "")
        for state_key, label in STATE_LABELS.items():
            secs = day.get(state_key, 0)
            line_rows.append({"Date": d, "State": label, "Minutes": round(secs / 60, 1)})
    df_line = pd.DataFrame(line_rows)
    fig_line = px.line(
        df_line, x="Date", y="Minutes", color="State", markers=True,
        color_discrete_map={v: STATE_COLORS[k] for k, v in STATE_LABELS.items()},
    )
    fig_line.update_layout(
        xaxis_title="", yaxis_title="min",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    title_text=""),
        margin=dict(t=5, b=5, l=5, r=5), height=250,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, sans-serif", color="#64748b", size=12),
    )
    st.plotly_chart(fig_line, use_container_width=True)
else:
    st.info("No daily data yet.")

# ═══════════════════════════════════════════════════════════════════
#  ROW 4 — App-wise breakdown (full width, compact)
# ═══════════════════════════════════════════════════════════════════
st.caption("App-wise Breakdown")

if apps_data and isinstance(apps_data, list) and len(apps_data) > 0:
    rows = []
    for app in apps_data:
        name = app["app_name"]
        for state_key, secs in app.get("states", {}).items():
            if secs > 0:
                rows.append({
                    "App": name,
                    "State": STATE_LABELS.get(state_key, state_key),
                    "Seconds": secs,
                    "Duration": _fmt(secs),
                })
    if rows:
        df_apps = pd.DataFrame(rows)
        n_apps = df_apps["App"].nunique()
        fig_apps = px.bar(
            df_apps, y="App", x="Seconds", color="State",
            orientation="h", barmode="stack",
            color_discrete_map={v: STATE_COLORS[k] for k, v in STATE_LABELS.items()},
            custom_data=["Duration", "State"],
        )
        fig_apps.update_traces(
            hovertemplate=(
                "<b>%{y}</b><br>"
                "%{customdata[1]}: %{customdata[0]}<br>"
                "<extra></extra>"
            )
        )
        fig_apps.update_layout(
            yaxis={"categoryorder": "total ascending", "title": "", "automargin": True},
            xaxis_title="seconds",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        title_text=""),
            margin=dict(t=5, b=5, l=10, r=5),
            height=max(180, n_apps * 28 + 60),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="system-ui, -apple-system, sans-serif", color="#64748b", size=12),
        )
        st.plotly_chart(fig_apps, use_container_width=True)
    else:
        st.info("No app data yet.")
else:
    st.info("No app data yet.")

# ── Footer ──────────────────────────────────────────────────────────
st.markdown(
    '<p class="ud-footer">Only interaction counts are recorded — no keystroke content is ever captured.</p>',
    unsafe_allow_html=True,
)

# ── Auto-refresh ────────────────────────────────────────────────────
if auto_refresh:
    import time as _time
    _time.sleep(30)
    st.rerun()
