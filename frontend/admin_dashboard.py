"""
Streamlit Admin Dashboard — Central Productivity Leaderboard.

Two-page layout driven by URL query parameters:
  Page 1 (default)  — Leaderboard table with "View" links per user
  Page 2 (?user_id) — User detail: non-productive app breakdown + 7-day trend

Run with:
    streamlit run frontend/admin_dashboard.py --server.port 8502 --server.headless true
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
    page_title="Admin — Productivity Dashboard",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Compact enterprise CSS ──────────────────────────────────────────
st.markdown(
    """
    <style>
    .block-container { padding-top: 2.5rem; padding-bottom: 0.5rem; }
    [data-testid="stMetric"] { padding: 0.4rem 0; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; }
    .element-container { margin-bottom: -0.15rem; }
    .stPlotlyChart { margin-top: -0.5rem; margin-bottom: -0.5rem; }
    .row-widget.stDataFrame { font-size: 0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

STATE_COLORS = {
    "productive": "#22c55e",
    "non_productive": "#ef4444",
}
STATE_LABELS = {
    "productive": "Productive",
    "non_productive": "Non-Productive",
}


# ── API helpers ─────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None):
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        st.error(f"API error ({path}): {exc}")
        return None


def _fmt(seconds: float) -> str:
    """Format seconds as '2h 15m' or '45s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if sec and not h:
        parts.append(f"{sec}s")
    return " ".join(parts)


# ── Sidebar ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("**Admin Settings**")
    auto_refresh = st.checkbox("Auto-refresh (10 s)", value=True)

# ── Route: read query params ────────────────────────────────────────
_qp = st.query_params
selected_user_id = _qp.get("user_id", None)


# =====================================================================
#  PAGE 2 — User Detail View  (when ?user_id= is present)
# =====================================================================
if selected_user_id:
    # ── Back link (clean navigation, no stale DOM) ─────────────
    st.markdown(
        '<a href="?" target="_self" style="font-size:1rem;">&larr; Back to Leaderboard</a>',
        unsafe_allow_html=True,
    )

    st.header(f"User Detail — {selected_user_id}")

    # ── Fetch user data ─────────────────────────────────────────
    summary = _get("/summary/today", {"user_id": selected_user_id})
    np_apps = _get(f"/admin/user/{selected_user_id}/non-productive-apps")
    daily_data = _get("/daily", {"days": 7, "user_id": selected_user_id})

    if not summary or summary.get("total_seconds", 0) == 0:
        st.info(f"No data for {selected_user_id} today.")
        st.stop()

    total = summary.get("total_seconds", 1)
    productive = summary.get("productive", 0)
    non_productive = summary.get("non_productive", 0)
    prod_pct = round(productive / total * 100, 1) if total else 0
    non_prod_pct = round(non_productive / total * 100, 1) if total else 0

    # ── ROW 1 — Summary metrics ─────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Non-Productive %", f"{non_prod_pct}%")
    m2.metric("Productive %", f"{prod_pct}%")
    m3.metric("Non-Productive Time", _fmt(non_productive))
    m4.metric("Total Tracked", _fmt(total))

    # ── ROW 2 — Non-productive app breakdown ────────────────────
    st.caption(f"Non-Productive Apps — {selected_user_id} (Today)")

    if np_apps and len(np_apps) > 0:
        df_np = pd.DataFrame(np_apps)
        df_np["Duration"] = df_np["seconds"].apply(_fmt)
        df_np = df_np.sort_values("seconds", ascending=True)

        fig_np = px.bar(
            df_np, y="app_name", x="seconds", orientation="h",
            text="Duration",
            color_discrete_sequence=["#ef4444"],
        )
        fig_np.update_traces(textposition="outside")
        fig_np.update_layout(
            yaxis={"title": "", "automargin": True},
            xaxis_title="seconds",
            margin=dict(t=5, b=5, l=10, r=5),
            height=max(180, len(df_np) * 30 + 60),
            showlegend=False,
        )
        st.plotly_chart(fig_np, use_container_width=True)
    else:
        st.success(f"{selected_user_id} has no non-productive app usage today.")

    # ── ROW 3 — 7-day daily trend line graph ────────────────────
    st.caption(f"7-Day Trend — {selected_user_id}")

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
            margin=dict(t=5, b=5, l=5, r=5), height=280,
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("No daily trend data available.")

    # ── Footer ──────────────────────────────────────────────────
    st.caption("Admin view — data refreshes automatically.")

    if auto_refresh:
        import time as _time
        _time.sleep(10)
        st.rerun()

    st.stop()


# =====================================================================
#  PAGE 1 — Leaderboard View  (default, no ?user_id=)
# =====================================================================
st.header("Admin Dashboard — Productivity Leaderboard")

# ── Fetch leaderboard ───────────────────────────────────────────────
leaderboard = _get("/admin/leaderboard")

if not leaderboard or len(leaderboard) == 0:
    st.info("No user data for today yet. Ensure trackers are running.")
    st.stop()

# ── ROW 1 — Summary metrics ────────────────────────────────────────
total_users = len(leaderboard)
avg_prod = sum(r["productive_pct"] for r in leaderboard) / total_users if total_users else 0
avg_non_prod = sum(r["non_productive_pct"] for r in leaderboard) / total_users if total_users else 0
total_tracked = sum(r["total_sec"] for r in leaderboard)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Users", total_users)
m2.metric("Avg Productive", f"{avg_prod:.1f}%")
m3.metric("Avg Non-Productive", f"{avg_non_prod:.1f}%")
m4.metric("Total Tracked", _fmt(total_tracked))

# ═══════════════════════════════════════════════════════════════════
#  ROW 2 — Leaderboard table (sorted: highest non-productive first)
# ═══════════════════════════════════════════════════════════════════
st.caption("Users ranked by non-productive percentage (highest first)")

def _row_bg(np_pct: float) -> tuple[str, str]:
    """Return (background-color, text-color) based on non-productive %."""
    if np_pct >= 50:
        t = min((np_pct - 50) / 50, 1.0)
        intensity = 0.3 + 0.7 * t
        r = int(255 - 75 * intensity)
        g = int(220 - 180 * intensity)
        b = int(220 - 180 * intensity)
        text = "#991b1b" if intensity < 0.6 else "#ffffff"
        return f"rgb({r},{g},{b})", text

    prod_pct = 100 - np_pct
    if prod_pct >= 70:
        t = min((prod_pct - 70) / 30, 1.0)
        intensity = 0.25 + 0.75 * t
        r = int(220 - 190 * intensity)
        g = int(255 - 115 * intensity)
        b = int(220 - 170 * intensity)
        text = "#166534" if intensity < 0.7 else "#ffffff"
        return f"rgb({r},{g},{b})", text

    return "transparent", "inherit"


# Build HTML table with View links and gradient rows
_header = (
    "<tr>"
    "<th>User</th>"
    "<th>Non-Productive %</th><th>Productive %</th>"
    "<th>Non-Productive Time</th><th>Productive Time</th>"
    "<th>Total Time</th><th></th>"
    "</tr>"
)

_rows_html = ""
for entry in leaderboard:
    uid = entry["user_id"]
    np_pct = entry["non_productive_pct"]
    p_pct = entry["productive_pct"]
    bg, tc = _row_bg(np_pct)
    _rows_html += (
        f'<tr style="background-color:{bg}; color:{tc}">'
        f"<td><b>{uid}</b></td>"
        f"<td>{np_pct:.1f}%</td><td>{p_pct:.1f}%</td>"
        f"<td>{_fmt(entry['non_productive_sec'])}</td>"
        f"<td>{_fmt(entry['productive_sec'])}</td>"
        f"<td>{_fmt(entry['total_sec'])}</td>"
        f'<td><a href="?user_id={uid}" target="_self"'
        f' style="color:{tc}; font-weight:bold; text-decoration:underline;">'
        f"View</a></td>"
        f"</tr>"
    )

_table_html = f"""
<style>
.leader-table {{ width:100%; border-collapse:collapse; font-size:0.92rem; }}
.leader-table th {{ text-align:left; padding:10px 12px; border-bottom:2px solid #444;
                     font-weight:600; }}
.leader-table td {{ padding:10px 12px; border-bottom:1px solid #333; }}
.leader-table tr:hover {{ opacity:0.9; }}
</style>
<table class="leader-table">
<thead>{_header}</thead>
<tbody>{_rows_html}</tbody>
</table>
"""

st.markdown(_table_html, unsafe_allow_html=True)

# ── Footer ──────────────────────────────────────────────────────────
st.caption("Admin view — data refreshes automatically.")

# ── Auto-refresh ────────────────────────────────────────────────────
if auto_refresh:
    import time as _time
    _time.sleep(10)
    st.rerun()
