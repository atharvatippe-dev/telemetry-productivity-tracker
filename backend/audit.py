"""
Audit logging helper — one-liner to record security-relevant actions.

Usage from any Flask route:

    from backend.audit import log_action
    log_action("admin", "delete_user", target_user="User_32")
"""

from __future__ import annotations

import logging
from flask import request, has_request_context
from backend.models import db, AuditLog

logger = logging.getLogger("backend.audit")


def log_action(
    actor: str,
    action: str,
    *,
    target_user: str | None = None,
    detail: str | None = None,
) -> None:
    """Insert one row into the audit_log table.

    Automatically captures IP address and User-Agent from the current
    Flask request context (if available).
    """
    ip = None
    ua = None
    if has_request_context():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        ua = request.headers.get("User-Agent", "")[:512]

    entry = AuditLog(
        actor=actor,
        action=action,
        target_user=target_user,
        ip_address=ip,
        user_agent=ua,
        detail=detail[:1024] if detail else None,
    )
    db.session.add(entry)
    db.session.commit()

    logger.info(
        "AUDIT | %s | actor=%s target=%s | %s",
        action, actor, target_user or "-", detail or "",
    )
