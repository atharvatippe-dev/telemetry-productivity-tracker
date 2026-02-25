"""
AI-powered leaderboard summary generator.

Provides two summary strategies:
  1. OpenAI-based: sends aggregated (privacy-safe) leaderboard data to
     an LLM and returns a structured markdown summary.
  2. Deterministic fallback: computes the same summary sections using
     simple heuristics when OpenAI is unavailable or fails.

Two levels of summary are available:
  - get_summary()            — compact inline summary for admin dashboard (~160 words)
  - get_executive_summary()  — detailed executive-ready report (~180-300 words)

Usage:
    from ai_summary import get_summary, get_executive_summary
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

logger = logging.getLogger("ai_summary")

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_SUMMARY_TTL = 60  # seconds

_cached_summary: str = ""
_cached_at: float = 0.0
_cached_hash: str = ""

SYSTEM_PROMPT = (
    "You are a helpful analytics assistant. Summarize productivity leaderboard "
    "data for a manager. Be neutral, non-judgmental, and privacy-safe. Do not "
    "shame individuals. Do not infer intent or personal traits. Keep the summary "
    "under 160 words.\n\n"
    "Use EXACTLY this format (bold labels, bullet points, no markdown headers):\n\n"
    "**Overview**\n"
    "One or two sentence summary of the team's day.\n\n"
    "**Highlights**\n"
    "- bullet 1\n"
    "- bullet 2\n"
    "- bullet 3\n\n"
    "**Watchouts**\n"
    "- bullet 1\n"
    "- bullet 2\n\n"
    "**Suggested Actions**\n"
    "- bullet 1\n"
    "- bullet 2\n\n"
    "**Data Note**\n"
    "One line about data methodology."
)


def _build_payload(leaderboard: list[dict]) -> dict:
    """
    Build a compact, privacy-safe JSON payload from raw leaderboard data.
    Only includes aggregated metrics -- no window titles, no keystroke content.
    """
    if not leaderboard:
        return {}

    sorted_by_prod = sorted(leaderboard, key=lambda r: r.get("productive_pct", 0), reverse=True)
    prod_pcts = [r.get("productive_pct", 0) for r in leaderboard]
    non_prod_pcts = [r.get("non_productive_pct", 0) for r in leaderboard]

    top_n = min(3, len(sorted_by_prod))
    bottom_n = min(3, len(sorted_by_prod))

    def _user_entry(r: dict) -> dict:
        return {
            "user_id": r.get("user_id", "unknown"),
            "productive_pct": round(r.get("productive_pct", 0), 1),
            "non_productive_pct": round(r.get("non_productive_pct", 0), 1),
            "productive_minutes": round(r.get("productive_sec", 0) / 60, 1),
            "non_productive_minutes": round(r.get("non_productive_sec", 0) / 60, 1),
        }

    return {
        "leaderboard_definition": (
            "Ranks employees by non-productive percentage and summarizes "
            "productive vs non-productive time inferred from interaction "
            "counts and app classification."
        ),
        "time_window": "today",
        "refresh_interval_sec": 60,
        "total_users": len(leaderboard),
        "top_users": [_user_entry(r) for r in sorted_by_prod[:top_n]],
        "bottom_users": [_user_entry(r) for r in sorted_by_prod[-bottom_n:]],
        "team_aggregates": {
            "avg_productive_pct": round(statistics.mean(prod_pcts), 1),
            "median_productive_pct": round(statistics.median(prod_pcts), 1),
            "highest_non_productive_pct": round(max(non_prod_pcts), 1),
            "lowest_non_productive_pct": round(min(non_prod_pcts), 1),
        },
    }


def _call_openai(payload: dict) -> str:
    """Call OpenAI API with the structured payload. Returns markdown string."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed")

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=15.0, max_retries=1)

    user_msg = (
        "Generate the summary using the JSON below. The leaderboard is inferred "
        "from app categories and interaction counts only; no keystroke content is "
        f"captured. JSON: {json.dumps(payload)}"
    )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=400,
    )

    return response.choices[0].message.content.strip()


def _fallback_summary(leaderboard: list[dict]) -> str:
    """
    Deterministic fallback when OpenAI is unavailable.
    Produces the same structured markdown sections using simple heuristics.
    """
    if not leaderboard:
        return "_No leaderboard data available._"

    n = len(leaderboard)
    if n < 2:
        r = leaderboard[0]
        return (
            f"**Overview:** Only one user tracked today ({r['user_id']}) with "
            f"{r.get('productive_pct', 0):.0f}% productive time. More data "
            "needed for meaningful team insights.\n\n"
            "**Data note:** Productivity is inferred from app categories and "
            "interaction counts, not keystroke content."
        )

    prod_pcts = [r.get("productive_pct", 0) for r in leaderboard]
    avg_prod = statistics.mean(prod_pcts)
    median_prod = statistics.median(prod_pcts)
    total_minutes = sum(r.get("total_sec", 0) for r in leaderboard) / 60

    sorted_lb = sorted(leaderboard, key=lambda r: r.get("productive_pct", 0), reverse=True)
    top = sorted_lb[0]
    bottom = sorted_lb[-1]

    if total_minutes < 10:
        return (
            "**Overview:** Less than 10 minutes of total tracked time across "
            "the team. Insufficient data for meaningful insights.\n\n"
            "**Data note:** Productivity is inferred from app categories and "
            "interaction counts, not keystroke content."
        )

    overview = (
        f"**Overview**\n"
        f"{n} team members tracked today with an average productive rate of "
        f"{avg_prod:.0f}% (median {median_prod:.0f}%). "
        f"Total tracked time: {total_minutes:.0f} minutes."
    )

    highlights = (
        "**Highlights**\n"
        f"- {top['user_id']} leads with {top.get('productive_pct', 0):.0f}% productive time\n"
        f"- Team median productivity is {median_prod:.0f}%\n"
        f"- {n} users are actively being tracked today"
    )

    spread = max(prod_pcts) - min(prod_pcts)
    watchouts = (
        "**Watchouts**\n"
        f"- {bottom['user_id']} has the highest non-productive percentage "
        f"at {bottom.get('non_productive_pct', 0):.0f}%\n"
        f"- Productivity spread across the team is {spread:.0f} percentage points"
    )

    actions = (
        "**Suggested Actions**\n"
        "- Review app classification settings to ensure accurate categorization\n"
        "- Consider a brief team check-in to understand workflow patterns"
    )

    data_note = (
        "**Data Note**\n"
        "Productivity is inferred from app categories and "
        "interaction counts, not keystroke content. Updates every 60 seconds."
    )

    return f"{overview}\n\n{highlights}\n\n{watchouts}\n\n{actions}\n\n{data_note}"


def _data_hash(leaderboard: list[dict]) -> str:
    """Fast hash of leaderboard data for cache invalidation."""
    raw = json.dumps(leaderboard, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def get_summary(leaderboard: list[dict]) -> tuple[str, bool]:
    """
    Return (markdown_summary, is_ai_generated).

    Uses a 60-second TTL cache. If the data hasn't changed and the cache
    is fresh, returns the cached result without calling OpenAI.
    """
    global _cached_summary, _cached_at, _cached_hash

    now = time.time()
    current_hash = _data_hash(leaderboard)

    if _cached_summary and (now - _cached_at) < _SUMMARY_TTL:
        return _cached_summary, "ai" in _cached_summary.lower() or _cached_at > 0

    if not leaderboard:
        return "_No leaderboard data available._", False

    is_ai = False

    if OPENAI_API_KEY:
        try:
            payload = _build_payload(leaderboard)
            summary = _call_openai(payload)
            is_ai = True
        except Exception as exc:
            logger.warning("OpenAI call failed: %s — using fallback.", exc)
            summary = _fallback_summary(leaderboard)
    else:
        summary = _fallback_summary(leaderboard)

    _cached_summary = summary
    _cached_at = now
    _cached_hash = current_hash

    return summary, is_ai


# =====================================================================
#  Executive Summary — richer, executive-ready report
# =====================================================================

EXEC_SYSTEM_PROMPT = (
    "You are the Head of Operations reviewing daily productivity telemetry for your team.\n\n"
    "Your job:\n"
    "- Assess overall team health quickly\n"
    "- Identify top and bottom performers (data only)\n"
    "- Detect operational risks\n"
    "- Highlight variance and engagement spread\n"
    "- Recommend operational actions (not HR actions)\n"
    "- Stay neutral, objective, and data-driven\n\n"
    "Do NOT:\n"
    "- Shame individuals\n"
    "- Infer intent, mood, or personal traits\n"
    "- Over-explain methodology\n"
    "- Use emotional or motivational language\n"
    "- Write more than 220 words\n\n"
    "Interpret the data using these operational rules:\n"
    "- If average productive % < 55%, explicitly flag as performance concern.\n"
    "- If productivity spread > 30 percentage points, flag uneven engagement.\n"
    "- If highest non-productive % > 50%, flag distraction risk.\n"
    "- If total tracked time per user is low (< 60 min average), flag insufficient data.\n"
    "- If top 1 and bottom 1 differ by > 40 points, flag strong imbalance.\n\n"
    "Focus on signal over commentary.\n\n"
    "Use EXACTLY this structure:\n\n"
    "## Daily Ops Snapshot\n"
    "2-3 sentences summarizing team performance, trend strength, and overall health.\n\n"
    "## Key Signals\n"
    "- Bullet highlighting average productivity and total tracked time\n"
    "- Bullet highlighting top performer\n"
    "- Bullet highlighting lowest performer\n"
    "- Bullet highlighting spread across team\n\n"
    "## Risk Indicators\n"
    "- Bullet(s) only if thresholds triggered\n"
    "- If no major risks, state: \"No major operational risks detected.\"\n\n"
    "## Operational Actions\n"
    "- 2-3 practical next steps (e.g., review app classification, team sync, monitor trend)\n\n"
    "## Data Note\n"
    "Productivity is inferred from application categories and interaction counts only. "
    "No keystroke content or personal data is captured."
)

_exec_cached_summary: str = ""
_exec_cached_at: float = 0.0
_EXEC_TTL = 300  # 5-minute cache for executive summary


def _build_exec_payload(leaderboard: list[dict]) -> dict:
    """
    Build a detailed, privacy-safe payload for the executive summary.
    Includes per-user aggregated metrics, team stats, and outlier detection.
    """
    if not leaderboard:
        return {}

    sorted_by_prod = sorted(
        leaderboard, key=lambda r: r.get("productive_pct", 0), reverse=True
    )
    prod_pcts = [r.get("productive_pct", 0) for r in leaderboard]
    non_prod_pcts = [r.get("non_productive_pct", 0) for r in leaderboard]
    total_secs = [r.get("total_sec", 0) for r in leaderboard]

    avg_prod = statistics.mean(prod_pcts)
    median_prod = statistics.median(prod_pcts)
    stdev_prod = statistics.stdev(prod_pcts) if len(prod_pcts) > 1 else 0

    def _user_detail(r: dict) -> dict:
        return {
            "user_id": r.get("user_id", "unknown"),
            "productive_pct": round(r.get("productive_pct", 0), 1),
            "non_productive_pct": round(r.get("non_productive_pct", 0), 1),
            "productive_minutes": round(r.get("productive_sec", 0) / 60, 1),
            "non_productive_minutes": round(r.get("non_productive_sec", 0) / 60, 1),
            "total_minutes": round(r.get("total_sec", 0) / 60, 1),
        }

    # Detect outliers: users > 1 stdev away from mean
    outliers = []
    if stdev_prod > 0:
        for r in leaderboard:
            pct = r.get("productive_pct", 0)
            deviation = (pct - avg_prod) / stdev_prod
            if abs(deviation) > 1.0:
                direction = "above average" if deviation > 0 else "below average"
                outliers.append({
                    "user_id": r.get("user_id", "unknown"),
                    "productive_pct": round(pct, 1),
                    "direction": direction,
                    "deviation": round(abs(deviation), 1),
                })

    top_n = min(3, len(sorted_by_prod))
    bottom_n = min(3, len(sorted_by_prod))

    return {
        "report_type": "Executive Daily Productivity Summary",
        "time_window": "today (so far)",
        "team_size": len(leaderboard),
        "team_aggregates": {
            "avg_productive_pct": round(avg_prod, 1),
            "median_productive_pct": round(median_prod, 1),
            "stdev_productive_pct": round(stdev_prod, 1),
            "total_productive_minutes": round(
                sum(r.get("productive_sec", 0) for r in leaderboard) / 60, 1
            ),
            "total_non_productive_minutes": round(
                sum(r.get("non_productive_sec", 0) for r in leaderboard) / 60, 1
            ),
            "total_tracked_minutes": round(sum(total_secs) / 60, 1),
            "highest_non_productive_pct": round(max(non_prod_pcts), 1),
            "lowest_non_productive_pct": round(min(non_prod_pcts), 1),
            "productivity_spread_pct": round(max(prod_pcts) - min(prod_pcts), 1),
        },
        "top_performers": [_user_detail(r) for r in sorted_by_prod[:top_n]],
        "bottom_performers": [_user_detail(r) for r in sorted_by_prod[-bottom_n:]],
        "outliers": outliers,
        "methodology": (
            "Productivity is inferred from application categories and user "
            "interaction counts (keystrokes, mouse clicks, mouse movement). "
            "No keystroke content, window titles, or personal data is included."
        ),
    }


def _call_openai_exec(payload: dict) -> str:
    """Call OpenAI with the executive summary prompt. Returns markdown string."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — run: pip install openai")

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0, max_retries=1)

    user_msg = (
        "Generate the executive productivity summary using the JSON data below. "
        "Remember: be descriptive, constructive, and use easy language suitable "
        "for company leadership. No shaming. No sensitive details.\n\n"
        f"JSON: {json.dumps(payload)}"
    )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": EXEC_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.5,
        max_tokens=800,
    )

    return response.choices[0].message.content.strip()


def _fallback_exec_summary(leaderboard: list[dict]) -> str:
    """
    Deterministic Ops Snapshot fallback when OpenAI is unavailable.
    Matches the same structure as the AI prompt.
    """
    if not leaderboard:
        return "_No leaderboard data available._"

    n = len(leaderboard)
    prod_pcts = [r.get("productive_pct", 0) for r in leaderboard]
    non_prod_pcts = [r.get("non_productive_pct", 0) for r in leaderboard]
    avg_prod = statistics.mean(prod_pcts)
    total_min = sum(r.get("total_sec", 0) for r in leaderboard) / 60
    avg_min_per_user = total_min / n if n else 0

    sorted_lb = sorted(leaderboard, key=lambda r: r.get("productive_pct", 0), reverse=True)
    top = sorted_lb[0]
    bottom = sorted_lb[-1]
    spread = max(prod_pcts) - min(prod_pcts) if n > 1 else 0
    max_np = max(non_prod_pcts) if non_prod_pcts else 0
    top_bottom_diff = top.get("productive_pct", 0) - bottom.get("productive_pct", 0) if n > 1 else 0

    if total_min < 10:
        return (
            "## Daily Ops Snapshot\n\n"
            "Less than 10 minutes of total tracked time across the team. "
            "Insufficient data for meaningful operational insights.\n\n"
            "## Data Note\n\n"
            "Productivity is inferred from application categories and interaction "
            "counts only. No keystroke content or personal data is captured."
        )

    # Daily Ops Snapshot
    health = "healthy" if avg_prod >= 55 else "below benchmark"
    snapshot = (
        f"## Daily Ops Snapshot\n\n"
        f"{n} team member{'s' if n > 1 else ''} tracked today with "
        f"{total_min:.0f} total minutes of active usage. "
        f"Average productive rate is {avg_prod:.0f}% — {health}. "
    )
    if spread > 30:
        snapshot += f"Engagement spread is wide at {spread:.0f} points."
    else:
        snapshot += "Team engagement is relatively consistent."

    # Key Signals
    signals = ["## Key Signals\n"]
    signals.append(f"- Average productivity: {avg_prod:.0f}% across {total_min:.0f} total tracked minutes")
    signals.append(f"- Top performer: {top['user_id']} at {top.get('productive_pct', 0):.0f}% productive")
    if n > 1:
        signals.append(f"- Lowest performer: {bottom['user_id']} at {bottom.get('productive_pct', 0):.0f}% productive")
        signals.append(f"- Spread: {spread:.0f} percentage points across team")

    # Risk Indicators
    risks = ["## Risk Indicators\n"]
    has_risk = False
    if avg_prod < 55:
        risks.append(f"- **Performance concern:** Average productive rate ({avg_prod:.0f}%) is below 55% threshold")
        has_risk = True
    if spread > 30:
        risks.append(f"- **Uneven engagement:** {spread:.0f}-point spread indicates inconsistent team output")
        has_risk = True
    if max_np > 50:
        worst = [r for r in leaderboard if r.get("non_productive_pct", 0) == max_np][0]
        risks.append(f"- **Distraction risk:** {worst['user_id']} at {max_np:.0f}% non-productive")
        has_risk = True
    if avg_min_per_user < 60:
        risks.append(f"- **Insufficient data:** Average {avg_min_per_user:.0f} min/user — trends may not be reliable")
        has_risk = True
    if top_bottom_diff > 40:
        risks.append(f"- **Strong imbalance:** {top_bottom_diff:.0f}-point gap between top and bottom performer")
        has_risk = True
    if not has_risk:
        risks.append("- No major operational risks detected.")

    # Operational Actions
    actions = ["## Operational Actions\n"]
    actions.append("- Review app classification settings to ensure accurate categorization")
    if has_risk:
        actions.append("- Schedule brief team sync to surface workflow blockers")
    actions.append("- Monitor trend over the next 2–3 days before drawing conclusions")

    data_note = (
        "## Data Note\n\n"
        "Productivity is inferred from application categories and interaction counts only. "
        "No keystroke content or personal data is captured."
    )

    return "\n\n".join([snapshot, "\n".join(signals), "\n".join(risks), "\n".join(actions), data_note])


def get_executive_summary(
    leaderboard: list[dict], force_refresh: bool = False
) -> tuple[str, bool]:
    """
    Return (markdown_executive_summary, is_ai_generated).

    Uses a 5-minute TTL cache. Pass force_refresh=True to bypass cache.
    """
    global _exec_cached_summary, _exec_cached_at

    now = time.time()

    if (
        not force_refresh
        and _exec_cached_summary
        and (now - _exec_cached_at) < _EXEC_TTL
    ):
        is_ai = bool(OPENAI_API_KEY)
        return _exec_cached_summary, is_ai

    if not leaderboard:
        return "_No leaderboard data available._", False

    is_ai = False

    if OPENAI_API_KEY:
        try:
            payload = _build_exec_payload(leaderboard)
            summary = _call_openai_exec(payload)
            is_ai = True
        except Exception as exc:
            logger.warning("OpenAI exec call failed: %s — using fallback.", exc)
            summary = _fallback_exec_summary(leaderboard)
    else:
        summary = _fallback_exec_summary(leaderboard)

    _exec_cached_summary = summary
    _exec_cached_at = now

    return summary, is_ai
