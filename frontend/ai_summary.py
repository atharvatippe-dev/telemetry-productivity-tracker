"""
AI-powered leaderboard summary generator.

Provides two summary strategies:
  1. OpenAI-based: sends aggregated (privacy-safe) leaderboard data to
     an LLM and returns a structured markdown summary.
  2. Deterministic fallback: computes the same summary sections using
     simple heuristics when OpenAI is unavailable or fails.

Usage:
    from frontend.ai_summary import get_summary
    markdown = get_summary(leaderboard_data)
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
