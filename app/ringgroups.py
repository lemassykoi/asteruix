"""Ring Group CRUD — API + UI routes."""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import write_ring_groups

ringgroups_bp = Blueprint("ringgroups", __name__)

EXT_RE = re.compile(r"^\d{3,6}$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _-]{0,63}$")
VALID_STRATEGIES = {"ringall", "hunt", "memoryhunt"}
VALID_NOANSWER_ACTIONS = {"hangup", "voicemail", "vmblast", "extension"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_ring_group(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []

    ext = data.get("extension", "").strip()
    if is_new:
        if not ext:
            errors.append("Extension number is required.")
        elif not EXT_RE.match(ext):
            errors.append("Extension must be 3-6 digits.")

    name = data.get("name", "").strip()
    if not name:
        errors.append("Name is required.")
    elif not NAME_RE.match(name):
        errors.append("Name must start with a letter or digit and be up to 64 characters (letters, digits, spaces, hyphens, underscores).")

    strategy = data.get("strategy", "ringall").strip()
    if strategy not in VALID_STRATEGIES:
        errors.append(f"Invalid strategy: {strategy}")

    members = data.get("members", "").strip()
    if not members:
        errors.append("Members are required.")
    else:
        for m in members.split(","):
            m = m.strip()
            if m and not EXT_RE.match(m):
                errors.append(f"Invalid member extension: {m}")

    ring_time = data.get("ring_time", "30")
    try:
        rt = int(ring_time)
        if rt < 10 or rt > 120:
            errors.append("Ring time must be between 10 and 120.")
    except (TypeError, ValueError):
        errors.append("Ring time must be a number.")

    data["moh_class"] = data.get("moh_class", "default").strip()

    noanswer_action = data.get("noanswer_action", "hangup").strip()
    if noanswer_action not in VALID_NOANSWER_ACTIONS:
        errors.append(f"Invalid no-answer action: {noanswer_action}")

    noanswer_target = data.get("noanswer_target", "").strip()
    if noanswer_action in ("voicemail", "extension") and not noanswer_target:
        errors.append("No-answer target is required when action is voicemail or extension.")
    # vmblast needs no target — it uses blast_config from DB

    return errors


def _apply_config() -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-ringgroup-apply",
        writers=[write_ring_groups],
        reload_commands=["dialplan reload"],
    )


def _rg_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_extensions():
    """Fetch extensions for member selector."""
    db = get_db()
    return db.execute("SELECT ext, callerid_name FROM extensions WHERE enabled = 1 ORDER BY ext").fetchall()


def _get_moh_classes():
    """Fetch MoH classes for dropdown."""
    db = get_db()
    return db.execute("SELECT name FROM moh_classes ORDER BY name").fetchall()


def _get_announcements():
    """Fetch announcements for dropdown."""
    db = get_db()
    return db.execute("SELECT key_name FROM announcements ORDER BY key_name").fetchall()


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/ring-groups
# ---------------------------------------------------------------------------

@ringgroups_bp.route("/api/v1/ring-groups", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM ring_groups ORDER BY extension").fetchall()
    return jsonify([_rg_to_dict(r) for r in rows])


@ringgroups_bp.route("/api/v1/ring-groups/<extension>", methods=["GET"])
@login_required
def api_get(extension):
    db = get_db()
    row = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_rg_to_dict(row))


@ringgroups_bp.route("/api/v1/ring-groups", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    errors = _validate_ring_group(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    ext = data["extension"].strip()

    # Check for duplicate in ring_groups
    if db.execute("SELECT 1 FROM ring_groups WHERE extension = ?", (ext,)).fetchone():
        return jsonify({"errors": [f"Ring group {ext} already exists."]}), 409

    # Check conflict with extensions table
    if db.execute("SELECT 1 FROM extensions WHERE ext = ?", (ext,)).fetchone():
        return jsonify({"errors": [f"Extension {ext} is already used by a phone extension."]}), 409

    db.execute(
        "INSERT INTO ring_groups (extension, name, strategy, members, ring_time, "
        "greeting_announcement, moh_class, noanswer_announcement, noanswer_action, noanswer_target) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data["extension"], data["name"], data["strategy"], data["members"],
         int(data["ring_time"]), data["greeting_announcement"], data["moh_class"],
         data["noanswer_announcement"], data["noanswer_action"], data["noanswer_target"]),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config()
    status = "ok" if ok else "error"
    log_action("ring_group_create", target=ext, after=data, username=username, status=status)

    result = db.execute("SELECT * FROM ring_groups WHERE extension = ?", (ext,)).fetchone()
    code = 201 if ok else 207
    return jsonify({"ring_group": _rg_to_dict(result), "applied": ok, "message": msg}), code


@ringgroups_bp.route("/api/v1/ring-groups/<extension>", methods=["PUT"])
@login_required
def api_update(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["extension"] = extension
    errors = _validate_ring_group(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _rg_to_dict(existing)

    db.execute(
        "UPDATE ring_groups SET name=?, strategy=?, members=?, ring_time=?, "
        "greeting_announcement=?, moh_class=?, noanswer_announcement=?, "
        "noanswer_action=?, noanswer_target=? WHERE extension=?",
        (data["name"], data["strategy"], data["members"], int(data["ring_time"]),
         data["greeting_announcement"], data["moh_class"], data["noanswer_announcement"],
         data["noanswer_action"], data["noanswer_target"], extension),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config()
    status = "ok" if ok else "error"
    log_action("ring_group_update", target=extension,
               before=before, after=data, username=username, status=status)

    result = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    return jsonify({"ring_group": _rg_to_dict(result), "applied": ok, "message": msg})


@ringgroups_bp.route("/api/v1/ring-groups/<extension>", methods=["DELETE"])
@login_required
def api_delete(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    before = _rg_to_dict(existing)
    db.execute("DELETE FROM ring_groups WHERE extension = ?", (extension,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config()
    status = "ok" if ok else "error"
    log_action("ring_group_delete", target=extension, before=before,
               username=username, status=status)

    return jsonify({"deleted": extension, "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@ringgroups_bp.route("/ring-groups")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM ring_groups ORDER BY extension").fetchall()
    return render_template("ringgroups_list.html", groups=rows)


@ringgroups_bp.route("/ring-groups/new", methods=["GET", "POST"])
@login_required
def ui_new():
    if request.method == "POST":
        data = {
            "extension": request.form.get("extension", "").strip(),
            "name": request.form.get("name", "").strip(),
            "strategy": request.form.get("strategy", "ringall").strip(),
            "members": request.form.get("members", "").strip(),
            "ring_time": request.form.get("ring_time", "30").strip(),
            "greeting_announcement": request.form.get("greeting_announcement", "").strip(),
            "moh_class": request.form.get("moh_class", "default").strip(),
            "noanswer_announcement": request.form.get("noanswer_announcement", "").strip(),
            "noanswer_action": request.form.get("noanswer_action", "hangup").strip(),
            "noanswer_target": request.form.get("noanswer_target", "").strip(),
        }
        errors = _validate_ring_group(data, is_new=True)
        db = get_db()
        if db.execute("SELECT 1 FROM ring_groups WHERE extension = ?", (data["extension"],)).fetchone():
            errors.append(f"Ring group {data['extension']} already exists.")
        if db.execute("SELECT 1 FROM extensions WHERE ext = ?", (data["extension"],)).fetchone():
            errors.append(f"Extension {data['extension']} is already used by a phone extension.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("ringgroups_form.html", group=data, is_new=True,
                                   extensions=_get_extensions(), moh_classes=_get_moh_classes(),
                                   announcements=_get_announcements())

        db.execute(
            "INSERT INTO ring_groups (extension, name, strategy, members, ring_time, "
            "greeting_announcement, moh_class, noanswer_announcement, noanswer_action, noanswer_target) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data["extension"], data["name"], data["strategy"], data["members"],
             int(data["ring_time"]), data["greeting_announcement"], data["moh_class"],
             data["noanswer_announcement"], data["noanswer_action"], data["noanswer_target"]),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config()
        log_action("ring_group_create", target=data["extension"], after=data,
                    username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("ringgroups.ui_list"))

    # GET — blank form
    defaults = {
        "extension": "", "name": "", "strategy": "ringall", "members": "",
        "ring_time": 30, "greeting_announcement": "", "moh_class": "default",
        "noanswer_announcement": "", "noanswer_action": "hangup", "noanswer_target": "",
    }
    return render_template("ringgroups_form.html", group=defaults, is_new=True,
                           extensions=_get_extensions(), moh_classes=_get_moh_classes(),
                           announcements=_get_announcements())


@ringgroups_bp.route("/ring-groups/<extension>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        flash("Ring group not found.", "danger")
        return redirect(url_for("ringgroups.ui_list"))

    if request.method == "POST":
        data = {
            "extension": extension,
            "name": request.form.get("name", "").strip(),
            "strategy": request.form.get("strategy", "ringall").strip(),
            "members": request.form.get("members", "").strip(),
            "ring_time": request.form.get("ring_time", "30").strip(),
            "greeting_announcement": request.form.get("greeting_announcement", "").strip(),
            "moh_class": request.form.get("moh_class", "default").strip(),
            "noanswer_announcement": request.form.get("noanswer_announcement", "").strip(),
            "noanswer_action": request.form.get("noanswer_action", "hangup").strip(),
            "noanswer_target": request.form.get("noanswer_target", "").strip(),
        }
        errors = _validate_ring_group(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("ringgroups_form.html", group=data, is_new=False,
                                   extensions=_get_extensions(), moh_classes=_get_moh_classes(),
                                   announcements=_get_announcements())

        before = _rg_to_dict(existing)
        db.execute(
            "UPDATE ring_groups SET name=?, strategy=?, members=?, ring_time=?, "
            "greeting_announcement=?, moh_class=?, noanswer_announcement=?, "
            "noanswer_action=?, noanswer_target=? WHERE extension=?",
            (data["name"], data["strategy"], data["members"], int(data["ring_time"]),
             data["greeting_announcement"], data["moh_class"], data["noanswer_announcement"],
             data["noanswer_action"], data["noanswer_target"], extension),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config()
        log_action("ring_group_update", target=extension, before=before, after=data,
                    username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("ringgroups.ui_list"))

    group = _rg_to_dict(existing)
    return render_template("ringgroups_form.html", group=group, is_new=False,
                           extensions=_get_extensions(), moh_classes=_get_moh_classes(),
                           announcements=_get_announcements())


@ringgroups_bp.route("/ring-groups/<extension>/delete", methods=["POST"])
@login_required
def ui_delete(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM ring_groups WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        flash("Ring group not found.", "danger")
        return redirect(url_for("ringgroups.ui_list"))

    before = _rg_to_dict(existing)
    db.execute("DELETE FROM ring_groups WHERE extension = ?", (extension,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config()
    log_action("ring_group_delete", target=extension, before=before,
               username=username, status="ok" if ok else "error")
    flash(f"Ring group {extension} deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("ringgroups.ui_list"))
