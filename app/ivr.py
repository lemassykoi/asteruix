"""IVR Menu Management — API + UI routes."""

from __future__ import annotations

import json
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import generate_ivr_menus, write_ivr_menus

ivr_bp = Blueprint("ivr", __name__)

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
VALID_DIGITS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#"}
VALID_ACTIONS = {"goto_extension", "goto_context", "hangup"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_ivr(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []
    name = data.get("name", "").strip()
    if is_new:
        if not name:
            errors.append("IVR name is required.")
        elif not NAME_RE.match(name):
            errors.append("Name must start with a letter, max 64 chars, letters/digits/hyphens/underscores only.")

    greeting = data.get("greeting", "").strip()
    if not greeting:
        errors.append("Greeting announcement is required.")

    try:
        timeout = int(data.get("timeout", 5))
        if timeout < 1 or timeout > 60:
            errors.append("Timeout must be between 1 and 60 seconds.")
    except (TypeError, ValueError):
        errors.append("Timeout must be a number.")

    try:
        invalid_retries = int(data.get("invalid_retries", 3))
        if invalid_retries < 1 or invalid_retries > 10:
            errors.append("Invalid retries must be between 1 and 10.")
    except (TypeError, ValueError):
        errors.append("Invalid retries must be a number.")

    options = data.get("options", [])
    if not options:
        errors.append("At least one DTMF option is required.")

    seen_digits = set()
    for i, opt in enumerate(options):
        digit = opt.get("digit", "").strip()
        action = opt.get("action", "").strip()
        target = opt.get("target", "").strip()

        if not digit:
            errors.append(f"Option {i+1}: digit is required.")
        elif digit not in VALID_DIGITS:
            errors.append(f"Option {i+1}: invalid digit '{digit}'.")
        elif digit in seen_digits:
            errors.append(f"Option {i+1}: digit '{digit}' is already used.")
        else:
            seen_digits.add(digit)

        if not action:
            errors.append(f"Option {i+1}: action is required.")
        elif action not in VALID_ACTIONS:
            errors.append(f"Option {i+1}: invalid action '{action}'.")

        if action in ("goto_extension", "goto_context") and not target:
            errors.append(f"Option {i+1}: target is required for action '{action}'.")

    return errors


def _parse_options_from_form(form) -> list[dict]:
    """Parse DTMF options from repeated form fields."""
    options = []
    idx = 0
    while True:
        digit = form.get(f"opt_{idx}_digit", "").strip()
        action = form.get(f"opt_{idx}_action", "").strip()
        target = form.get(f"opt_{idx}_target", "").strip()
        if not digit and not action and not target:
            break
        options.append({"digit": digit, "action": action, "target": target})
        idx += 1
    return options


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-ivr-apply",
        writers=[write_ivr_menus],
        reload_commands=["dialplan reload"],
    )


def _ivr_to_dict(row) -> dict:
    d = {k: row[k] for k in row.keys()}
    d["options"] = json.loads(d.get("options_json", "[]"))
    return d


def _get_select_options():
    """Fetch options for form select fields."""
    db = get_db()
    extensions = db.execute(
        "SELECT ext, callerid_name FROM extensions WHERE enabled = 1 ORDER BY ext"
    ).fetchall()
    announcements = db.execute(
        "SELECT id, key_name, active FROM announcements ORDER BY key_name"
    ).fetchall()
    return {
        "extensions": extensions,
        "announcements": announcements,
    }


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/ivr
# ---------------------------------------------------------------------------

@ivr_bp.route("/api/v1/ivr", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM ivr_menus ORDER BY name").fetchall()
    return jsonify([_ivr_to_dict(r) for r in rows])


@ivr_bp.route("/api/v1/ivr", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    data.setdefault("options", [])
    errors = _validate_ivr(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()

    if db.execute("SELECT 1 FROM ivr_menus WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"IVR menu '{name}' already exists."]}), 409

    options_json = json.dumps(data["options"])
    db.execute(
        "INSERT INTO ivr_menus (name, greeting, timeout, invalid_retries, options_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            name,
            data.get("greeting", "").strip(),
            int(data.get("timeout", 5)),
            int(data.get("invalid_retries", 3)),
            options_json,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("ivr_create", target=name, after=data,
               username=username, status=status)

    result = db.execute("SELECT * FROM ivr_menus WHERE name = ?", (name,)).fetchone()
    code = 201 if ok else 207
    return jsonify({"ivr": _ivr_to_dict(result), "applied": ok, "message": msg}), code


@ivr_bp.route("/api/v1/ivr/<int:ivr_id>", methods=["GET"])
@login_required
def api_get(ivr_id):
    db = get_db()
    row = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_ivr_to_dict(row))


@ivr_bp.route("/api/v1/ivr/<int:ivr_id>", methods=["PUT"])
@login_required
def api_update(ivr_id):
    db = get_db()
    existing = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["name"] = existing["name"]  # name is immutable
    data.setdefault("options", json.loads(existing["options_json"]))
    errors = _validate_ivr(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _ivr_to_dict(existing)
    options_json = json.dumps(data["options"])

    db.execute(
        "UPDATE ivr_menus SET greeting=?, timeout=?, invalid_retries=?, "
        "options_json=? WHERE id=?",
        (
            data.get("greeting", existing["greeting"]).strip(),
            int(data.get("timeout", existing["timeout"])),
            int(data.get("invalid_retries", existing["invalid_retries"])),
            options_json,
            ivr_id,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("ivr_update", target=existing["name"],
               before=before, after=data, username=username, status=status)

    result = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    return jsonify({"ivr": _ivr_to_dict(result), "applied": ok, "message": msg})


@ivr_bp.route("/api/v1/ivr/<int:ivr_id>", methods=["DELETE"])
@login_required
def api_delete(ivr_id):
    db = get_db()
    existing = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = _ivr_to_dict(existing)
    db.execute("DELETE FROM ivr_menus WHERE id = ?", (ivr_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("ivr_delete", target=before["name"], before=before,
               username=username, status=status)

    return jsonify({"deleted": before["name"], "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@ivr_bp.route("/ivr")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM ivr_menus ORDER BY name").fetchall()
    menus = [_ivr_to_dict(r) for r in rows]
    return render_template("ivr_list.html", menus=menus)


@ivr_bp.route("/ivr/new", methods=["GET", "POST"])
@login_required
def ui_new():
    db = get_db()
    if request.method == "POST":
        options = _parse_options_from_form(request.form)
        data = {
            "name": request.form.get("name", "").strip(),
            "greeting": request.form.get("greeting", "").strip(),
            "timeout": request.form.get("timeout", "5").strip(),
            "invalid_retries": request.form.get("invalid_retries", "3").strip(),
            "options": options,
        }
        errors = _validate_ivr(data, is_new=True)
        if db.execute("SELECT 1 FROM ivr_menus WHERE name = ?", (data["name"],)).fetchone():
            errors.append(f"IVR menu '{data['name']}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            opts = _get_select_options()
            return render_template("ivr_form.html", ivr=data, is_new=True, **opts)

        options_json = json.dumps(options)
        db.execute(
            "INSERT INTO ivr_menus (name, greeting, timeout, invalid_retries, options_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["name"],
                data["greeting"],
                int(data["timeout"]),
                int(data["invalid_retries"]),
                options_json,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("ivr_create", target=data["name"], after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("ivr.ui_list"))

    # GET — blank form with defaults
    defaults = {
        "name": "",
        "greeting": "",
        "timeout": "5",
        "invalid_retries": "3",
        "options": [{"digit": "0", "action": "goto_extension", "target": ""}],
    }
    opts = _get_select_options()
    return render_template("ivr_form.html", ivr=defaults, is_new=True, **opts)


@ivr_bp.route("/ivr/<int:ivr_id>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(ivr_id):
    db = get_db()
    existing = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    if not existing:
        flash("IVR menu not found.", "danger")
        return redirect(url_for("ivr.ui_list"))

    if request.method == "POST":
        options = _parse_options_from_form(request.form)
        data = {
            "name": existing["name"],
            "greeting": request.form.get("greeting", "").strip(),
            "timeout": request.form.get("timeout", "5").strip(),
            "invalid_retries": request.form.get("invalid_retries", "3").strip(),
            "options": options,
        }
        errors = _validate_ivr(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            data["id"] = ivr_id
            opts = _get_select_options()
            return render_template("ivr_form.html", ivr=data, is_new=False, **opts)

        before = _ivr_to_dict(existing)
        options_json = json.dumps(options)
        db.execute(
            "UPDATE ivr_menus SET greeting=?, timeout=?, invalid_retries=?, "
            "options_json=? WHERE id=?",
            (
                data["greeting"],
                int(data["timeout"]),
                int(data["invalid_retries"]),
                options_json,
                ivr_id,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("ivr_update", target=existing["name"],
                   before=before, after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("ivr.ui_list"))

    ivr = _ivr_to_dict(existing)
    ivr["id"] = existing["id"]
    opts = _get_select_options()
    return render_template("ivr_form.html", ivr=ivr, is_new=False, **opts)


@ivr_bp.route("/ivr/<int:ivr_id>/delete", methods=["POST"])
@login_required
def ui_delete(ivr_id):
    db = get_db()
    existing = db.execute("SELECT * FROM ivr_menus WHERE id = ?", (ivr_id,)).fetchone()
    if not existing:
        flash("IVR menu not found.", "danger")
        return redirect(url_for("ivr.ui_list"))

    before = _ivr_to_dict(existing)
    db.execute("DELETE FROM ivr_menus WHERE id = ?", (ivr_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("ivr_delete", target=before["name"], before=before,
               username=username, status="ok" if ok else "error")
    flash(f"IVR menu '{before['name']}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("ivr.ui_list"))


@ivr_bp.route("/ivr/preview")
@login_required
def ui_preview():
    """Show the current generated IVR dialplan text."""
    content = generate_ivr_menus()
    return render_template("ivr_preview.html", dialplan=content)
