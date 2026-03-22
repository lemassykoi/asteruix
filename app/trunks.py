"""Trunks CRUD — API + UI routes."""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import write_pjsip_trunks

trunks_bp = Blueprint("trunks", __name__)

VALID_TYPES = {"registration", "identify", "device"}
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_trunk(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []
    name = data.get("name", "").strip()
    if is_new:
        if not name:
            errors.append("Trunk name is required.")
        elif not NAME_RE.match(name):
            errors.append("Name must start with a letter, contain only letters/digits/hyphens/underscores, max 32 chars.")

    trunk_type = data.get("type", "registration")
    if trunk_type not in VALID_TYPES:
        errors.append(f"Invalid trunk type: {trunk_type}")

    host = data.get("host", "").strip()
    if not host:
        errors.append("Host is required.")

    if trunk_type in ("registration", "device"):
        if not data.get("username", "").strip():
            errors.append(f"Username is required for {trunk_type} trunks.")
        if is_new and not data.get("password", "").strip():
            errors.append(f"Password is required for {trunk_type} trunks.")

    identify_match = data.get("identify_match", "").strip()
    if trunk_type != "device" and not identify_match:
        errors.append("Identify match (IP/hostname) is required.")

    return errors


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-trunk-apply",
        writers=[write_pjsip_trunks],
        reload_commands=["pjsip reload"],
    )


def _trunk_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/trunks
# ---------------------------------------------------------------------------

@trunks_bp.route("/api/v1/trunks", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM trunks ORDER BY name").fetchall()
    return jsonify([{k: v for k, v in _trunk_to_dict(r).items() if k != 'password'} for r in rows])


@trunks_bp.route("/api/v1/trunks", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    errors = _validate_trunk(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()

    if db.execute("SELECT 1 FROM trunks WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"Trunk '{name}' already exists."]}), 409

    db.execute(
        "INSERT INTO trunks (name, type, host, username, password, from_domain, "
        "contact_uri, identify_match, registration_client_uri, registration_server_uri, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            data.get("type", "registration"),
            data.get("host", "").strip(),
            data.get("username", "").strip(),
            data.get("password", "").strip(),
            data.get("from_domain", "").strip(),
            data.get("contact_uri", "").strip(),
            data.get("identify_match", "").strip(),
            data.get("registration_client_uri", "").strip(),
            data.get("registration_server_uri", "").strip(),
            int(data.get("enabled", 1)),
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    safe_data = {k: v for k, v in data.items() if k != "password"}
    log_action("trunk_create", target=name, after=safe_data, username=username, status=status)

    result = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    result_dict = _trunk_to_dict(result)
    result_dict.pop('password', None)
    code = 201 if ok else 207
    return jsonify({"trunk": result_dict, "applied": ok, "message": msg}), code


@trunks_bp.route("/api/v1/trunks/<name>", methods=["GET"])
@login_required
def api_get(name):
    db = get_db()
    row = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    d = _trunk_to_dict(row)
    d.pop('password', None)
    return jsonify(d)


@trunks_bp.route("/api/v1/trunks/<name>", methods=["PUT"])
@login_required
def api_update(name):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["name"] = name
    # Keep existing password if not provided
    if not data.get("password", "").strip():
        data["password"] = existing["password"]
    errors = _validate_trunk(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _trunk_to_dict(existing)

    db.execute(
        "UPDATE trunks SET type=?, host=?, username=?, password=?, from_domain=?, "
        "contact_uri=?, identify_match=?, registration_client_uri=?, "
        "registration_server_uri=?, enabled=? WHERE name=?",
        (
            data.get("type", existing["type"]),
            data.get("host", existing["host"]).strip(),
            data.get("username", existing["username"]).strip(),
            data["password"],
            data.get("from_domain", existing["from_domain"]).strip(),
            data.get("contact_uri", existing["contact_uri"]).strip(),
            data.get("identify_match", existing["identify_match"]).strip(),
            data.get("registration_client_uri", existing["registration_client_uri"]).strip(),
            data.get("registration_server_uri", existing["registration_server_uri"]).strip(),
            int(data.get("enabled", existing["enabled"])),
            name,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    safe_before = {k: v for k, v in before.items() if k != "password"}
    safe_after = {k: v for k, v in data.items() if k != "password"}
    log_action("trunk_update", target=name, before=safe_before, after=safe_after,
               username=username, status=status)

    result = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    result_dict = _trunk_to_dict(result)
    result_dict.pop('password', None)
    return jsonify({"trunk": result_dict, "applied": ok, "message": msg})


@trunks_bp.route("/api/v1/trunks/<name>", methods=["DELETE"])
@login_required
def api_delete(name):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = {k: v for k, v in _trunk_to_dict(existing).items() if k != "password"}
    db.execute("DELETE FROM trunks WHERE name = ?", (name,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("trunk_delete", target=name, before=before, username=username, status=status)

    return jsonify({"deleted": name, "applied": ok, "message": msg})


@trunks_bp.route("/api/v1/trunks/<name>/enable", methods=["POST"])
@login_required
def api_enable(name):
    return _set_enabled(name, 1)


@trunks_bp.route("/api/v1/trunks/<name>/disable", methods=["POST"])
@login_required
def api_disable(name):
    return _set_enabled(name, 0)


def _set_enabled(name: str, value: int):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    db.execute("UPDATE trunks SET enabled = ? WHERE name = ?", (value, name))
    db.commit()

    username = get_current_user() or "system"
    action = "trunk_enable" if value else "trunk_disable"
    ok, msg = _apply_config(username)
    log_action(action, target=name, username=username, status="ok" if ok else "error")

    return jsonify({"name": name, "enabled": value, "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@trunks_bp.route("/trunks")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM trunks ORDER BY name").fetchall()
    return render_template("trunks_list.html", trunks=rows)


@trunks_bp.route("/trunks/new", methods=["GET", "POST"])
@login_required
def ui_new():
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "type": request.form.get("type", "registration").strip(),
            "host": request.form.get("host", "").strip(),
            "username": request.form.get("username", "").strip(),
            "password": request.form.get("password", "").strip(),
            "from_domain": request.form.get("from_domain", "").strip(),
            "contact_uri": request.form.get("contact_uri", "").strip(),
            "identify_match": request.form.get("identify_match", "").strip(),
            "registration_client_uri": request.form.get("registration_client_uri", "").strip(),
            "registration_server_uri": request.form.get("registration_server_uri", "").strip(),
            "enabled": 1 if request.form.get("enabled") else 0,
        }
        errors = _validate_trunk(data, is_new=True)
        db = get_db()
        if db.execute("SELECT 1 FROM trunks WHERE name = ?", (data["name"],)).fetchone():
            errors.append(f"Trunk '{data['name']}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("trunks_form.html", trunk=data, is_new=True)

        db.execute(
            "INSERT INTO trunks (name, type, host, username, password, from_domain, "
            "contact_uri, identify_match, registration_client_uri, registration_server_uri, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"], data["type"], data["host"], data["username"],
                data["password"], data["from_domain"], data["contact_uri"],
                data["identify_match"], data["registration_client_uri"],
                data["registration_server_uri"], data["enabled"],
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        safe_data = {k: v for k, v in data.items() if k != "password"}
        log_action("trunk_create", target=data["name"], after=safe_data,
                    username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("trunks.ui_list"))

    # GET — blank form
    defaults = {
        "name": "", "type": "registration", "host": "", "username": "",
        "password": "", "from_domain": "", "contact_uri": "",
        "identify_match": "", "registration_client_uri": "",
        "registration_server_uri": "", "enabled": 1,
    }
    return render_template("trunks_form.html", trunk=defaults, is_new=True)


@trunks_bp.route("/trunks/<trunk_name>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(trunk_name):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (trunk_name,)).fetchone()
    if not existing:
        flash("Trunk not found.", "danger")
        return redirect(url_for("trunks.ui_list"))

    if request.method == "POST":
        data = {
            "name": trunk_name,
            "type": request.form.get("type", "").strip() or existing["type"],
            "host": request.form.get("host", "").strip(),
            "username": request.form.get("username", "").strip(),
            "password": request.form.get("password", "").strip() or existing["password"],
            "from_domain": request.form.get("from_domain", "").strip(),
            "contact_uri": request.form.get("contact_uri", "").strip(),
            "identify_match": request.form.get("identify_match", "").strip(),
            "registration_client_uri": request.form.get("registration_client_uri", "").strip(),
            "registration_server_uri": request.form.get("registration_server_uri", "").strip(),
            "enabled": 1 if request.form.get("enabled") else 0,
        }
        errors = _validate_trunk(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("trunks_form.html", trunk=data, is_new=False)

        before = {k: v for k, v in _trunk_to_dict(existing).items() if k != "password"}
        db.execute(
            "UPDATE trunks SET type=?, host=?, username=?, password=?, from_domain=?, "
            "contact_uri=?, identify_match=?, registration_client_uri=?, "
            "registration_server_uri=?, enabled=? WHERE name=?",
            (
                data["type"], data["host"], data["username"], data["password"],
                data["from_domain"], data["contact_uri"], data["identify_match"],
                data["registration_client_uri"], data["registration_server_uri"],
                data["enabled"], trunk_name,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        safe_after = {k: v for k, v in data.items() if k != "password"}
        log_action("trunk_update", target=trunk_name, before=before, after=safe_after,
                    username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("trunks.ui_list"))

    return render_template("trunks_form.html", trunk=existing, is_new=False)


@trunks_bp.route("/trunks/<trunk_name>/delete", methods=["POST"])
@login_required
def ui_delete(trunk_name):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (trunk_name,)).fetchone()
    if not existing:
        flash("Trunk not found.", "danger")
        return redirect(url_for("trunks.ui_list"))

    before = {k: v for k, v in _trunk_to_dict(existing).items() if k != "password"}
    db.execute("DELETE FROM trunks WHERE name = ?", (trunk_name,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("trunk_delete", target=trunk_name, before=before, username=username,
                status="ok" if ok else "error")
    flash(f"Trunk '{trunk_name}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("trunks.ui_list"))


@trunks_bp.route("/trunks/<trunk_name>/toggle", methods=["POST"])
@login_required
def ui_toggle(trunk_name):
    db = get_db()
    existing = db.execute("SELECT * FROM trunks WHERE name = ?", (trunk_name,)).fetchone()
    if not existing:
        flash("Trunk not found.", "danger")
        return redirect(url_for("trunks.ui_list"))

    new_val = 0 if existing["enabled"] else 1
    db.execute("UPDATE trunks SET enabled = ? WHERE name = ?", (new_val, trunk_name))
    db.commit()

    username = get_current_user() or "system"
    action = "trunk_enable" if new_val else "trunk_disable"
    ok, msg = _apply_config(username)
    log_action(action, target=trunk_name, username=username, status="ok" if ok else "error")
    state = "enabled" if new_val else "disabled"
    flash(f"Trunk '{trunk_name}' {state}. {msg}", "info" if ok else "danger")
    return redirect(url_for("trunks.ui_list"))
