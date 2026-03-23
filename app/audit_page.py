"""Audit Log — paginated viewer for audit_log entries."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from app.auth import login_required
from app.db import get_db

audit_page_bp = Blueprint("audit_page", __name__)

PER_PAGE = 50


# ---------------------------------------------------------------------------
# UI route
# ---------------------------------------------------------------------------

@audit_page_bp.route("/audit")
@login_required
def ui_list():
    db = get_db()

    action_filter = request.args.get("action", "")

    # Distinct action types for filter dropdown
    action_types = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        ).fetchall()
    ]

    # Build query with optional filter
    if action_filter:
        rows = db.execute(
            "SELECT id, ts, username, action, target, status "
            "FROM audit_log WHERE action = ? ORDER BY id DESC",
            (action_filter,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, ts, username, action, target, status "
            "FROM audit_log ORDER BY id DESC"
        ).fetchall()

    total = len(rows)

    page = request.args.get("page", 1, type=int)
    page = max(1, page)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * PER_PAGE
    end = start + PER_PAGE
    entries = [dict(r) for r in rows[start:end]]

    return render_template(
        "audit_log.html",
        entries=entries,
        page=page,
        total_pages=total_pages,
        total=total,
        action_filter=action_filter,
        action_types=action_types,
    )
