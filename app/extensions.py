"""Extensions CRUD — API + UI routes."""

from __future__ import annotations

import secrets
import string

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.asterisk_cmd import AsteriskCommandError, run_command
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import write_pjsip_extensions, write_voicemail_boxes
from app.snapshots import take_snapshot

extensions_bp = Blueprint("extensions", __name__)

VALID_CODECS = {"g722", "ulaw", "alaw", "g729", "opus", "gsm"}
VALID_DTMF = {"rfc4733", "inband", "info", "auto"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_password(length: int = 20) -> str:
    """Generate a random SIP password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _validate_extension(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []
    ext = data.get("ext", "").strip()
    if is_new:
        if not ext:
            errors.append("Extension number is required.")
        elif not ext.isdigit() or len(ext) < 3 or len(ext) > 6:
            errors.append("Extension must be 3-6 digits.")

    pw = data.get("sip_password", "")
    if is_new and not pw:
        errors.append("SIP password is required.")
    elif pw and len(pw) < 8:
        errors.append("SIP password must be at least 8 characters.")

    vm_pin = data.get("vm_pin", "1234")
    if vm_pin and (not vm_pin.isdigit() or len(vm_pin) < 4):
        errors.append("Voicemail PIN must be at least 4 digits.")

    codecs = data.get("codecs", "g722,ulaw,alaw")
    for c in codecs.split(","):
        if c.strip() and c.strip() not in VALID_CODECS:
            errors.append(f"Unknown codec: {c.strip()}")

    dtmf = data.get("dtmf_mode", "rfc4733")
    if dtmf not in VALID_DTMF:
        errors.append(f"Invalid DTMF mode: {dtmf}")

    mc = data.get("max_contacts", "3")
    try:
        mc_int = int(mc)
        if mc_int < 1 or mc_int > 10:
            errors.append("Max contacts must be between 1 and 10.")
    except (ValueError, TypeError):
        errors.append("Max contacts must be a number.")

    return errors


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    take_snapshot("pre-extension-apply")
    write_pjsip_extensions()
    write_voicemail_boxes()
    try:
        run_command("pjsip reload")
        run_command("module reload app_voicemail.so")
        return True, "Config applied and Asterisk reloaded."
    except AsteriskCommandError as exc:
        return False, f"Asterisk reload failed: {exc}"


def _ext_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/extensions
# ---------------------------------------------------------------------------

@extensions_bp.route("/api/v1/extensions", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM extensions ORDER BY ext").fetchall()
    return jsonify([{k: v for k, v in _ext_to_dict(r).items() if k != 'sip_password'} for r in rows])


@extensions_bp.route("/api/v1/extensions", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    if not data.get("sip_password"):
        data["sip_password"] = _generate_password()
    errors = _validate_extension(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    ext = data["ext"].strip()

    # Check for duplicate
    if db.execute("SELECT 1 FROM extensions WHERE ext = ?", (ext,)).fetchone():
        return jsonify({"errors": [f"Extension {ext} already exists."]}), 409

    db.execute(
        "INSERT INTO extensions (ext, callerid_name, sip_password, vm_pin, "
        "enabled, max_contacts, codecs, language, dtmf_mode, musicclass) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ext,
            data.get("callerid_name", f"Ext {ext}"),
            data["sip_password"],
            data.get("vm_pin", "1234"),
            int(data.get("enabled", 1)),
            int(data.get("max_contacts", 3)),
            data.get("codecs", "g722,ulaw,alaw"),
            data.get("language", "fr"),
            data.get("dtmf_mode", "rfc4733"),
            data.get("musicclass", "default"),
        ),
    )
    # Create matching voicemail box
    db.execute(
        "INSERT OR IGNORE INTO voicemail_boxes (mailbox, pin, name) VALUES (?, ?, ?)",
        (ext, data.get("vm_pin", "1234"), data.get("callerid_name", f"Ext {ext}")),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    safe_data = {k: v for k, v in data.items() if k != "sip_password"}
    log_action("extension_create", target=ext, after=safe_data, username=username, status=status)

    result = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    result_dict = _ext_to_dict(result)
    result_dict.pop('sip_password', None)
    code = 201 if ok else 207
    return jsonify({"extension": result_dict, "applied": ok, "message": msg}), code


@extensions_bp.route("/api/v1/extensions/<ext>", methods=["GET"])
@login_required
def api_get(ext):
    db = get_db()
    row = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    d = _ext_to_dict(row)
    d.pop('sip_password', None)
    return jsonify(d)


@extensions_bp.route("/api/v1/extensions/<ext>", methods=["PUT"])
@login_required
def api_update(ext):
    db = get_db()
    existing = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["ext"] = ext
    errors = _validate_extension(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _ext_to_dict(existing)

    db.execute(
        "UPDATE extensions SET callerid_name=?, sip_password=?, vm_pin=?, "
        "enabled=?, max_contacts=?, codecs=?, language=?, dtmf_mode=?, musicclass=? "
        "WHERE ext=?",
        (
            data.get("callerid_name", existing["callerid_name"]),
            data.get("sip_password", existing["sip_password"]),
            data.get("vm_pin", existing["vm_pin"]),
            int(data.get("enabled", existing["enabled"])),
            int(data.get("max_contacts", existing["max_contacts"])),
            data.get("codecs", existing["codecs"]),
            data.get("language", existing["language"]),
            data.get("dtmf_mode", existing["dtmf_mode"]),
            data.get("musicclass", existing["musicclass"]),
            ext,
        ),
    )
    # Sync voicemail box
    db.execute(
        "UPDATE voicemail_boxes SET pin=?, name=? WHERE mailbox=?",
        (
            data.get("vm_pin", existing["vm_pin"]),
            data.get("callerid_name", existing["callerid_name"]),
            ext,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    safe_before = {k: v for k, v in before.items() if k != "sip_password"}
    safe_after = {k: v for k, v in data.items() if k != "sip_password"}
    log_action("extension_update", target=ext, before=safe_before, after=safe_after, username=username, status=status)

    result = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    result_dict = _ext_to_dict(result)
    result_dict.pop('sip_password', None)
    return jsonify({"extension": result_dict, "applied": ok, "message": msg})


@extensions_bp.route("/api/v1/extensions/<ext>", methods=["DELETE"])
@login_required
def api_delete(ext):
    db = get_db()
    existing = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = _ext_to_dict(existing)
    db.execute("DELETE FROM extensions WHERE ext = ?", (ext,))
    db.execute("DELETE FROM voicemail_boxes WHERE mailbox = ?", (ext,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    safe_before = {k: v for k, v in before.items() if k != "sip_password"}
    log_action("extension_delete", target=ext, before=safe_before, username=username, status=status)

    return jsonify({"deleted": ext, "applied": ok, "message": msg})


@extensions_bp.route("/api/v1/extensions/<ext>/regenerate-secret", methods=["POST"])
@login_required
def api_regenerate_secret(ext):
    db = get_db()
    existing = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    new_pw = _generate_password()
    db.execute("UPDATE extensions SET sip_password = ? WHERE ext = ?", (new_pw, ext))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("extension_regenerate_secret", target=ext, username=username)

    return jsonify({"ext": ext, "sip_password": new_pw, "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@extensions_bp.route("/extensions")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM extensions ORDER BY ext").fetchall()
    return render_template("extensions_list.html", extensions=rows)


@extensions_bp.route("/extensions/new", methods=["GET", "POST"])
@login_required
def ui_new():
    if request.method == "POST":
        data = {
            "ext": request.form.get("ext", "").strip(),
            "callerid_name": request.form.get("callerid_name", "").strip(),
            "sip_password": request.form.get("sip_password", "").strip() or _generate_password(),
            "vm_pin": request.form.get("vm_pin", "1234").strip(),
            "enabled": 1 if request.form.get("enabled") else 0,
            "max_contacts": request.form.get("max_contacts", "3").strip(),
            "codecs": request.form.get("codecs", "g722,ulaw,alaw").strip(),
            "language": request.form.get("language", "fr").strip(),
            "dtmf_mode": request.form.get("dtmf_mode", "rfc4733").strip(),
            "musicclass": request.form.get("musicclass", "default").strip(),
        }
        errors = _validate_extension(data, is_new=True)
        db = get_db()
        if db.execute("SELECT 1 FROM extensions WHERE ext = ?", (data["ext"],)).fetchone():
            errors.append(f"Extension {data['ext']} already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("extensions_form.html", ext=data, is_new=True)

        db.execute(
            "INSERT INTO extensions (ext, callerid_name, sip_password, vm_pin, "
            "enabled, max_contacts, codecs, language, dtmf_mode, musicclass) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["ext"], data["callerid_name"], data["sip_password"],
                data["vm_pin"], data["enabled"], int(data["max_contacts"]),
                data["codecs"], data["language"], data["dtmf_mode"],
                data["musicclass"],
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO voicemail_boxes (mailbox, pin, name) VALUES (?, ?, ?)",
            (data["ext"], data["vm_pin"], data["callerid_name"]),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        safe_data = {k: v for k, v in data.items() if k != "sip_password"}
        log_action("extension_create", target=data["ext"], after=safe_data, username=username,
                    status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("extensions.ui_list"))

    # GET — blank form
    defaults = {
        "ext": "", "callerid_name": "", "sip_password": _generate_password(),
        "vm_pin": "1234", "enabled": 1, "max_contacts": 3,
        "codecs": "g722,ulaw,alaw", "language": "fr", "dtmf_mode": "rfc4733",
        "musicclass": "default",
    }
    return render_template("extensions_form.html", ext=defaults, is_new=True)


@extensions_bp.route("/extensions/<ext_id>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(ext_id):
    db = get_db()
    existing = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext_id,)).fetchone()
    if not existing:
        flash("Extension not found.", "danger")
        return redirect(url_for("extensions.ui_list"))

    if request.method == "POST":
        data = {
            "ext": ext_id,
            "callerid_name": request.form.get("callerid_name", "").strip(),
            "sip_password": request.form.get("sip_password", "").strip() or existing["sip_password"],
            "vm_pin": request.form.get("vm_pin", "").strip() or existing["vm_pin"],
            "enabled": 1 if request.form.get("enabled") else 0,
            "max_contacts": request.form.get("max_contacts", "3").strip(),
            "codecs": request.form.get("codecs", "").strip() or existing["codecs"],
            "language": request.form.get("language", "").strip() or existing["language"],
            "dtmf_mode": request.form.get("dtmf_mode", "").strip() or existing["dtmf_mode"],
            "musicclass": request.form.get("musicclass", "").strip() or existing["musicclass"],
        }
        errors = _validate_extension(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("extensions_form.html", ext=data, is_new=False)

        before = _ext_to_dict(existing)
        db.execute(
            "UPDATE extensions SET callerid_name=?, sip_password=?, vm_pin=?, "
            "enabled=?, max_contacts=?, codecs=?, language=?, dtmf_mode=?, musicclass=? "
            "WHERE ext=?",
            (
                data["callerid_name"], data["sip_password"], data["vm_pin"],
                data["enabled"], int(data["max_contacts"]), data["codecs"],
                data["language"], data["dtmf_mode"], data["musicclass"], ext_id,
            ),
        )
        db.execute(
            "UPDATE voicemail_boxes SET pin=?, name=? WHERE mailbox=?",
            (data["vm_pin"], data["callerid_name"], ext_id),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        safe_before = {k: v for k, v in before.items() if k != "sip_password"}
        safe_after = {k: v for k, v in data.items() if k != "sip_password"}
        log_action("extension_update", target=ext_id, before=safe_before, after=safe_after,
                    username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("extensions.ui_list"))

    return render_template("extensions_form.html", ext=existing, is_new=False)


@extensions_bp.route("/extensions/<ext_id>/delete", methods=["POST"])
@login_required
def ui_delete(ext_id):
    db = get_db()
    existing = db.execute("SELECT * FROM extensions WHERE ext = ?", (ext_id,)).fetchone()
    if not existing:
        flash("Extension not found.", "danger")
        return redirect(url_for("extensions.ui_list"))

    before = _ext_to_dict(existing)
    db.execute("DELETE FROM extensions WHERE ext = ?", (ext_id,))
    db.execute("DELETE FROM voicemail_boxes WHERE mailbox = ?", (ext_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    safe_before = {k: v for k, v in before.items() if k != "sip_password"}
    log_action("extension_delete", target=ext_id, before=safe_before, username=username,
                status="ok" if ok else "error")
    flash(f"Extension {ext_id} deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("extensions.ui_list"))
