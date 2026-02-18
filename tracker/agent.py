"""
Tracker agent — runs locally and collects telemetry every POLL_INTERVAL_SEC.

Behaviour
---------
1. Every POLL_INTERVAL_SEC (default 1 s):
   - Capture active window (app_name, window_title)
   - Read accumulated keystroke / mouse counts (and reset)
   - Read system idle time
   - Append a sample to a local batch buffer.

2. Every BATCH_INTERVAL_SEC (default 10 s):
   - POST the buffered samples to the backend /track endpoint.
   - On network failure, persist the batch to BUFFER_FILE (JSON lines)
     and retry on the next cycle.

3. On startup, if BUFFER_FILE exists and is non-empty, flush it first.

Privacy
-------
- Only keystroke *counts* are recorded — NEVER the actual keys pressed.
- Window titles may reveal browsing tabs; users can disable title capture
  via the CAPTURE_WINDOW_TITLE env var (default True).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Ensure project root is on sys.path so relative imports work
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from tracker.platform.factory import get_collector

# ── Configuration ───────────────────────────────────────────────────
load_dotenv(_project_root / ".env")

BACKEND_URL: str = os.getenv("BACKEND_URL", "http://127.0.0.1:5000")
POLL_INTERVAL: float = float(os.getenv("POLL_INTERVAL_SEC", "1"))
BATCH_INTERVAL: float = float(os.getenv("BATCH_INTERVAL_SEC", "10"))
BUFFER_FILE: Path = Path(os.getenv("BUFFER_FILE", str(_project_root / "tracker" / "buffer.json")))
WAKE_THRESHOLD: float = float(os.getenv("WAKE_THRESHOLD_SEC", "30"))
USER_ID: str = os.getenv("USER_ID", "default")

# ── Ghost / system apps to ignore during Power Nap or lock screen ────
# Samples from these apps with zero interaction are silently dropped.
GHOST_APPS: set[str] = {
    s.strip().lower()
    for s in os.getenv(
        "GHOST_APPS",
        "loginwindow,UserNotificationCenter,ScreenSaverEngine,WindowServer,"
        "SystemUIServer,Dock,Finder,LockScreen,logind",
    ).split(",")
    if s.strip()
}

# ── Privacy: Window Title Mode ──────────────────────────────────────
# "full" = store complete title, "redacted" = keywords only, "off" = no title
WINDOW_TITLE_MODE: str = os.getenv("WINDOW_TITLE_MODE", "full").lower().strip()

# Load classification patterns for redaction mode
# (same lists the backend uses for productivity classification)
_REDACT_PATTERNS: list[str] = []
if WINDOW_TITLE_MODE == "redacted":
    for env_key in ("NON_PRODUCTIVE_APPS", "MEETING_APPS"):
        raw = os.getenv(env_key, "")
        _REDACT_PATTERNS.extend(
            s.strip().lower() for s in raw.split(",") if s.strip()
        )

# ── Multi-monitor / split-screen / PiP distraction detection ────────
# Load NON_PRODUCTIVE_APPS patterns to check against visible windows
_NON_PRODUCTIVE_PATTERNS: list[str] = [
    s.strip().lower()
    for s in os.getenv(
        "NON_PRODUCTIVE_APPS",
        "youtube,netflix,reddit,twitter,instagram,facebook,tiktok",
    ).split(",")
    if s.strip()
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tracker.agent")


def _apply_title_mode(window_title: str) -> str:
    """
    Apply the configured WINDOW_TITLE_MODE to a raw window title.

    Modes:
      full     — return as-is (default)
      redacted — scan for classification keywords; return only the matched
                 keyword. Strips away sensitive content like email subjects,
                 document names, URLs. If no keyword matches, return "".
                 e.g. "RE: Salary Review - YouTube" → "youtube"
                 e.g. "secret-project.docx - Microsoft Word" → ""
      off      — always return ""
    """
    if WINDOW_TITLE_MODE == "off":
        return ""

    if WINDOW_TITLE_MODE == "redacted":
        title_lower = window_title.lower()
        for pattern in _REDACT_PATTERNS:
            if pattern in title_lower:
                # Return only the matched keyword, not the full title
                return pattern
        # No classification keyword found — redact entirely
        return ""

    # "full" mode — return unchanged
    return window_title


def _check_distraction(collector, active_app_name: str) -> bool:
    """
    Return True if a non-productive app is visible on ANY screen other than
    the active window.  Catches:
      • YouTube on a second monitor while Cursor is focused
      • Netflix in macOS Split View beside a code editor
      • YouTube PiP floating over the IDE

    Uses CGWindowListCopyWindowInfo (macOS) to enumerate every on-screen
    window, then checks each against NON_PRODUCTIVE_APPS patterns.
    """
    try:
        visible = collector.get_visible_windows()
    except Exception:
        return False

    for owner, title in visible:
        # Skip the currently active app — it's already captured separately
        # (Rule 1 in the productivity engine handles it if it's non-productive)
        if owner == active_app_name:
            continue
        combined = f"{owner} {title}".lower()
        for pattern in _NON_PRODUCTIVE_PATTERNS:
            if pattern in combined:
                return True
    return False


# ── Graceful shutdown ───────────────────────────────────────────────
_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s — shutting down.", signum)
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── Buffering helpers ───────────────────────────────────────────────

def _save_buffer(events: list[dict]) -> None:
    """Append events to the local JSON-lines buffer file."""
    if not events:
        return
    try:
        BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BUFFER_FILE, "a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        logger.info("Buffered %d events locally.", len(events))
    except OSError as exc:
        logger.error("Failed to write buffer file: %s", exc)


def _load_and_clear_buffer() -> list[dict]:
    """Load buffered events from disk and clear the file."""
    if not BUFFER_FILE.exists() or BUFFER_FILE.stat().st_size == 0:
        return []
    events: list[dict] = []
    try:
        with open(BUFFER_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        # Clear after successful read
        BUFFER_FILE.write_text("")
        logger.info("Loaded %d buffered events from disk.", len(events))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Error reading buffer: %s", exc)
    return events


def _send_batch(events: list[dict]) -> bool:
    """
    POST events to the backend.  Returns True on success.
    On failure, returns False (caller should buffer locally).
    """
    if not events:
        return True
    try:
        resp = requests.post(
            f"{BACKEND_URL}/track",
            json={"events": events},
            timeout=5,
        )
        if resp.status_code == 201:
            data = resp.json()
            logger.info("Sent %d events — server ingested %d.", len(events), data.get("ingested", "?"))
            return True
        else:
            logger.warning("Backend returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.RequestException as exc:
        logger.warning("Backend unreachable: %s", exc)
        return False


# ── Main loop ───────────────────────────────────────────────────────

def main() -> None:
    logger.info("Starting tracker agent.")
    logger.info("  User ID       : %s", USER_ID)
    logger.info("  Backend URL   : %s", BACKEND_URL)
    logger.info("  Poll interval : %.1f s", POLL_INTERVAL)
    logger.info("  Batch interval: %.1f s", BATCH_INTERVAL)
    logger.info("  Buffer file   : %s", BUFFER_FILE)
    logger.info("  Title mode    : %s", WINDOW_TITLE_MODE)

    collector = get_collector()
    collector.start_input_listener()

    # Flush any buffered events from a previous crash
    stale = _load_and_clear_buffer()
    if stale:
        if not _send_batch(stale):
            _save_buffer(stale)

    batch: list[dict] = []
    last_flush = time.monotonic()
    last_wall = time.time()  # wall-clock tracks sleep/hibernate gaps

    try:
        while _running:
            loop_start = time.monotonic()
            now_wall = time.time()

            # ── Wake detection ───────────────────────────────────
            # Wall-clock advances during system sleep; monotonic may not.
            # If the gap exceeds WAKE_THRESHOLD, the system was suspended.
            wall_gap = now_wall - last_wall
            if wall_gap > WAKE_THRESHOLD:
                logger.info(
                    "Wake detected: system was suspended for ~%.0f s. "
                    "Flushing stale batch (%d samples) and resetting counters.",
                    wall_gap, len(batch),
                )
                # Flush pre-sleep samples (they have valid pre-sleep timestamps)
                if batch:
                    if not _send_batch(batch):
                        _save_buffer(batch)
                    batch = []
                    last_flush = time.monotonic()

                # Discard stale input counts accumulated while suspended
                collector.get_and_reset_counts()

                # Skip this iteration — first post-wake sample has inflated
                # idle_seconds (= entire sleep duration) which is misleading
                last_wall = now_wall
                time.sleep(POLL_INTERVAL)
                continue

            last_wall = now_wall

            # 1. Collect a sample
            try:
                app_name, window_title = collector.get_active_window()
                window_title = _apply_title_mode(window_title)
            except Exception as exc:
                logger.debug("Window detection error: %s", exc)
                app_name, window_title = "unknown", ""

            # Check all visible windows for non-productive distractions
            # (multi-monitor, split-screen, PiP)
            distraction_visible = _check_distraction(collector, app_name)

            counts = collector.get_and_reset_counts()
            idle = collector.get_idle_seconds()

            # Skip ghost/system apps with no interaction (Power Nap, lock screen)
            is_ghost = app_name.lower() in GHOST_APPS
            has_interaction = counts["keystroke_count"] > 0 or counts["mouse_clicks"] > 0
            if is_ghost and not has_interaction:
                last_wall = now_wall
                elapsed = time.monotonic() - loop_start
                sleep_time = max(0, POLL_INTERVAL - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                continue

            sample = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user_id": USER_ID,
                "app_name": app_name,
                "window_title": window_title,
                "keystroke_count": counts["keystroke_count"],
                "mouse_clicks": counts["mouse_clicks"],
                "mouse_distance": counts["mouse_distance"],
                "idle_seconds": idle,
                "distraction_visible": distraction_visible,
            }
            batch.append(sample)

            # 2. Flush batch on interval
            now = time.monotonic()
            if now - last_flush >= BATCH_INTERVAL:
                if not _send_batch(batch):
                    _save_buffer(batch)
                batch = []
                last_flush = now

            # 3. Sleep to honour poll interval
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0, POLL_INTERVAL - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    finally:
        # Shutdown: flush remaining batch
        collector.stop_input_listener()
        if batch:
            if not _send_batch(batch):
                _save_buffer(batch)
        logger.info("Tracker agent stopped.")


if __name__ == "__main__":
    main()
