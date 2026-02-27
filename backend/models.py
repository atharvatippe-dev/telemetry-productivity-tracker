"""
SQLAlchemy models for telemetry storage and audit logging.

TelemetryEvent — one raw sample sent by the tracker agent.
AuditLog       — tamper-evident record of security-relevant actions.
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class AuditLog(db.Model):
    """
    Immutable record of a security-relevant action.

    Fields
    ------
    id          : auto-increment PK
    timestamp   : UTC datetime when the action occurred
    actor       : who performed it — admin username, device_id, or "system"
    action      : what happened — e.g. "delete_user", "login_failed", "retention_cleanup"
    target_user : which user was affected (if applicable)
    ip_address  : requester's IP address
    user_agent  : requester's browser or agent string
    detail      : optional extra context (e.g. reason for failure)
    """

    __tablename__ = "audit_log"

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    timestamp: datetime = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    actor: str = db.Column(db.String(256), nullable=False, default="unknown")
    action: str = db.Column(db.String(128), nullable=False, index=True)
    target_user: str = db.Column(db.String(128), nullable=True)
    ip_address: str = db.Column(db.String(64), nullable=True)
    user_agent: str = db.Column(db.String(512), nullable=True)
    detail: str = db.Column(db.String(1024), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "target_user": self.target_user,
            "ip_address": self.ip_address,
            "detail": self.detail,
        }

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"actor={self.actor!r} target={self.target_user!r}>"
        )


class TelemetryEvent(db.Model):
    """
    A single telemetry sample (≈ 1-second granularity from the tracker).

    Fields
    ------
    id                  : auto-increment PK
    user_id             : identifier for the employee (from .env on their laptop)
    timestamp           : UTC datetime of the sample
    app_name            : active application name (e.g. "Google Chrome")
    window_title        : window / tab title (may contain URL, doc name, etc.)
    keystroke_count     : number of keystrokes in the interval (count only, NO content)
    mouse_clicks        : number of mouse click events in the interval
    mouse_distance      : approximate mouse travel in pixels (optional; useful for density)
    idle_seconds        : seconds of inactivity at the time of sampling
    distraction_visible : True if a non-productive app is visible on another screen /
                          split-view / PiP (multi-monitor distraction detection)
    """

    __tablename__ = "telemetry_events"

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id: str = db.Column(db.String(128), nullable=False, default="default", server_default="default", index=True)
    timestamp: datetime = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    app_name: str = db.Column(db.String(256), nullable=False, default="unknown")
    window_title: str = db.Column(db.String(1024), nullable=False, default="")
    keystroke_count: int = db.Column(db.Integer, nullable=False, default=0)
    mouse_clicks: int = db.Column(db.Integer, nullable=False, default=0)
    mouse_distance: float = db.Column(db.Float, nullable=False, default=0.0)
    idle_seconds: float = db.Column(db.Float, nullable=False, default=0.0)
    distraction_visible: bool = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("false"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "app_name": self.app_name,
            "window_title": self.window_title,
            "keystroke_count": self.keystroke_count,
            "mouse_clicks": self.mouse_clicks,
            "mouse_distance": self.mouse_distance,
            "idle_seconds": self.idle_seconds,
            "distraction_visible": self.distraction_visible,
        }

    def __repr__(self) -> str:
        return (
            f"<TelemetryEvent id={self.id} user={self.user_id!r} app={self.app_name!r} "
            f"keys={self.keystroke_count} clicks={self.mouse_clicks} "
            f"idle={self.idle_seconds:.1f}s>"
        )
