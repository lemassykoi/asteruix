"""Conference Room Settings — API + UI routes."""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import generate_confbridge_profiles, write_confbridge_profiles

conference_bp = Blueprint("conference", __name__)

EXT_RE = re.compile(r"^\d{3,6}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_room(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []

    ext = data.get("extension", "").strip()
    if is_new:
        if not ext:
            errors.append("Extension number is required.")
        elif not EXT_RE.match(ext):
            errors.append("Extension must be 3-6 digits.")

    max_members = data.get("max_members")
    try:
        max_members = int(max_members)
        if max_members < 1 or max_members > 100:
            errors.append("Max members must be between 1 and 100.")
    except (TypeError, ValueError):
        errors.append("Max members must be a number.")

    return errors


def _apply_config() -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    return safe_apply(
        label="pre-conference-apply",
        writers=[write_confbridge_profiles],
        reload_commands=["module reload app_confbridge.so"],
    )


def _room_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_moh_classes():
    """Fetch MoH classes for select dropdown."""
    db = get_db()
    return db.execute("SELECT name FROM moh_classes ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/conference/rooms
# ---------------------------------------------------------------------------

@conference_bp.route("/api/v1/conference/rooms", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM conference_rooms ORDER BY extension").fetchall()
    return jsonify([_room_to_dict(r) for r in rows])


@conference_bp.route("/api/v1/conference/rooms/<extension>", methods=["GET"])
@login_required
def api_get(extension):
    db = get_db()
    row = db.execute(
        "SELECT * FROM conference_rooms WHERE extension = ?", (extension,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_room_to_dict(row))


@conference_bp.route("/api/v1/conference/rooms/<extension>", methods=["PUT"])
@login_required
def api_update(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM conference_rooms WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["extension"] = extension
    errors = _validate_room(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _room_to_dict(existing)

    db.execute(
        "UPDATE conference_rooms SET max_members=?, moh_class=?, "
        "announce_join_leave=?, music_on_hold_when_empty=? "
        "WHERE extension=?",
        (
            int(data.get("max_members", existing["max_members"])),
            data.get("moh_class", existing["moh_class"]).strip(),
            1 if data.get("announce_join_leave") else 0,
            1 if data.get("music_on_hold_when_empty") else 0,
            extension,
        ),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config()
    status = "ok" if ok else "error"
    log_action("conference_room_update", target=extension,
               before=before, after=data, username=username, status=status)

    result = db.execute(
        "SELECT * FROM conference_rooms WHERE extension = ?", (extension,)
    ).fetchone()
    return jsonify({"room": _room_to_dict(result), "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@conference_bp.route("/conference")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM conference_rooms ORDER BY extension").fetchall()
    return render_template("conference_list.html", rooms=rows)


@conference_bp.route("/conference/<extension>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(extension):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM conference_rooms WHERE extension = ?", (extension,)
    ).fetchone()
    if not existing:
        flash("Conference room not found.", "danger")
        return redirect(url_for("conference.ui_list"))

    if request.method == "POST":
        data = {
            "extension": extension,
            "max_members": request.form.get("max_members", "10").strip(),
            "moh_class": request.form.get("moh_class", "default").strip(),
            "announce_join_leave": 1 if request.form.get("announce_join_leave") else 0,
            "music_on_hold_when_empty": 1 if request.form.get("music_on_hold_when_empty") else 0,
        }

        errors = _validate_room(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            moh_classes = _get_moh_classes()
            return render_template("conference_form.html", room=data,
                                   moh_classes=moh_classes)

        before = _room_to_dict(existing)
        db.execute(
            "UPDATE conference_rooms SET max_members=?, moh_class=?, "
            "announce_join_leave=?, music_on_hold_when_empty=? "
            "WHERE extension=?",
            (
                int(data["max_members"]),
                data["moh_class"],
                data["announce_join_leave"],
                data["music_on_hold_when_empty"],
                extension,
            ),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config()
        log_action("conference_room_update", target=extension,
                   before=before, after=data,
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("conference.ui_list"))

    room = _room_to_dict(existing)
    moh_classes = _get_moh_classes()
    return render_template("conference_form.html", room=room,
                           moh_classes=moh_classes)
