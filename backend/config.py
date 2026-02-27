"""
Backend configuration — loaded from environment / .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


class Config:
    """Flask + app configuration."""

    # ── Flask / DB ──────────────────────────────────────────────
    FLASK_HOST: str = os.getenv("FLASK_HOST", "127.0.0.1")
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))
    SQLALCHEMY_DATABASE_URI: str = os.getenv("DATABASE_URI", "sqlite:///telemetry.db")
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ── Productivity thresholds (tuned for 10-second buckets) ───
    BUCKET_SIZE_SEC: int = int(os.getenv("BUCKET_SIZE_SEC", "10"))

    # Combined interaction threshold (keystrokes + clicks)
    # Scaled from 10 (at 60s) → 2 (at 10s)
    PRODUCTIVE_INTERACTION_THRESHOLD: int = int(
        os.getenv("PRODUCTIVE_INTERACTION_THRESHOLD", "2")
    )
    # Keystroke-only threshold (if user is mostly typing)
    # Scaled from 5 (at 60s) → 1 (at 10s)
    PRODUCTIVE_KEYSTROKE_THRESHOLD: int = int(
        os.getenv("PRODUCTIVE_KEYSTROKE_THRESHOLD", "1")
    )
    # Mouse-only threshold (if user is mostly clicking — design tools, etc.)
    # Scaled from 3 (at 60s) → 1 (at 10s)
    PRODUCTIVE_MOUSE_THRESHOLD: int = int(
        os.getenv("PRODUCTIVE_MOUSE_THRESHOLD", "1")
    )

    # ── Reading / Active Presence detection (tuned for 10s buckets) ──
    # Minimum mouse movement (pixels) to infer physical presence (reading/scrolling)
    # Scaled from 50px (at 60s) → 8px (at 10s)
    MOUSE_MOVEMENT_THRESHOLD: float = float(
        os.getenv("MOUSE_MOVEMENT_THRESHOLD", "8")
    )
    # OS idle seconds beyond which the user is assumed away from the computer
    IDLE_AWAY_THRESHOLD: float = float(
        os.getenv("IDLE_AWAY_THRESHOLD", "30")
    )
    # Anti-wiggle: minimum 1-second samples with mouse movement in a bucket
    # to count as sustained presence (real reading vs occasional nudge)
    # Scaled from 15 (at 60s) → 3 (at 10s)
    MOUSE_MOVEMENT_MIN_SAMPLES: int = int(
        os.getenv("MOUSE_MOVEMENT_MIN_SAMPLES", "3")
    )

    # ── Anti-cheat: Interaction variance (tuned for 10s buckets) ──
    # Minimum fraction of samples with zero interaction (natural pauses)
    MIN_ZERO_SAMPLE_RATIO: float = float(
        os.getenv("MIN_ZERO_SAMPLE_RATIO", "0.25")
    )
    # Minimum distinct per-sample interaction values (real typing = many, bot = 1-2)
    # Reduced from 3 to 2 for 10s buckets (fewer samples = fewer possible distinct values)
    MIN_DISTINCT_VALUES: int = int(
        os.getenv("MIN_DISTINCT_VALUES", "2")
    )

    # ── Multi-monitor / Split-screen / PiP distraction ─────────
    # Fraction of bucket samples with a visible distraction needed to
    # block the "active presence" (reading) productivity pathway
    DISTRACTION_MIN_RATIO: float = float(
        os.getenv("DISTRACTION_MIN_RATIO", "0.3")
    )

    # ── Meeting apps (always productive) ────────────────────────
    # Apps that are considered productive even with zero interaction
    # (you're talking in a meeting, not typing)
    MEETING_APPS: list[str] = [
        s.strip().lower()
        for s in os.getenv(
            "MEETING_APPS",
            "zoom,microsoft teams,google meet,webex,facetime,slack huddle,discord call,skype,around,tuple,gather",
        ).split(",")
        if s.strip()
    ]

    # ── Data Retention ────────────────────────────────────────────
    # Days of raw events to keep; older events are purged on startup
    # 0 = disabled (keep forever)
    DATA_RETENTION_DAYS: int = int(os.getenv("DATA_RETENTION_DAYS", "14"))

    # ── Timezone ──────────────────────────────────────────────────
    # Local timezone for day boundary calculations
    # "today" starts at midnight in THIS timezone, not UTC
    TIMEZONE: str = os.getenv("TIMEZONE", "UTC")

    # ── Browser apps (website-level breakdown) ──────────────────
    # Window titles of these apps are parsed to extract the website/service name
    BROWSER_APPS: list[str] = [
        s.strip().lower()
        for s in os.getenv(
            "BROWSER_APPS",
            "safari,google chrome,chrome,firefox,microsoft edge,msedge,brave browser,brave,arc,chromium,opera",
        ).split(",")
        if s.strip()
    ]

    # ── App classification ──────────────────────────────────────
    # Apps that are ALWAYS non-productive regardless of interaction
    NON_PRODUCTIVE_APPS: list[str] = [
        s.strip().lower()
        for s in os.getenv(
            "NON_PRODUCTIVE_APPS",
            "youtube,netflix,reddit,twitter,x.com,instagram,facebook,tiktok,twitch,discord,spotify,steam,epic games",
        ).split(",")
        if s.strip()
    ]

    # ── Data Minimization ──────────────────────────────────────
    # Server-side enforcement: discard all window titles before storage
    # Even if a tracker sends titles, the backend replaces them with ""
    DROP_TITLES: bool = os.getenv("DROP_TITLES", "false").lower() in ("true", "1", "yes")

    # ── Rate Limiting & Input Validation ────────────────────────
    # Max request body size in kilobytes (rejects with HTTP 413 if exceeded)
    MAX_REQUEST_SIZE_KB: int = int(os.getenv("MAX_REQUEST_SIZE_KB", "512"))

    # Per-device (or per-IP in demo mode) rate limit for POST /track
    # Format: "<count>/minute" — requests beyond this get HTTP 429
    RATE_LIMIT_PER_DEVICE: str = os.getenv("RATE_LIMIT_PER_DEVICE", "120/minute")

    # ── Enterprise Hardening ─────────────────────────────────────
    # Master toggle: True = relaxed (no auth, open dashboards)
    #                False = enforce device auth, RBAC, rate limits, etc.
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() in ("true", "1", "yes")

    # Flask session signing key (required in production)
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    # Admin credentials (required in production)
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
