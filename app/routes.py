from flask import Blueprint, jsonify, redirect, render_template, url_for

from app.db import get_db
from app.auth import login_required

core_bp = Blueprint("core", __name__)


@core_bp.route("/health")
def health():
    db = get_db()
    row = db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return jsonify(status="ok", schema_version=row["v"] if row else 0)


@core_bp.route("/")
@login_required
def index():
    return redirect(url_for("system.dashboard"))


@core_bp.route("/api/v1/audit", methods=["GET"])
@login_required
def audit_recent():
    db = get_db()
    rows = db.execute(
        "SELECT id, ts, username, action, target, status "
        "FROM audit_log ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return jsonify([dict(r) for r in rows])
