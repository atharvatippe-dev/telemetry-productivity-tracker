"""
Productivity inference engine — 2-state model.

Converts raw telemetry events into labelled time-buckets with:
  - productivity state:  productive | non_productive
  - confidence score:    [0.0 … 1.0]

Decision tree (evaluated top-to-bottom, first match wins)
---------------------------------------------------------
1. Non-productive app (YouTube, Netflix, …) → ALWAYS non_productive
   Even with interaction — typing YouTube comments ≠ work.

2. Meeting app (Zoom, Teams, Meet, …) → ALWAYS productive
   Meetings are real work — you talk, listen, present. Zero typing is normal.

3. Interaction meets threshold → productive (with anti-cheat check)
   Keystrokes + clicks above the configured thresholds = clearly working.
   BUT if the pattern is suspiciously uniform (auto-clicker/key repeater),
   it gets downgraded to non_productive.
   Distraction on another screen applies a confidence penalty but does NOT
   override genuinely typed/clicked interaction.

4. Active presence detected (reading/reviewing) → productive
   Mouse movement ≥ MOUSE_MOVEMENT_THRESHOLD AND OS idle < IDLE_AWAY_THRESHOLD.
   BLOCKED if a non-productive distraction is visible on another monitor /
   split-view / PiP (≥ DISTRACTION_MIN_RATIO of samples flagged).
   Reasoning: mouse movement without typing is ambiguous — if YouTube is
   on the other screen, the user is more likely watching than reading code.

5. Everything else → non_productive
   No interaction AND no mouse movement (or high OS idle) = user is away
   from the computer or disengaged (e.g., phone, daydreaming).
"""
##python-note down variable and understand it later
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from backend.config import Config
from backend.models import TelemetryEvent


# ── Productivity states ─────────────────────────────────────────────
STATES = (
    "productive",
    "non_productive",
)


@dataclass
class Bucket:
    """One time-bucket of aggregated telemetry."""

    start: datetime
    end: datetime
    state: str = "non_productive"
    confidence: float = 0.0
    total_keystrokes: int = 0
    total_clicks: int = 0
    total_mouse_distance: float = 0.0
    max_idle: float = 0.0
    dominant_app: str = "unknown"
    dominant_title: str = ""
    event_count: int = 0

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "state": self.state,
            #3 decimals for confidence
            "confidence": round(self.confidence, 3),
            "total_keystrokes": self.total_keystrokes,
            "total_clicks": self.total_clicks,
            "total_mouse_distance": round(self.total_mouse_distance, 1),
            "max_idle": round(self.max_idle, 1),
            "dominant_app": self.dominant_app,
            "dominant_title": self.dominant_title,
            "event_count": self.event_count,
        }


# ── Helpers ─────────────────────────────────────────────────────────
#this helper function takes 3 parameters appname,windowtitle and config and return boolean
def _is_non_productive_app(app_name: str, window_title: str, cfg: Config) -> bool:
    """
    Return True if the dominant app/title matches the NON_PRODUCTIVE_APPS list.
    These are always non-productive regardless of interaction level.
    """
    combined = f"{app_name} {window_title}".lower()
    for pattern in cfg.NON_PRODUCTIVE_APPS:
        if pattern in combined:


            return True
    return False


def _is_meeting_app(app_name: str, window_title: str, cfg: Config) -> bool:
    """
    Return True if the dominant app/title matches the MEETING_APPS list.
    Meeting apps are always productive — you're talking/listening, not typing.
    e.g. Zoom, Microsoft Teams, Google Meet, Webex, FaceTime.
    """
    combined = f"{app_name} {window_title}".lower()
    for pattern in cfg.MEETING_APPS:
        if pattern in combined:
            return True
    return False


def _dominant(events: Sequence[TelemetryEvent]) -> tuple[str, str]:
    """Return the most-frequent (app_name, window_title) pair."""
    from collections import Counter
    counts: Counter[tuple[str, str]] = Counter()
    for e in events:
        counts[(e.app_name, e.window_title)] += 1
    if not counts:
        return ("unknown", "")
    (app, title), _ = counts.most_common(1)[0]
    return app, title


def _meets_threshold(
    total_keystrokes: int,
    total_clicks: int,
    cfg: Config,
) -> bool:
    """
    Return True if the bucket's interaction meets ANY of the thresholds:
      • combined (keystrokes + clicks) >= PRODUCTIVE_INTERACTION_THRESHOLD
      • keystrokes alone            >= PRODUCTIVE_KEYSTROKE_THRESHOLD
      • clicks alone                >= PRODUCTIVE_MOUSE_THRESHOLD

    Meeting any single threshold is enough to be productive.
    """
    combined = total_keystrokes + total_clicks
    if combined >= cfg.PRODUCTIVE_INTERACTION_THRESHOLD:
        return True
    if total_keystrokes >= cfg.PRODUCTIVE_KEYSTROKE_THRESHOLD:
        return True
    if total_clicks >= cfg.PRODUCTIVE_MOUSE_THRESHOLD:
        return True
    return False


def _is_suspicious_pattern(
    events: Sequence[TelemetryEvent],
    cfg: Config,
) -> bool:
    """
    Return True if the interaction pattern looks like an auto-clicker or
    key repeater rather than genuine human input.

    Two signals (BOTH must be suspicious to flag):

    1. Zero-sample ratio — real typing has natural pauses where per-sample
       interaction is 0 (thinking, reading between bursts). Auto-clickers
       produce input on nearly every sample.
       Flag: fraction of zero-interaction samples < MIN_ZERO_SAMPLE_RATIO

    2. Distinct values — real typing produces many different per-sample
       counts (0, 1, 3, 5, 8, 12…). Auto-clickers produce 1-2 values
       (e.g., always exactly 1 keystroke per sample).
       Flag: fewer than MIN_DISTINCT_VALUES unique per-sample counts

    Only flags when BOTH signals are suspicious simultaneously, to avoid
    false positives on short, focused typing bursts.
    """
    if len(events) < 10:
        # Too few samples to judge variance; don't flag
        return False

    # Per-sample interaction count (keystrokes + clicks)
    per_sample = [e.keystroke_count + e.mouse_clicks for e in events]

    # Signal 1: what fraction of samples have zero interaction?
    zero_count = sum(1 for v in per_sample if v == 0)
    zero_ratio = zero_count / len(per_sample)
    low_zeros = zero_ratio < cfg.MIN_ZERO_SAMPLE_RATIO

    # Signal 2: how many distinct values appear?
    distinct = len(set(per_sample))
    low_variety = distinct < cfg.MIN_DISTINCT_VALUES

    # Only flag if BOTH signals are suspicious
    return low_zeros and low_variety


def _is_actively_present(
    total_mouse_distance: float,
    max_idle: float,
    events: Sequence[TelemetryEvent],
    cfg: Config,
) -> bool:
    """
    Return True if the user is physically present and engaged — i.e. "reading".

    Conditions (ALL must be true):
      • Mouse movement ≥ MOUSE_MOVEMENT_THRESHOLD  (scrolling, tracking code)
      • OS idle time   < IDLE_AWAY_THRESHOLD        (they haven't walked away)
      • Movement is sustained across ≥ MOUSE_MOVEMENT_MIN_SAMPLES samples
        (anti-wiggle: real reading = movement in 20-50 out of 60 samples,
         occasional nudge = movement in 1-5 samples)

    This catches: code review, reading documentation, scrolling through PRs,
    reviewing design mockups — any situation where the user is at the computer
    but not actively typing or clicking.

    This blocks: occasional mouse wiggles while on the phone.
    """
    has_movement = total_mouse_distance >= cfg.MOUSE_MOVEMENT_THRESHOLD
    not_away = max_idle < cfg.IDLE_AWAY_THRESHOLD

    if not (has_movement and not_away):
        return False

    # Anti-wiggle: count how many individual samples have mouse movement
    # A wiggle = 1-5 samples with movement. Real reading = 15+ samples.
    samples_with_movement = sum(1 for e in events if e.mouse_distance > 0)
    is_sustained = samples_with_movement >= cfg.MOUSE_MOVEMENT_MIN_SAMPLES

    return is_sustained


def _has_distraction(events: Sequence[TelemetryEvent], cfg: Config) -> bool:
    """
    Return True if a significant fraction of the bucket's samples have a
    non-productive app visible on another screen (multi-monitor, split-view,
    or Picture-in-Picture).

    Uses DISTRACTION_MIN_RATIO — e.g. 0.3 means ≥ 30% of samples must be
    flagged for the bucket to be considered "distracted."
    """
    if not events:
        return False
    flagged = sum(1 for e in events if getattr(e, "distraction_visible", False))
    ratio = flagged / len(events)
    return ratio >= cfg.DISTRACTION_MIN_RATIO


def _confidence(
    total_keystrokes: int,
    total_clicks: int,
    total_mouse_distance: float,
    max_idle: float,
    event_count: int,
    bucket_size: int,
    cfg: Config,
    distracted: bool = False,
) -> float:
    """
    Compute confidence ∈ [0, 1].

    Formula blends:
      • interaction density  — keystrokes + clicks relative to threshold
      • presence signal      — mouse movement relative to movement threshold
      • coverage             — did the tracker produce samples for the full bucket?
      • idle penalty         — long idle → lower confidence
      • distraction penalty  — visible non-productive app on another screen → ×0.8

    Weights: 35% density, 20% presence, 25% coverage, 20% idle penalty.
    """
    if event_count == 0:
        return 0.0

    interaction = total_keystrokes + total_clicks
    threshold = max(cfg.PRODUCTIVE_INTERACTION_THRESHOLD, 1)
    movement_thresh = max(cfg.MOUSE_MOVEMENT_THRESHOLD, 1)

    density = min(interaction / threshold, 1.0)
    presence = min(total_mouse_distance / movement_thresh, 1.0)
    coverage = min(event_count / max(bucket_size, 1), 1.0)
    idle_penalty = 1.0 - min(max_idle / max(bucket_size, 1), 1.0)

    raw = 0.35 * density + 0.20 * presence + 0.25 * coverage + 0.20 * idle_penalty

    # Distraction penalty: if a non-productive app is visible on another
    # screen, the user's focus is likely split → reduce confidence by 20%
    if distracted:
        raw *= 0.8

    return max(0.0, min(raw, 1.0))


# ── Main engine ─────────────────────────────────────────────────────

def bucketize(
    events: Sequence[TelemetryEvent],
    cfg: Config | None = None,
) -> list[Bucket]:
    """
    Slice *events* into fixed-width time buckets and infer productivity.

    Parameters
    ----------
    events : sequence of TelemetryEvent, assumed sorted by timestamp ASC.
    cfg    : Config instance (defaults to global Config()).

    Returns
    -------
    List of Bucket objects, one per time-window that contains ≥ 1 event.
    """
    if cfg is None:
        cfg = Config()

    if not events:
        return []

    bucket_size = cfg.BUCKET_SIZE_SEC

    # ── Group events into time-windows ──────────────────────────
    first_ts = events[0].timestamp
    buckets_map: dict[int, list[TelemetryEvent]] = {}
    for e in events:
        delta = (e.timestamp - first_ts).total_seconds()
        idx = int(delta // bucket_size)
        buckets_map.setdefault(idx, []).append(e)

    result: list[Bucket] = []

    for idx in sorted(buckets_map):
        evts = buckets_map[idx]
        b_start = first_ts + timedelta(seconds=idx * bucket_size)
        b_end = b_start + timedelta(seconds=bucket_size)

        total_keystrokes = sum(e.keystroke_count for e in evts)
        total_clicks = sum(e.mouse_clicks for e in evts)
        total_mouse_distance = sum(e.mouse_distance for e in evts)
        # Cap each sample's idle at bucket_size — defensive against inflated
        # values from system wake (sleep/hibernate/lid-close).  A single
        # sample's idle should never exceed the bucket it belongs to.
        max_idle = max(
            (min(e.idle_seconds, bucket_size) for e in evts),
            default=0.0,
        )
        dom_app, dom_title = _dominant(evts)

        # Check once: is a non-productive app visible on another screen?
        distracted = _has_distraction(evts, cfg)

        # ── Determine state ─────────────────────────────────────
        # Rule 1: Non-productive apps are ALWAYS non-productive
        if _is_non_productive_app(dom_app, dom_title, cfg):
            state = "non_productive"
        # Rule 2: Meeting apps are ALWAYS productive
        #   Zoom, Teams, Meet, etc. — you talk/listen, not type
        elif _is_meeting_app(dom_app, dom_title, cfg):
            state = "productive"
        # Rule 3: Interaction meets threshold → productive
        #   BUT check for auto-clicker/key repeater patterns first
        #   Distraction on another screen does NOT override real typing/clicking
        #   (confidence is penalized instead)
        elif _meets_threshold(total_keystrokes, total_clicks, cfg):
            if _is_suspicious_pattern(evts, cfg):
                # Looks like a bot — metronomic, no natural pauses
                state = "non_productive"
            else:
                state = "productive"
        # Rule 4: Active presence (reading/reviewing) → productive
        #   Mouse movement + low idle + sustained across samples
        #   e.g. reading code in Cursor, reviewing a PR, scrolling docs
        #   BLOCKED if a distraction is visible — mouse movement is ambiguous
        #   when YouTube/Netflix is on another screen; user is likely watching,
        #   not reading code.
        elif (
            not distracted
            and _is_actively_present(total_mouse_distance, max_idle, evts, cfg)
        ):
            state = "productive"
        # Rule 5: No interaction AND no presence → non-productive
        #   e.g. Cursor open but user is on phone watching reels
        else:
            state = "non_productive"

        conf = _confidence(
            total_keystrokes, total_clicks, total_mouse_distance,
            max_idle, len(evts), bucket_size, cfg, distracted,
        )

        result.append(
            Bucket(
                start=b_start,
                end=b_end,
                state=state,
                confidence=conf,
                total_keystrokes=total_keystrokes,
                total_clicks=total_clicks,
                total_mouse_distance=total_mouse_distance,
                max_idle=max_idle,
                dominant_app=dom_app,
                dominant_title=dom_title,
                event_count=len(evts),
            )
        )

    return result


def summarize_buckets(buckets: list[Bucket]) -> dict:
    """
    Aggregate bucket list into a summary dict:
      { productive: <sec>, non_productive: <sec>,
        total_seconds: <sec>, total_buckets: <int> }
    """
    summary: dict[str, float] = {s: 0.0 for s in STATES}
    for b in buckets:
        duration = (b.end - b.start).total_seconds()
        summary[b.state] = summary.get(b.state, 0.0) + duration

    total = sum(summary.values())
    return {
        **{k: int(v) for k, v in summary.items()},
        "total_seconds": int(total),
        "total_buckets": len(buckets),
    }


def _is_browser(app_name: str, cfg: Config) -> bool:
    """Return True if the app is a web browser (per BROWSER_APPS config)."""
    name_lower = app_name.lower()
    return any(b in name_lower for b in cfg.BROWSER_APPS)


def _extract_site_label(window_title: str, cfg: Config) -> str:
    """
    Extract a human-readable site/service name from a browser window title.

    Strategy (first match wins):
      1. Check against NON_PRODUCTIVE_APPS patterns → return the matched keyword
         e.g. "Funny Cat Video - YouTube" → "YouTube"
      2. Check against MEETING_APPS patterns → return the matched keyword
         e.g. "Google Meet - meeting" → "Google Meet"
      3. Fallback: split on common title delimiters and take the last segment
         e.g. "Pull Request #42 · my-repo · GitHub" → "GitHub"
         e.g. "Inbox (3) - Gmail" → "Gmail"
      4. Last resort: truncate to 40 chars
    """
    if not window_title or not window_title.strip():
        return "Other"

    title_lower = window_title.lower()

    # 1. Known non-productive sites
    for pattern in cfg.NON_PRODUCTIVE_APPS:
        if pattern in title_lower:
            return pattern.capitalize()

    # 2. Known meeting sites
    for pattern in cfg.MEETING_APPS:
        if pattern in title_lower:
            return pattern.title()

    # 3. Delimiter-based extraction (last segment is usually the site name)
    for delimiter in [" - ", " — ", " | ", " · "]:
        if delimiter in window_title:
            segment = window_title.rsplit(delimiter, 1)[-1].strip()
            if segment:
                return segment[:40]

    # 4. Last resort
    return window_title[:40] if len(window_title) > 40 else window_title


def app_breakdown(buckets: list[Bucket], cfg: Config | None = None) -> list[dict]:
    """
    Per-app breakdown: for each dominant_app, return time in each state.

    For browser apps (Chrome, Safari, etc.), entries are split by website
    using the window title, e.g. "Safari — YouTube", "Chrome — GitHub".
    """
    if cfg is None:
        cfg = Config()

    from collections import defaultdict

    apps: dict[str, dict] = defaultdict(lambda: {s: 0.0 for s in STATES})
    for b in buckets:
        duration = (b.end - b.start).total_seconds()

        if _is_browser(b.dominant_app, cfg):
            site = _extract_site_label(b.dominant_title, cfg)
            key = f"{b.dominant_app} — {site}"
        else:
            key = b.dominant_app

        apps[key][b.state] += duration

    result = []
    for app_name, states in sorted(apps.items(), key=lambda x: -sum(x[1].values())):
        total = sum(states.values())
        productive_time = states.get("productive", 0)
        category = "productive" if productive_time > total / 2 else "non_productive"
        result.append({
            "app_name": app_name,
            "category": category,
            "total_seconds": int(total),
            "states": {k: int(v) for k, v in states.items()},
        })
    return result
