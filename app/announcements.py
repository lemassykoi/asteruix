"""Announcements management — API + UI routes."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)

from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db

announcements_bp = Blueprint("announcements", __name__)

ANNOUNCEMENTS_DIR = "/var/lib/asterisk/sounds/fr"
ALLOWED_EXTENSIONS = {"mp3", "wav", "ogg"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Sanitize a filename to a safe slug."""
    base = os.path.splitext(name)[0]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", base).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:64] if slug else "upload"


def _convert_to_wav16(src_path: str, dest_path: str) -> bool:
    """Convert audio file to 16kHz mono 16-bit WAV using sox, rename to .wav16."""
    wav_tmp = dest_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["sox", src_path, "-r", "16000", "-c", "1", "-b", "16", wav_tmp],
            check=True, capture_output=True, timeout=60,
        )
        os.rename(wav_tmp, dest_path)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if os.path.exists(wav_tmp):
            os.unlink(wav_tmp)
        return False


def _get_duration(filepath: str) -> float | None:
    """Get audio duration in seconds using soxi."""
    try:
        result = subprocess.run(
            ["soxi", "-D", filepath],
            capture_output=True, text=True, timeout=10,
        )
        return round(float(result.stdout.strip()), 1)
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired):
        return None


def _chown_asterisk(path: str):
    """Best-effort chown to asterisk:asterisk."""
    try:
        import pwd
        pw = pwd.getpwnam("asterisk")
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError):
        pass


def _validate_announcement(data: dict, is_new: bool = True) -> list[str]:
    errors = []
    key_name = data.get("key_name", "").strip()
    if is_new:
        if not key_name:
            errors.append("Key name is required.")
        elif not KEY_RE.match(key_name):
            errors.append("Key must start with a letter, contain only letters/digits/hyphens/underscores, max 64 chars.")
    return errors


def _ann_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# JSON API — /api/v1/announcements
# ---------------------------------------------------------------------------

@announcements_bp.route("/api/v1/announcements", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM announcements ORDER BY key_name").fetchall()
    result = []
    for r in rows:
        d = _ann_to_dict(r)
        filepath = os.path.join(ANNOUNCEMENTS_DIR, r["filename"])
        d["file_exists"] = os.path.exists(filepath)
        d["duration_sec"] = _get_duration(filepath) if d["file_exists"] else None
        result.append(d)
    return jsonify(result)


@announcements_bp.route("/api/v1/announcements", methods=["POST"])
@login_required
def api_create():
    if "file" not in request.files:
        return jsonify({"errors": ["No file provided."]}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"errors": ["No file selected."]}), 400

    key_name = request.form.get("key_name", "").strip()
    language = request.form.get("language", "fr").strip() or "fr"
    data = {"key_name": key_name}
    errors = _validate_announcement(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"errors": [f"File type '.{ext}' not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}"]}), 400

    db = get_db()
    if db.execute("SELECT 1 FROM announcements WHERE key_name = ?", (key_name,)).fetchone():
        return jsonify({"errors": [f"Announcement '{key_name}' already exists."]}), 409

    # Save upload to temp file
    fd, tmp_src = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    f.save(tmp_src)

    if os.path.getsize(tmp_src) > MAX_UPLOAD_SIZE:
        os.unlink(tmp_src)
        return jsonify({"errors": ["File too large (max 50 MB)."]}), 400

    dest_filename = f"{key_name}.wav16"
    dest_path = os.path.join(ANNOUNCEMENTS_DIR, dest_filename)

    if not _convert_to_wav16(tmp_src, dest_path):
        os.unlink(tmp_src)
        return jsonify({"errors": ["Audio conversion failed. Check file format."]}), 422

    os.unlink(tmp_src)
    _chown_asterisk(dest_path)

    duration = _get_duration(dest_path)

    db.execute(
        "INSERT INTO announcements (key_name, filename, language, active) VALUES (?, ?, ?, 0)",
        (key_name, dest_filename, language),
    )
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_create", target=key_name,
               after={"key_name": key_name, "filename": dest_filename, "language": language},
               username=username, status="ok")

    ann = db.execute("SELECT * FROM announcements WHERE key_name = ?", (key_name,)).fetchone()
    d = _ann_to_dict(ann)
    d["duration_sec"] = duration
    return jsonify(d), 201


@announcements_bp.route("/api/v1/announcements/<int:ann_id>", methods=["PUT"])
@login_required
def api_update(ann_id):
    db = get_db()
    existing = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    language = data.get("language", existing["language"]).strip()
    active = data.get("active")

    if active is not None:
        active = int(bool(active))
        # If activating, deactivate all others first
        if active:
            db.execute("UPDATE announcements SET active = 0")
        db.execute("UPDATE announcements SET language = ?, active = ? WHERE id = ?",
                   (language, active, ann_id))
    else:
        db.execute("UPDATE announcements SET language = ? WHERE id = ?",
                   (language, ann_id))
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_update", target=existing["key_name"],
               after={"language": language, "active": active},
               username=username, status="ok")

    updated = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    return jsonify(_ann_to_dict(updated))


@announcements_bp.route("/api/v1/announcements/<int:ann_id>", methods=["DELETE"])
@login_required
def api_delete(ann_id):
    db = get_db()
    existing = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    # Remove file from disk
    filepath = os.path.join(ANNOUNCEMENTS_DIR, existing["filename"])
    if os.path.exists(filepath):
        os.unlink(filepath)

    db.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_delete", target=existing["key_name"],
               username=username, status="ok")

    return jsonify({"deleted": ann_id, "key_name": existing["key_name"]})


@announcements_bp.route("/api/v1/announcements/<int:ann_id>/stream")
@login_required
def api_stream(ann_id):
    db = get_db()
    ann = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not ann:
        return jsonify({"error": "Not found"}), 404

    filepath = os.path.join(ANNOUNCEMENTS_DIR, ann["filename"])
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found on disk"}), 404

    return send_file(filepath, mimetype="audio/wav")


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

def _get_usage(db, key_name: str) -> list[str]:
    """Return a list of human-readable places where this announcement is used."""
    usages = []
    # Inbound routes: closed announcement
    for r in db.execute(
        "SELECT name FROM inbound_routes WHERE closed_announcement = ?", (key_name,)
    ).fetchall():
        usages.append(f"Inbound \"{r['name']}\" (closed)")
    # Ring groups: greeting
    for r in db.execute(
        "SELECT extension, name FROM ring_groups WHERE greeting_announcement = ?", (key_name,)
    ).fetchall():
        usages.append(f"Ring Group {r['extension']} \"{r['name']}\" (greeting)")
    # Ring groups: no-answer
    for r in db.execute(
        "SELECT extension, name FROM ring_groups WHERE noanswer_announcement = ?", (key_name,)
    ).fetchall():
        usages.append(f"Ring Group {r['extension']} \"{r['name']}\" (no-answer)")
    # IVR menus: greeting
    for r in db.execute(
        "SELECT name FROM ivr_menus WHERE greeting = ?", (key_name,)
    ).fetchall():
        usages.append(f"IVR \"{r['name']}\" (greeting)")
    return usages


@announcements_bp.route("/announcements")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM announcements ORDER BY key_name").fetchall()
    announcements = []
    for r in rows:
        d = dict(r)
        filepath = os.path.join(ANNOUNCEMENTS_DIR, r["filename"])
        d["file_exists"] = os.path.exists(filepath)
        d["duration_sec"] = _get_duration(filepath) if d["file_exists"] else None
        d["usages"] = _get_usage(db, r["key_name"])
        announcements.append(d)
    return render_template("announcements_list.html", announcements=announcements,
                           announcements_dir=ANNOUNCEMENTS_DIR)


@announcements_bp.route("/announcements/upload", methods=["POST"])
@login_required
def ui_upload():
    key_name = request.form.get("key_name", "").strip()
    language = request.form.get("language", "fr").strip() or "fr"
    data = {"key_name": key_name}
    errors = _validate_announcement(data, is_new=True)

    db = get_db()
    if db.execute("SELECT 1 FROM announcements WHERE key_name = ?", (key_name,)).fetchone():
        errors.append(f"Announcement '{key_name}' already exists.")

    if "file" not in request.files or not request.files["file"].filename:
        errors.append("No file selected.")

    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("announcements.ui_list"))

    f = request.files["file"]
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        flash(f"File type '.{ext}' not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}", "danger")
        return redirect(url_for("announcements.ui_list"))

    fd, tmp_src = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    f.save(tmp_src)

    if os.path.getsize(tmp_src) > MAX_UPLOAD_SIZE:
        os.unlink(tmp_src)
        flash("File too large (max 50 MB).", "danger")
        return redirect(url_for("announcements.ui_list"))

    dest_filename = f"{key_name}.wav16"
    dest_path = os.path.join(ANNOUNCEMENTS_DIR, dest_filename)

    if not _convert_to_wav16(tmp_src, dest_path):
        os.unlink(tmp_src)
        flash("Audio conversion failed. Check file format.", "danger")
        return redirect(url_for("announcements.ui_list"))

    os.unlink(tmp_src)
    _chown_asterisk(dest_path)

    duration = _get_duration(dest_path)
    db.execute(
        "INSERT INTO announcements (key_name, filename, language, active) VALUES (?, ?, ?, 0)",
        (key_name, dest_filename, language),
    )
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_create", target=key_name,
               after={"key_name": key_name, "filename": dest_filename, "language": language},
               username=username, status="ok")
    flash(f"Announcement '{key_name}' uploaded ({dest_filename}).", "info")
    return redirect(url_for("announcements.ui_list"))


@announcements_bp.route("/announcements/<int:ann_id>/activate", methods=["POST"])
@login_required
def ui_activate(ann_id):
    db = get_db()
    existing = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not existing:
        flash("Announcement not found.", "danger")
        return redirect(url_for("announcements.ui_list"))

    # Deactivate all, activate this one
    db.execute("UPDATE announcements SET active = 0")
    db.execute("UPDATE announcements SET active = 1 WHERE id = ?", (ann_id,))
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_activate", target=existing["key_name"],
               username=username, status="ok")
    flash(f"'{existing['key_name']}' set as active closed-hours announcement.", "info")
    return redirect(url_for("announcements.ui_list"))


@announcements_bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@login_required
def ui_delete(ann_id):
    db = get_db()
    existing = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not existing:
        flash("Announcement not found.", "danger")
        return redirect(url_for("announcements.ui_list"))

    filepath = os.path.join(ANNOUNCEMENTS_DIR, existing["filename"])
    if os.path.exists(filepath):
        os.unlink(filepath)

    db.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
    db.commit()

    username = get_current_user() or "system"
    log_action("announcement_delete", target=existing["key_name"],
               username=username, status="ok")
    flash(f"Announcement '{existing['key_name']}' deleted.", "info")
    return redirect(url_for("announcements.ui_list"))


@announcements_bp.route("/announcements/<int:ann_id>/play")
@login_required
def ui_play(ann_id):
    db = get_db()
    ann = db.execute("SELECT * FROM announcements WHERE id = ?", (ann_id,)).fetchone()
    if not ann:
        return "Not found", 404

    filepath = os.path.join(ANNOUNCEMENTS_DIR, ann["filename"])
    if not os.path.exists(filepath):
        return "File not found", 404

    return send_file(filepath, mimetype="audio/wav")
