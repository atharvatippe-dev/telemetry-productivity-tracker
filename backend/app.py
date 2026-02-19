"""
Flask application — REST API for the Productivity Tracker.

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
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

from backend.config import Config
from backend.models import db, TelemetryEvent
from backend.productivity import bucketize, summarize_buckets, app_breakdown, STATES

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend")


def create_app(config: Config | None = None) -> Flask:
    """Application factory."""
    app = Flask(__name__)

    if config is None:
        config = Config()

    app.config.from_object(config)
    # Store config for easy access in routes
    app.tracker_config = config  # type: ignore[attr-defined]

    # ── Extensions ──────────────────────────────────────────────
    CORS(app)
    db.init_app(app)

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


def _register_routes(app: Flask) -> None:
    """Register all API routes on the app."""

    # ── POST /track ─────────────────────────────────────────────
    @app.route("/track", methods=["POST"])
    def track():
        """
        Ingest a batch of telemetry events.

        Expects JSON:
          { "events": [ { timestamp, app_name, window_title,
                          keystroke_count, mouse_clicks,
                          mouse_distance, idle_seconds }, ... ] }

        Returns 201 on success.
        """
        data = request.get_json(silent=True)
        if not data or "events" not in data:
            return jsonify({"error": "Missing 'events' array in payload"}), 400

        events_raw = data["events"]
        if not isinstance(events_raw, list):
            return jsonify({"error": "'events' must be a list"}), 400

        created = 0
        for raw in events_raw:
            try:
                ts = raw.get("timestamp")
                if isinstance(ts, str):
                    # Accept ISO format; fall back to now
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    ts = datetime.now(timezone.utc)

                event = TelemetryEvent(
                    timestamp=ts,
                    user_id=raw.get("user_id", "default"),
                    app_name=raw.get("app_name", "unknown"),
                    window_title=raw.get("window_title", ""),
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

    # ── helper: build a base query filtered by time + optional user_id ─
    def _base_query(start, end, user_id=None):
        q = TelemetryEvent.query.filter(
            TelemetryEvent.timestamp >= start,
            TelemetryEvent.timestamp < end,
        )
        if user_id:
            q = q.filter(TelemetryEvent.user_id == user_id)
        return q.order_by(TelemetryEvent.timestamp.asc())

    # ── GET /summary/today ──────────────────────────────────────
    @app.route("/summary/today", methods=["GET"])
    def summary_today():
        """
        Return productivity state totals for today.
        Optional query param: ?user_id=<id>
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        user_id = request.args.get("user_id")
        start, end = _today_range(cfg)
        events = _base_query(start, end, user_id).all()
        buckets = bucketize(events, cfg)
        summary = summarize_buckets(buckets)
        return jsonify(summary), 200

    # ── GET /apps ───────────────────────────────────────────────
    @app.route("/apps", methods=["GET"])
    def apps():
        """
        Per-app breakdown for today.
        Optional query param: ?user_id=<id>
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        user_id = request.args.get("user_id")
        start, end = _today_range(cfg)
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
        Return all users with their productivity stats for today,
        sorted by non-productive percentage descending (worst offenders first).

        Response: [
          { user_id, productive_sec, non_productive_sec,
            total_sec, productive_pct, non_productive_pct }, ...
        ]
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _today_range(cfg)

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
        Return non-productive app breakdown for a specific user today.

        Response: [ { app_name, seconds }, ... ]
        sorted by seconds descending.
        """
        cfg = app.tracker_config  # type: ignore[attr-defined]
        start, end = _today_range(cfg)
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

    # ── DELETE /admin/user/<user_id> ────────────────────────────
    @app.route("/admin/user/<user_id>", methods=["DELETE"])
    def admin_delete_user(user_id: str):
        """Delete all telemetry events for a given user."""
        count = TelemetryEvent.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        logger.info("Deleted %d events for user %r.", count, user_id)
        return jsonify({"deleted": count, "user_id": user_id}), 200

    # ── GET /dashboard/<user_id> ─────────────────────────────────
    @app.route("/dashboard/<user_id>", methods=["GET"])
    def user_dashboard(user_id: str):
        """Serve a self-contained HTML dashboard for a specific user."""
        return render_template("dashboard.html", user_id=user_id)

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
