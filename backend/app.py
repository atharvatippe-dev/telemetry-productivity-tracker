"""
Flask application — REST API for Zinnia Axion.

Endpoints
---------
POST /track                              — ingest telemetry events (batch)
GET  /summary/today[?user_id=]           — productivity state totals for today
GET  /apps[?user_id=]                    — per-app breakdown for today
GET  /daily?days=7[&user_id=]            — daily time-series of state totals
POST /cleanup                            — manually purge old events
GET  /db-stats                           — database size and retention info
GET  /dashboard/<user_id>                — self-contained HTML dashboard for a user
GET  /health                             — simple health check
GET  /admin/leaderboard                  — all users sorted by non-productive %
GET  /admin/user/<user_id>/non-productive-apps — non-productive apps for a user today
GET  /admin/tracker-status                  — online/offline status of all user trackers
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from backend.config import Config
from backend.models import db, TelemetryEvent
from backend.productivity import bucketize, summarize_buckets, app_breakdown, STATES
from backend.audit import log_action

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend")


def _check_production_config(config: Config) -> None:
    """Verify critical security settings when running in production mode.

    Raises SystemExit with a clear message if any required setting is missing.
    Called only when DEMO_MODE=false.
    """
    errors: list[str] = []

    if not config.SECRET_KEY:
        errors.append(
            "SECRET_KEY is not set. Flask needs it to sign session cookies securely. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    if not config.ADMIN_PASSWORD:
        errors.append(
            "ADMIN_PASSWORD is not set. A password is required for admin dashboard access "
            "in production mode."
        )

    uri = config.SQLALCHEMY_DATABASE_URI
    if uri.startswith("sqlite"):
        errors.append(
            f"DATABASE_URI is set to SQLite ({uri}). "
            "Production deployments should use PostgreSQL for reliability and concurrency."
        )

    if errors:
        logger.error("=" * 70)
        logger.error("PRODUCTION MODE STARTUP FAILED — missing required configuration:")
        logger.error("")
        for i, err in enumerate(errors, 1):
            logger.error("  %d. %s", i, err)
        logger.error("")
        logger.error("Fix these in your .env file, then restart.")
        logger.error("Or set DEMO_MODE=true to run without security enforcement.")
        logger.error("=" * 70)
        raise SystemExit(1)


def create_app(config: Config | None = None) -> Flask:
    """Application factory."""
    app = Flask(__name__)

    if config is None:
        config = Config()

    # ── Demo / Production mode gate ─────────────────────────────
    if config.DEMO_MODE:
        logger.warning("=" * 70)
        logger.warning(
            "DEMO MODE ACTIVE — authentication and access control are DISABLED."
        )
        logger.warning(
            "Set DEMO_MODE=false in .env before deploying to production."
        )
        logger.warning("=" * 70)
    else:
        _check_production_config(config)
        app.secret_key = config.SECRET_KEY
        logger.info("Production mode enabled — all security features enforced.")

    app.config.from_object(config)
    # Store config for easy access in routes
    app.tracker_config = config  # type: ignore[attr-defined]

    # ── Task 6.1: Request size limit ────────────────────────────
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_REQUEST_SIZE_KB * 1024

    # ── Extensions ──────────────────────────────────────────────
    CORS(app)
    db.init_app(app)

    # ── Task 6.3: Per-device rate limiting ──────────────────────
    def _rate_limit_key():
        """Use X-Device-Id header if present, otherwise fall back to IP."""
        return request.headers.get("X-Device-Id", get_remote_address())

    limiter = Limiter(
        key_func=_rate_limit_key,
        app=app,
        default_limits=[],
        storage_uri="memory://",
    )
    app.limiter = limiter  # type: ignore[attr-defined]

    with app.app_context():
        db.create_all()
        logger.info("Database tables ensured.")

        # ── Migrate: add missing columns (works with both SQLite and PostgreSQL) ──
        inspector = db.inspect(db.engine)
        existing_cols = {c["name"] for c in inspector.get_columns("telemetry_events")}

        if "distraction_visible" not in existing_cols:
            db.session.execute(
                db.text(
                    "ALTER TABLE telemetry_events "
                    "ADD COLUMN distraction_visible BOOLEAN NOT NULL DEFAULT false"
                )
            )
            db.session.commit()
            logger.info("Migration: added distraction_visible column.")

        if "user_id" not in existing_cols:
            db.session.execute(
                db.text(
                    "ALTER TABLE telemetry_events "
                    "ADD COLUMN user_id VARCHAR(128) NOT NULL DEFAULT 'default'"
                )
            )
            db.session.commit()
            logger.info("Migration: added user_id column.")

        # ── Auto-cleanup old events on startup ───────────────
        _run_cleanup(config)

    # ── Routes ──────────────────────────────────────────────────
    _register_routes(app)

    return app


def _run_cleanup(config: Config) -> int:
    """
    Delete events older than DATA_RETENTION_DAYS.
    Returns the number of rows deleted. Skipped if retention is 0 (disabled).
    """
    retention = config.DATA_RETENTION_DAYS
    if retention <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
    count = TelemetryEvent.query.filter(TelemetryEvent.timestamp < cutoff).delete()
    db.session.commit()

    if count > 0:
        logger.info(
            "Cleanup: deleted %d events older than %d days (before %s).",
            count, retention, cutoff.isoformat(),
        )
        log_action("system", "retention_cleanup",
                   detail=f"Deleted {count} events older than {retention}d")
    else:
        logger.info("Cleanup: no events older than %d days to delete.", retention)

    return count


def _get_local_tz(config: Config) -> ZoneInfo:
    """Return ZoneInfo for the configured TIMEZONE (falls back to UTC)."""
    try:
        return ZoneInfo(config.TIMEZONE)
    except (KeyError, Exception):
        return ZoneInfo("UTC")


def _today_range(config: Config) -> tuple[datetime, datetime]:
    """
    Return (start_of_today, start_of_tomorrow) as UTC datetimes,
    but using the configured local timezone for day boundaries.

    e.g. TIMEZONE=Asia/Kolkata → "today" starts at 00:00 IST = 18:30 prev-day UTC.
    """
    local_tz = _get_local_tz(config)
    now_local = datetime.now(local_tz)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    # Convert to UTC for database queries (events are stored in UTC)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _day_range(date_obj, config: Config) -> tuple[datetime, datetime]:
    """
    Return (start_of_day, start_of_next_day) as UTC datetimes for a given date,
    using the configured local timezone for day boundaries.
    """
    local_tz = _get_local_tz(config)
    start_local = datetime.combine(date_obj, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _validate_event(raw: dict) -> str | None:
    """Validate a single telemetry event dict.

    Returns an error message string if invalid, or None if valid.
    """
    if not isinstance(raw, dict):
        return "event must be a JSON object"

    ts = raw.get("timestamp")
    if ts is not None and not isinstance(ts, str):
        return f"timestamp must be an ISO 8601 string, got {type(ts).__name__}"

    app_name = raw.get("app_name")
    if app_name is not None and not isinstance(app_name, str):
        return f"app_name must be a string, got {type(app_name).__name__}"

    for int_field in ("keystroke_count", "mouse_clicks"):
        val = raw.get(int_field)
        if val is not None:
            if not isinstance(val, (int, float)):
                return f"{int_field} must be a number, got {type(val).__name__}"
            if val < 0:
                return f"{int_field} must be >= 0, got {val}"

    for num_field in ("mouse_distance", "idle_seconds"):
        val = raw.get(num_field)
        if val is not None:
            if not isinstance(val, (int, float)):
                return f"{num_field} must be a number, got {type(val).__name__}"
            if val < 0:
                return f"{num_field} must be >= 0, got {val}"

    return None


def _register_routes(app: Flask) -> None:
    """Register all API routes on the app."""

    limiter: Limiter = app.limiter  # type: ignore[attr-defined]
    cfg: Config = app.tracker_config  # type: ignore[attr-defined]

    # ── POST /track ─────────────────────────────────────────────
    @app.route("/track", methods=["POST"])
    @limiter.limit(cfg.RATE_LIMIT_PER_DEVICE)
    def track():
        """
        Ingest a batch of telemetry events.

        Expects JSON:
          { "events": [ { timestamp, app_name, window_title,
                          keystroke_count, mouse_clicks,
                          mouse_distance, idle_seconds }, ... ] }

        Returns 201 on success, 400 on validation failure,
        413 if payload too large, 429 if rate-limited.
        """
        data = request.get_json(silent=True)
        if not data or "events" not in data:
            return jsonify({"error": "Missing 'events' array in payload"}), 400

        events_raw = data["events"]
        if not isinstance(events_raw, list):
            return jsonify({"error": "'events' must be a list"}), 400

        # ── Task 6.2: Schema validation ──────────────────────────
        errors: list[str] = []
        for i, raw in enumerate(events_raw):
            err = _validate_event(raw)
            if err:
                errors.append(f"event[{i}]: {err}")
        if errors:
            return jsonify({"error": "Validation failed", "details": errors}), 400

        # ── Task 5.3: Server-side title drop ─────────────────────
        drop_titles = cfg.DROP_TITLES

        created = 0
        for raw in events_raw:
            try:
                ts = raw.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    ts = datetime.now(timezone.utc)

                title = "" if drop_titles else raw.get("window_title", "")

                event = TelemetryEvent(
                    timestamp=ts,
                    user_id=raw.get("user_id", "default"),
                    app_name=raw.get("app_name", "unknown"),
                    window_title=title,
                    keystroke_count=int(raw.get("keystroke_count", 0)),
                    mouse_clicks=int(raw.get("mouse_clicks", 0)),
                    mouse_distance=float(raw.get("mouse_distance", 0.0)),
                    idle_seconds=float(raw.get("idle_seconds", 0.0)),
                    distraction_visible=bool(raw.get("distraction_visible", False)),
                )
                db.session.add(event)
                created += 1
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping malformed event: %s — %s", raw, exc)

        db.session.commit()
        logger.info("Ingested %d / %d events.", created, len(events_raw))
        return jsonify({"ingested": created}), 201

    # ── Custom error handler for 413 (payload too large) ─────────
    @app.errorhandler(413)
    def too_large(e):
        max_kb = cfg.MAX_REQUEST_SIZE_KB
        device = request.headers.get("X-Device-Id", "unknown")
        log_action(device, "request_too_large",
                   detail=f"Exceeded {max_kb} KB limit")
        return jsonify({
            "error": f"Payload too large. Maximum allowed: {max_kb} KB."
        }), 413

    # ── Custom error handler for 429 (rate limited) ──────────────
    @app.errorhandler(429)
    def rate_limited(e):
        device = request.headers.get("X-Device-Id", "unknown")
        log_action(device, "rate_limited",
                   detail=str(e.description))
        return jsonify({
            "error": "Too many requests. Slow down.",
            "retry_after": e.description,
        }), 429

    # ── helper: build a base query filtered by time + optional user_id ─
    def _base_query(start, end, user_id=None):
        q = TelemetryEvent.query.filter(
            TelemetryEvent.timestamp >= start,
            TelemetryEvent.timestamp < end,
        )
        if user_id:
            q = q.filter(TelemetryEvent.user_id == user_id)
        return q.order_by(TelemetryEvent.timestamp.asc())

    # ── helper: resolve date range from ?date= param or default to today ─
    def _resolve_range(cfg):
        date_str = request.args.get("date")
        if date_str:
            from datetime import date as _date_cls
            try:
                d = _date_cls.fromisoformat(date_str)
                return _day_range(d, cfg)
            except ValueError:
                pass
        return _today_range(cfg)

    # ── GET /summary/today ──────────────────────────────────────
    @app.route("/summary/today", methods=["GET"])
    def summary_today():
        """
        Return productivity state totals for today (or ?date=YYYY-MM-DD).
        Optional query param: ?user_id=<id>
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        user_id = request.args.get("user_id")
        start, end = _resolve_range(cfg)
        events = _base_query(start, end, user_id).all()
        buckets = bucketize(events, cfg)
        summary = summarize_buckets(buckets)
        return jsonify(summary), 200

    # ── GET /apps ───────────────────────────────────────────────
    @app.route("/apps", methods=["GET"])
    def apps():
        """
        Per-app breakdown for today (or ?date=YYYY-MM-DD).
        Optional query param: ?user_id=<id>
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        user_id = request.args.get("user_id")
        start, end = _resolve_range(cfg)
        events = _base_query(start, end, user_id).all()
        buckets = bucketize(events, cfg)
        breakdown = app_breakdown(buckets, cfg)
        return jsonify(breakdown), 200

    # ── GET /daily?days=7 ───────────────────────────────────────
    @app.route("/daily", methods=["GET"])
    def daily():
        """
        Daily time-series of state totals.
        Optional query params: ?days=7&user_id=<id>
        """
        try:
            num_days = int(request.args.get("days", 7))
        except ValueError:
            num_days = 7

        user_id = request.args.get("user_id")
        cfg = app.tracker_config  # type: ignore[attr-defined]
        local_tz = _get_local_tz(cfg)
        today = datetime.now(local_tz).date()
        series: list[dict] = []

        for offset in range(num_days - 1, -1, -1):
            day = today - timedelta(days=offset)
            start, end = _day_range(day, cfg)
            events = _base_query(start, end, user_id).all()
            buckets = bucketize(events, cfg)
            summary = summarize_buckets(buckets)
            summary["date"] = day.isoformat()
            series.append(summary)

        return jsonify(series), 200

    # ── POST /cleanup ──────────────────────────────────────────
    @app.route("/cleanup", methods=["POST"])
    def cleanup():
        """
        Manually trigger cleanup of old events.

        Optional JSON body: { "days": 14 }
        If not provided, uses DATA_RETENTION_DAYS from config.
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]

        data = request.get_json(silent=True)
        if data and "days" in data:
            try:
                override_days = int(data["days"])
                # Temporarily override for this call
                original = cfg.DATA_RETENTION_DAYS
                cfg.DATA_RETENTION_DAYS = override_days
                deleted = _run_cleanup(cfg)
                cfg.DATA_RETENTION_DAYS = original
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid 'days' value"}), 400
        else:
            deleted = _run_cleanup(cfg)

        log_action("admin", "manual_cleanup",
                   detail=f"Deleted {deleted} events (retention={cfg.DATA_RETENTION_DAYS}d)")
        return jsonify({"deleted": deleted, "retention_days": cfg.DATA_RETENTION_DAYS}), 200

    # ── GET /db-stats ────────────────────────────────────────────
    @app.route("/db-stats", methods=["GET"])
    def db_stats():
        """
        Return database statistics: total events, date range, estimated size.
        Useful for monitoring database growth.
        """
        total = TelemetryEvent.query.count()
        oldest = TelemetryEvent.query.order_by(TelemetryEvent.timestamp.asc()).first()
        newest = TelemetryEvent.query.order_by(TelemetryEvent.timestamp.desc()).first()

        cfg = app.tracker_config  # type: ignore[attr-defined]
        return jsonify({
            "total_events": total,
            "oldest_event": oldest.timestamp.isoformat() if oldest else None,
            "newest_event": newest.timestamp.isoformat() if newest else None,
            "retention_days": cfg.DATA_RETENTION_DAYS,
            "estimated_size_mb": round(total * 0.0002, 2),  # ~200 bytes per row
        }), 200

    # ── GET /admin/leaderboard ───────────────────────────────────
    @app.route("/admin/leaderboard", methods=["GET"])
    def admin_leaderboard():
        """
        Return all users with their productivity stats for today
        (or ?date=YYYY-MM-DD), sorted by non-productive % descending.

        Response: [
          { user_id, productive_sec, non_productive_sec,
            total_sec, productive_pct, non_productive_pct }, ...
        ]
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _resolve_range(cfg)

        # Fetch all today's events
        all_events = (
            TelemetryEvent.query
            .filter(TelemetryEvent.timestamp >= start, TelemetryEvent.timestamp < end)
            .order_by(TelemetryEvent.timestamp.asc())
            .all()
        )

        # Group by user_id
        from collections import defaultdict
        user_events: dict[str, list] = defaultdict(list)
        for ev in all_events:
            user_events[ev.user_id].append(ev)

        rows: list[dict] = []
        for uid, events in user_events.items():
            buckets = bucketize(events, cfg)
            summary = summarize_buckets(buckets)
            total = summary.get("total_seconds", 0)
            prod = summary.get("productive", 0)
            non_prod = summary.get("non_productive", 0)
            rows.append({
                "user_id": uid,
                "productive_sec": prod,
                "non_productive_sec": non_prod,
                "total_sec": total,
                "productive_pct": round(prod / total * 100, 1) if total else 0.0,
                "non_productive_pct": round(non_prod / total * 100, 1) if total else 0.0,
            })

        # Sort by non-productive % descending
        rows.sort(key=lambda r: r["non_productive_pct"], reverse=True)
        return jsonify(rows), 200

    # ── GET /admin/user/<user_id>/non-productive-apps ─────────
    @app.route("/admin/user/<user_id>/non-productive-apps", methods=["GET"])
    def admin_user_non_productive_apps(user_id: str):
        """
        Return non-productive app breakdown for a specific user
        for today (or ?date=YYYY-MM-DD).

        Response: [ { app_name, seconds }, ... ]
        sorted by seconds descending.
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _resolve_range(cfg)
        events = _base_query(start, end, user_id).all()
        buckets = bucketize(events, cfg)
        breakdown = app_breakdown(buckets, cfg)

        # Extract only non-productive entries
        np_apps: list[dict] = []
        for entry in breakdown:
            np_secs = entry.get("states", {}).get("non_productive", 0)
            if np_secs > 0:
                np_apps.append({
                    "app_name": entry["app_name"],
                    "seconds": np_secs,
                })

        np_apps.sort(key=lambda r: r["seconds"], reverse=True)
        return jsonify(np_apps), 200

    # ── GET /admin/user/<user_id>/app-breakdown ────────────────
    @app.route("/admin/user/<user_id>/app-breakdown", methods=["GET"])
    def admin_user_app_breakdown(user_id: str):
        """
        Full per-app breakdown with productive and non-productive seconds
        for today (or ?date=YYYY-MM-DD).

        Response: [ { app_name, productive, non_productive, total, category }, ... ]
        sorted by total seconds descending.
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _resolve_range(cfg)
        events = _base_query(start, end, user_id).all()
        buckets = bucketize(events, cfg)
        breakdown = app_breakdown(buckets, cfg)

        result = []
        for entry in breakdown:
            states = entry.get("states", {})
            p = states.get("productive", 0)
            np = states.get("non_productive", 0)
            total = p + np
            if total > 0:
                result.append({
                    "app_name": entry["app_name"],
                    "productive": p,
                    "non_productive": np,
                    "total": total,
                    "category": entry.get("category", "non_productive"),
                })
        result.sort(key=lambda r: r["total"], reverse=True)
        return jsonify(result), 200

    # ── DELETE /admin/user/<user_id> ────────────────────────────
    @app.route("/admin/user/<user_id>", methods=["DELETE"])
    def admin_delete_user(user_id: str):
        """Delete all telemetry events for a given user."""
        count = TelemetryEvent.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        logger.info("Deleted %d events for user %r.", count, user_id)
        log_action("admin", "delete_user", target_user=user_id,
                   detail=f"Deleted {count} events")
        return jsonify({"deleted": count, "user_id": user_id}), 200

    # ── GET /admin/tracker-status ────────────────────────────────
    @app.route("/admin/tracker-status", methods=["GET"])
    def admin_tracker_status():
        """
        Return online/offline status for every user that has data today.

        A tracker is considered "online" if its last event is within
        TRACKER_ONLINE_THRESHOLD seconds (default 60s — generous enough
        to cover a batch interval + network jitter).

        Response: [
          { user_id, last_seen, seconds_ago, status: "online"|"offline" }, ...
        ]
        """
        threshold = int(request.args.get("threshold", 60))
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _today_range(cfg)

        # DB stores naive local-time timestamps — compare with naive local now
        local_tz = _get_local_tz(cfg)
        now_naive = datetime.now(local_tz).replace(tzinfo=None)

        rows_raw = (
            db.session.query(
                TelemetryEvent.user_id,
                db.func.max(TelemetryEvent.timestamp).label("last_seen"),
            )
            .filter(TelemetryEvent.timestamp >= start, TelemetryEvent.timestamp < end)
            .group_by(TelemetryEvent.user_id)
            .all()
        )

        rows = []
        for uid, last_seen in rows_raw:
            ago = (now_naive - last_seen).total_seconds()
            rows.append({
                "user_id": uid,
                "last_seen": last_seen.isoformat(),
                "seconds_ago": round(ago),
                "status": "online" if ago <= threshold else "offline",
            })

        rows.sort(key=lambda r: r["seconds_ago"])
        return jsonify(rows), 200

    # ── GET /dashboard/<user_id> ─────────────────────────────────
    @app.route("/dashboard/<user_id>", methods=["GET"])
    def user_dashboard(user_id: str):
        """Serve a self-contained HTML dashboard for a specific user."""
        log_action("visitor", "view_dashboard", target_user=user_id)
        return render_template("dashboard.html", user_id=user_id)

    # ── GET /admin/audit-log ────────────────────────────────────
    @app.route("/admin/audit-log", methods=["GET"])
    def admin_audit_log():
        """Return the most recent audit log entries.

        Optional query params: ?limit=50&action=delete_user
        """
        from backend.models import AuditLog
        limit = min(int(request.args.get("limit", 100)), 500)
        q = AuditLog.query.order_by(AuditLog.timestamp.desc())

        action_filter = request.args.get("action")
        if action_filter:
            q = q.filter(AuditLog.action == action_filter)

        entries = q.limit(limit).all()
        return jsonify([e.to_dict() for e in entries]), 200

    # ── GET /health ─────────────────────────────────────────────
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200


# ── Entrypoint for `python -m backend.app` ──────────────────────────
if __name__ == "__main__":
    cfg = Config()
    application = create_app(cfg)
    logger.info("Starting Flask on %s:%s", cfg.FLASK_HOST, cfg.FLASK_PORT)
    application.run(host=cfg.FLASK_HOST, port=cfg.FLASK_PORT, debug=True)
