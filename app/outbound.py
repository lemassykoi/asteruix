"""Outbound Routes — API + UI routes."""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import generate_outbound_routes, write_outbound_routes

outbound_bp = Blueprint("outbound", __name__)

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _/-]{0,63}$")
PATTERN_RE = re.compile(r"^[_0-9XZ N\[\]\-\!\.\+\*]+$")


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
            errors.append("Name must start with a letter or digit, max 64 chars.")

    pattern = data.get("pattern", "").strip()
    if not pattern:
        errors.append("Dial pattern is required.")
    elif not PATTERN_RE.match(pattern):
        errors.append("Pattern contains invalid characters.")

    trunk_name = data.get("trunk_name", "").strip()
    if not trunk_name:
        errors.append("Primary trunk is required.")

    priority = data.get("priority")
    if priority is not None:
        try:
            p = int(priority)
            if p < 1 or p > 999:
                errors.append("Priority must be between 1 and 999.")
        except (ValueError, TypeError):
            errors.append("Priority must be a number.")

    return errors


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-outbound-apply",
        writers=[write_outbound_routes],
        reload_commands=["dialplan reload"],
    )


def _route_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_trunks():
    """Fetch enabled trunks for select dropdowns."""
    db = get_db()
    return db.execute(
        "SELECT name, did FROM trunks WHERE enabled = 1 ORDER BY name"
    ).fetchall()


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/outbound-routes
# ---------------------------------------------------------------------------

@outbound_bp.route("/api/v1/outbound-routes", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute(
        "SELECT r.*, t.did AS trunk_did "
        "FROM outbound_routes r "
        "LEFT JOIN trunks t ON r.trunk_name = t.name "
        "ORDER BY r.priority, r.id"
    ).fetchall()
    return jsonify([_route_to_dict(r) for r in rows])


@outbound_bp.route("/api/v1/outbound-routes", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    errors = _validate_route(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()

    if db.execute("SELECT 1 FROM outbound_routes WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"Route '{name}' already exists."]}), 409

    db.execute(
        "INSERT INTO outbound_routes (name, pattern, trunk_name, failover_trunk, "
        "priority, enabled) VALUES (?, ?, ?, ?, ?, ?)",
        (
            name,
            data.get("pattern", "").strip(),
            data.get("trunk_name", "").strip(),
            data.get("failover_trunk", "").strip(),
            int(data.get("priority", 10)),
            int(data.get("enabled", 1)),
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("outbound_route_create", target=name, after=data,
               username=username, status="ok" if ok else "error")

    result = db.execute("SELECT * FROM outbound_routes WHERE name = ?", (name,)).fetchone()
    code = 201 if ok else 207
    return jsonify({"route": _route_to_dict(result), "applied": ok, "message": msg}), code


@outbound_bp.route("/api/v1/outbound-routes/<int:route_id>", methods=["GET"])
@login_required
def api_get(route_id):
    db = get_db()
    row = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_route_to_dict(row))


@outbound_bp.route("/api/v1/outbound-routes/<int:route_id>", methods=["PUT"])
@login_required
def api_update(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["name"] = existing["name"]
    errors = _validate_route(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _route_to_dict(existing)

    db.execute(
        "UPDATE outbound_routes SET pattern=?, trunk_name=?, failover_trunk=?, "
        "priority=?, enabled=? WHERE id=?",
        (
            data.get("pattern", existing["pattern"]).strip(),
            data.get("trunk_name", existing["trunk_name"]).strip(),
            data.get("failover_trunk", existing["failover_trunk"]).strip(),
            int(data.get("priority", existing["priority"])),
            int(data.get("enabled", existing["enabled"])),
            route_id,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("outbound_route_update", target=existing["name"],
               before=before, after=data,
               username=username, status="ok" if ok else "error")

    result = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    return jsonify({"route": _route_to_dict(result), "applied": ok, "message": msg})


@outbound_bp.route("/api/v1/outbound-routes/<int:route_id>", methods=["DELETE"])
@login_required
def api_delete(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = _route_to_dict(existing)
    db.execute("DELETE FROM outbound_routes WHERE id = ?", (route_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("outbound_route_delete", target=before["name"], before=before,
               username=username, status="ok" if ok else "error")

    return jsonify({"deleted": before["name"], "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@outbound_bp.route("/outbound-routes")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute(
        "SELECT r.*, t.did AS trunk_did, f.did AS failover_did "
        "FROM outbound_routes r "
        "LEFT JOIN trunks t ON r.trunk_name = t.name "
        "LEFT JOIN trunks f ON r.failover_trunk = f.name "
        "ORDER BY r.priority, r.id"
    ).fetchall()
    return render_template("outbound_list.html", routes=rows)


@outbound_bp.route("/outbound-routes/new", methods=["GET", "POST"])
@login_required
def ui_new():
    db = get_db()
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "pattern": request.form.get("pattern", "").strip(),
            "trunk_name": request.form.get("trunk_name", "").strip(),
            "failover_trunk": request.form.get("failover_trunk", "").strip(),
            "priority": request.form.get("priority", "10").strip(),
            "enabled": 1 if request.form.get("enabled") else 0,
        }

        errors = _validate_route(data, is_new=True)
        if db.execute("SELECT 1 FROM outbound_routes WHERE name = ?", (data["name"],)).fetchone():
            errors.append(f"Route '{data['name']}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            trunks = _get_trunks()
            return render_template("outbound_form.html", route=data, is_new=True, trunks=trunks)

        db.execute(
            "INSERT INTO outbound_routes (name, pattern, trunk_name, failover_trunk, "
            "priority, enabled) VALUES (?, ?, ?, ?, ?, ?)",
            (
                data["name"], data["pattern"], data["trunk_name"],
                data["failover_trunk"], int(data["priority"]), data["enabled"],
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("outbound_route_create", target=data["name"], after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("outbound.ui_list"))

    defaults = {
        "name": "", "pattern": "", "trunk_name": "", "failover_trunk": "",
        "priority": 10, "enabled": 1,
    }
    trunks = _get_trunks()
    return render_template("outbound_form.html", route=defaults, is_new=True, trunks=trunks)


@outbound_bp.route("/outbound-routes/<int:route_id>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        flash("Route not found.", "danger")
        return redirect(url_for("outbound.ui_list"))

    if request.method == "POST":
        data = {
            "name": existing["name"],
            "pattern": request.form.get("pattern", "").strip(),
            "trunk_name": request.form.get("trunk_name", "").strip(),
            "failover_trunk": request.form.get("failover_trunk", "").strip(),
            "priority": request.form.get("priority", "10").strip(),
            "enabled": 1 if request.form.get("enabled") else 0,
        }

        errors = _validate_route(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            data["id"] = route_id
            trunks = _get_trunks()
            return render_template("outbound_form.html", route=data, is_new=False, trunks=trunks)

        before = _route_to_dict(existing)
        db.execute(
            "UPDATE outbound_routes SET pattern=?, trunk_name=?, failover_trunk=?, "
            "priority=?, enabled=? WHERE id=?",
            (
                data["pattern"], data["trunk_name"], data["failover_trunk"],
                int(data["priority"]), data["enabled"], route_id,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("outbound_route_update", target=existing["name"],
                   before=before, after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("outbound.ui_list"))

    route = _route_to_dict(existing)
    trunks = _get_trunks()
    return render_template("outbound_form.html", route=route, is_new=False, trunks=trunks)


@outbound_bp.route("/outbound-routes/<int:route_id>/delete", methods=["POST"])
@login_required
def ui_delete(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        flash("Route not found.", "danger")
        return redirect(url_for("outbound.ui_list"))

    before = _route_to_dict(existing)
    db.execute("DELETE FROM outbound_routes WHERE id = ?", (route_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("outbound_route_delete", target=before["name"], before=before,
               username=username, status="ok" if ok else "error")
    flash(f"Route '{before['name']}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("outbound.ui_list"))


@outbound_bp.route("/outbound-routes/<int:route_id>/toggle", methods=["POST"])
@login_required
def ui_toggle(route_id):
    db = get_db()
    existing = db.execute("SELECT * FROM outbound_routes WHERE id = ?", (route_id,)).fetchone()
    if not existing:
        flash("Route not found.", "danger")
        return redirect(url_for("outbound.ui_list"))

    new_val = 0 if existing["enabled"] else 1
    db.execute("UPDATE outbound_routes SET enabled = ? WHERE id = ?", (new_val, route_id))
    db.commit()

    username = get_current_user() or "system"
    action = "outbound_route_enable" if new_val else "outbound_route_disable"
    ok, msg = _apply_config(username)
    log_action(action, target=existing["name"], username=username,
               status="ok" if ok else "error")
    state = "enabled" if new_val else "disabled"
    flash(f"Route '{existing['name']}' {state}. {msg}", "info" if ok else "danger")
    return redirect(url_for("outbound.ui_list"))


@outbound_bp.route("/outbound-routes/preview")
@login_required
def ui_preview():
    """Show the current generated dialplan text."""
    content = generate_outbound_routes()
    return render_template("outbound_preview.html", dialplan=content)
