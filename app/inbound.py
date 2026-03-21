"""Inbound Route Flow Editor — API + UI routes."""

from __future__ import annotations

import json
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import generate_inbound_flow, write_inbound_flow

inbound_bp = Blueprint("inbound", __name__)

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9 _-]{0,63}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_route(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []
    name = data.get("name", "").strip()
    if is_new:
        if not name:
            errors.append("Route name is required.")
        elif not NAME_RE.match(name):
            errors.append("Name must start with a letter, max 64 chars.")

    open_target = data.get("open_target", "").strip()
    if not open_target:
        errors.append("Open target extension is required.")
    elif not re.match(r"^\d{3,6}$", open_target):
        errors.append("Open target must be a 3-6 digit extension number.")

    closed_announcement = data.get("closed_announcement", "").strip()
    if not closed_announcement:
        errors.append("Closed announcement is required.")

    time_group_id = data.get("time_group_id")
    if time_group_id is None or str(time_group_id) == "":
        errors.append("Time group is required.")

    return errors


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-inbound-apply",
        writers=[write_inbound_flow],
        reload_commands=["dialplan reload"],
    )


def _route_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_select_options():
    """Fetch options for form select fields."""
    db = get_db()
    extensions = db.execute(
        "SELECT ext, callerid_name FROM extensions WHERE enabled = 1 ORDER BY ext"
    ).fetchall()
    time_groups = db.execute(
        "SELECT id, name FROM time_groups ORDER BY name"
    ).fetchall()
    announcements = db.execute(
        "SELECT id, key_name, active FROM announcements ORDER BY key_name"
    ).fetchall()
    blast_configs = db.execute(
        "SELECT id, mailbox_list, voicemail_flags FROM blast_config ORDER BY id"
    ).fetchall()
    return {
        "extensions": extensions,
        "time_groups": time_groups,
        "announcements": announcements,
        "blast_configs": blast_configs,
    }


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/inbound-routes
# ---------------------------------------------------------------------------

@inbound_bp.route("/api/v1/inbound-routes", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute(
        "SELECT r.*, t.name AS tg_name "
        "FROM inbound_routes r "
        "LEFT JOIN time_groups t ON r.time_group_id = t.id "
        "ORDER BY r.id"
    ).fetchall()
    return jsonify([_route_to_dict(r) for r in rows])


@inbound_bp.route("/api/v1/inbound-routes", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    errors = _validate_route(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()

    if db.execute("SELECT 1 FROM inbound_routes WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"Route '{name}' already exists."]}), 409

    db.execute(
        "INSERT INTO inbound_routes (name, open_target, closed_announcement, "
        "blast_profile, spam_family, fixed_holiday_family, variable_holiday_family, "
        "time_group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            data.get("open_target", "").strip(),
            data.get("closed_announcement", "").strip(),
            data.get("blast_profile") or None,
            data.get("spam_family", "spam-prefix").strip(),
            data.get("fixed_holiday_family", "holidays-fixed").strip(),
            data.get("variable_holiday_family", "holidays-variable").strip(),
            data.get("time_group_id"),
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("inbound_route_create", target=name, after=data,
               username=username, status=status)

    result = db.execute("SELECT * FROM inbound_routes WHERE name = ?", (name,)).fetchone()
    code = 201 if ok else 207
    return jsonify({"route": _route_to_dict(result), "applied": ok, "message": msg}), code


@inbound_bp.route("/api/v1/inbound-routes/<int:route_id>", methods=["GET"])
@login_required
def api_get(route_id):
    db = get_db()
    row = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_route_to_dict(row))


@inbound_bp.route("/api/v1/inbound-routes/<int:route_id>", methods=["PUT"])
@login_required
def api_update(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["name"] = existing["name"]  # name is immutable
    errors = _validate_route(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _route_to_dict(existing)

    db.execute(
        "UPDATE inbound_routes SET open_target=?, closed_announcement=?, "
        "blast_profile=?, spam_family=?, fixed_holiday_family=?, "
        "variable_holiday_family=?, time_group_id=? WHERE id=?",
        (
            data.get("open_target", existing["open_target"]).strip(),
            data.get("closed_announcement", existing["closed_announcement"]).strip(),
            data.get("blast_profile") or existing["blast_profile"],
            data.get("spam_family", existing["spam_family"]).strip(),
            data.get("fixed_holiday_family", existing["fixed_holiday_family"]).strip(),
            data.get("variable_holiday_family", existing["variable_holiday_family"]).strip(),
            data.get("time_group_id", existing["time_group_id"]),
            route_id,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("inbound_route_update", target=existing["name"],
               before=before, after=data, username=username, status=status)

    result = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    return jsonify({"route": _route_to_dict(result), "applied": ok, "message": msg})


@inbound_bp.route("/api/v1/inbound-routes/<int:route_id>", methods=["DELETE"])
@login_required
def api_delete(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = _route_to_dict(existing)
    db.execute("DELETE FROM inbound_routes WHERE id = ?", (route_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("inbound_route_delete", target=before["name"], before=before,
               username=username, status=status)

    return jsonify({"deleted": before["name"], "applied": ok, "message": msg})


@inbound_bp.route("/api/v1/inbound-routes/<int:route_id>/preview", methods=["GET"])
@login_required
def api_preview(route_id):
    """Return the generated dialplan text for preview."""
    db = get_db()
    existing = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404
    content = generate_inbound_flow()
    return jsonify({"dialplan": content})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@inbound_bp.route("/inbound-routes")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute(
        "SELECT r.*, t.name AS tg_name, b.mailbox_list, b.voicemail_flags "
        "FROM inbound_routes r "
        "LEFT JOIN time_groups t ON r.time_group_id = t.id "
        "LEFT JOIN blast_config b ON r.blast_profile = b.id "
        "ORDER BY r.id"
    ).fetchall()
    return render_template("inbound_list.html", routes=rows)


@inbound_bp.route("/inbound-routes/new", methods=["GET", "POST"])
@login_required
def ui_new():
    db = get_db()
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "open_target": request.form.get("open_target", "").strip(),
            "closed_announcement": request.form.get("closed_announcement", "").strip(),
            "blast_profile": request.form.get("blast_profile", "").strip() or None,
            "spam_family": request.form.get("spam_family", "spam-prefix").strip(),
            "fixed_holiday_family": request.form.get("fixed_holiday_family", "holidays-fixed").strip(),
            "variable_holiday_family": request.form.get("variable_holiday_family", "holidays-variable").strip(),
            "time_group_id": request.form.get("time_group_id", "").strip() or None,
        }
        if data["blast_profile"]:
            data["blast_profile"] = int(data["blast_profile"])
        if data["time_group_id"]:
            data["time_group_id"] = int(data["time_group_id"])

        errors = _validate_route(data, is_new=True)
        if db.execute("SELECT 1 FROM inbound_routes WHERE name = ?", (data["name"],)).fetchone():
            errors.append(f"Route '{data['name']}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            opts = _get_select_options()
            return render_template("inbound_form.html", route=data, is_new=True, **opts)

        db.execute(
            "INSERT INTO inbound_routes (name, open_target, closed_announcement, "
            "blast_profile, spam_family, fixed_holiday_family, variable_holiday_family, "
            "time_group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"], data["open_target"], data["closed_announcement"],
                data["blast_profile"], data["spam_family"],
                data["fixed_holiday_family"], data["variable_holiday_family"],
                data["time_group_id"],
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("inbound_route_create", target=data["name"], after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("inbound.ui_list"))

    # GET — blank form with defaults
    defaults = {
        "name": "", "open_target": "4900", "closed_announcement": "custom-closed",
        "blast_profile": 1, "spam_family": "spam-prefix",
        "fixed_holiday_family": "holidays-fixed",
        "variable_holiday_family": "holidays-variable",
        "time_group_id": None,
    }
    # Auto-select the first time group if available
    tg = db.execute("SELECT id FROM time_groups ORDER BY id LIMIT 1").fetchone()
    if tg:
        defaults["time_group_id"] = tg["id"]

    opts = _get_select_options()
    return render_template("inbound_form.html", route=defaults, is_new=True, **opts)


@inbound_bp.route("/inbound-routes/<int:route_id>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        flash("Route not found.", "danger")
        return redirect(url_for("inbound.ui_list"))

    if request.method == "POST":
        data = {
            "name": existing["name"],
            "open_target": request.form.get("open_target", "").strip(),
            "closed_announcement": request.form.get("closed_announcement", "").strip(),
            "blast_profile": request.form.get("blast_profile", "").strip() or None,
            "spam_family": request.form.get("spam_family", "spam-prefix").strip(),
            "fixed_holiday_family": request.form.get("fixed_holiday_family", "holidays-fixed").strip(),
            "variable_holiday_family": request.form.get("variable_holiday_family", "holidays-variable").strip(),
            "time_group_id": request.form.get("time_group_id", "").strip() or None,
        }
        if data["blast_profile"]:
            data["blast_profile"] = int(data["blast_profile"])
        if data["time_group_id"]:
            data["time_group_id"] = int(data["time_group_id"])

        errors = _validate_route(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            data["id"] = route_id
            opts = _get_select_options()
            return render_template("inbound_form.html", route=data, is_new=False, **opts)

        before = _route_to_dict(existing)
        db.execute(
            "UPDATE inbound_routes SET open_target=?, closed_announcement=?, "
            "blast_profile=?, spam_family=?, fixed_holiday_family=?, "
            "variable_holiday_family=?, time_group_id=? WHERE id=?",
            (
                data["open_target"], data["closed_announcement"],
                data["blast_profile"], data["spam_family"],
                data["fixed_holiday_family"], data["variable_holiday_family"],
                data["time_group_id"], route_id,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("inbound_route_update", target=existing["name"],
                   before=before, after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("inbound.ui_list"))

    route = _route_to_dict(existing)
    opts = _get_select_options()
    return render_template("inbound_form.html", route=route, is_new=False, **opts)


@inbound_bp.route("/inbound-routes/<int:route_id>/delete", methods=["POST"])
@login_required
def ui_delete(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM inbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        flash("Route not found.", "danger")
        return redirect(url_for("inbound.ui_list"))

    before = _route_to_dict(existing)
    db.execute("DELETE FROM inbound_routes WHERE id = ?", (route_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("inbound_route_delete", target=before["name"], before=before,
               username=username, status="ok" if ok else "error")
    flash(f"Route '{before['name']}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("inbound.ui_list"))


@inbound_bp.route("/inbound-routes/preview")
@login_required
def ui_preview():
    """Show the current generated dialplan text."""
    content = generate_inbound_flow()
    return render_template("inbound_preview.html", dialplan=content)
