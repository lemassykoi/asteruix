"""Music on Hold management — API + UI routes."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)

from app.apply import safe_apply
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import write_musiconhold_classes

moh_bp = Blueprint("moh", __name__)

MOH_BASE_DIR = "/var/lib/asterisk"
ALLOWED_EXTENSIONS = {"mp3", "wav", "ogg"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Sanitize a filename to a safe slug."""
    base = os.path.splitext(name)[0]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", base).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:64] if slug else "upload"


def _get_class_dir(class_name: str) -> str:
    """Return the filesystem directory for a MoH class."""
    db = get_db()
    row = db.execute("SELECT directory FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if row:
        return row["directory"]
    return os.path.join(MOH_BASE_DIR, f"moh-{class_name}")


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


def _validate_class(data: dict, is_new: bool = True) -> list[str]:
    errors = []
    name = data.get("name", "").strip()
    if is_new:
        if not name:
            errors.append("Class name is required.")
        elif not NAME_RE.match(name):
            errors.append("Name must start with a letter, contain only letters/digits/hyphens/underscores, max 32 chars.")
    directory = data.get("directory", "").strip()
    if not directory:
        errors.append("Directory path is required.")
    if directory and not directory.startswith("/var/lib/asterisk/"):
        errors.append("Directory must be under /var/lib/asterisk/.")
    if directory and ".." in directory:
        errors.append("Directory must not contain '..'.")
    return errors


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed MoH config, reload Asterisk."""
    return safe_apply(
        label="pre-moh-apply",
        writers=[write_musiconhold_classes],
        reload_commands=["moh reload"],
    )


def _class_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


def _track_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# JSON API — /api/v1/moh
# ---------------------------------------------------------------------------

@moh_bp.route("/api/v1/moh/classes", methods=["GET"])
@login_required
def api_list_classes():
    db = get_db()
    classes = db.execute("SELECT * FROM moh_classes ORDER BY name").fetchall()
    result = []
    for c in classes:
        d = _class_to_dict(c)
        tracks = db.execute(
            "SELECT * FROM moh_tracks WHERE class_name = ? ORDER BY filename",
            (c["name"],),
        ).fetchall()
        d["tracks"] = [_track_to_dict(t) for t in tracks]
        result.append(d)
    return jsonify(result)


@moh_bp.route("/api/v1/moh/classes", methods=["POST"])
@login_required
def api_create_class():
    data = request.get_json(force=True)
    if not data.get("directory"):
        data["directory"] = os.path.join(MOH_BASE_DIR, f"moh-{data.get('name', '')}")
    errors = _validate_class(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()
    directory = data["directory"].strip()

    if db.execute("SELECT 1 FROM moh_classes WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"Class '{name}' already exists."]}), 409

    os.makedirs(directory, exist_ok=True)
    _chown_asterisk(directory)

    db.execute("INSERT INTO moh_classes (name, directory) VALUES (?, ?)", (name, directory))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_class_create", target=name, after=data, username=username,
               status="ok" if ok else "error")

    return jsonify({"class": {"name": name, "directory": directory}, "applied": ok, "message": msg}), 201


@moh_bp.route("/api/v1/moh/classes/<name>", methods=["PUT"])
@login_required
def api_update_class(name):
    db = get_db()
    existing = db.execute("SELECT * FROM moh_classes WHERE name = ?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data["name"] = name
    if not data.get("directory"):
        data["directory"] = existing["directory"]
    errors = _validate_class(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    directory = data["directory"].strip()
    os.makedirs(directory, exist_ok=True)
    _chown_asterisk(directory)

    db.execute("UPDATE moh_classes SET directory = ? WHERE name = ?", (directory, name))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_class_update", target=name, after=data, username=username,
               status="ok" if ok else "error")

    return jsonify({"class": {"name": name, "directory": directory}, "applied": ok, "message": msg})


@moh_bp.route("/api/v1/moh/classes/<name>", methods=["DELETE"])
@login_required
def api_delete_class(name):
    db = get_db()
    existing = db.execute("SELECT * FROM moh_classes WHERE name = ?", (name,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    db.execute("DELETE FROM moh_tracks WHERE class_name = ?", (name,))
    db.execute("DELETE FROM moh_classes WHERE name = ?", (name,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_class_delete", target=name, username=username,
               status="ok" if ok else "error")

    return jsonify({"deleted": name, "applied": ok, "message": msg})


@moh_bp.route("/api/v1/moh/classes/<name>/tracks", methods=["POST"])
@login_required
def api_upload_track(name):
    db = get_db()
    cls = db.execute("SELECT * FROM moh_classes WHERE name = ?", (name,)).fetchone()
    if not cls:
        return jsonify({"error": "Class not found"}), 404

    if "file" not in request.files:
        return jsonify({"errors": ["No file provided."]}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"errors": ["No file selected."]}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"errors": [f"File type '.{ext}' not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}"]}), 400

    # Save upload to temp file
    fd, tmp_src = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    f.save(tmp_src)

    # Check size
    if os.path.getsize(tmp_src) > MAX_UPLOAD_SIZE:
        os.unlink(tmp_src)
        return jsonify({"errors": ["File too large (max 50 MB)."]}), 400

    slug = _safe_filename(f.filename)
    dest_filename = f"{slug}.wav16"
    dest_path = os.path.join(cls["directory"], dest_filename)

    # Avoid overwriting
    counter = 1
    while os.path.exists(dest_path):
        dest_filename = f"{slug}-{counter}.wav16"
        dest_path = os.path.join(cls["directory"], dest_filename)
        counter += 1

    if not _convert_to_wav16(tmp_src, dest_path):
        os.unlink(tmp_src)
        return jsonify({"errors": ["Audio conversion failed. Check file format."]}), 422

    os.unlink(tmp_src)
    _chown_asterisk(dest_path)

    duration = _get_duration(dest_path)
    db.execute(
        "INSERT INTO moh_tracks (class_name, filename, duration_sec) VALUES (?, ?, ?)",
        (name, dest_filename, duration),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_track_upload", target=f"{name}/{dest_filename}", username=username,
               status="ok" if ok else "error")

    track = db.execute(
        "SELECT * FROM moh_tracks WHERE class_name = ? AND filename = ?",
        (name, dest_filename),
    ).fetchone()

    return jsonify({"track": _track_to_dict(track), "applied": ok, "message": msg}), 201


@moh_bp.route("/api/v1/moh/classes/<name>/tracks/<int:track_id>", methods=["DELETE"])
@login_required
def api_delete_track(name, track_id):
    db = get_db()
    track = db.execute(
        "SELECT * FROM moh_tracks WHERE id = ? AND class_name = ?",
        (track_id, name),
    ).fetchone()
    if not track:
        return jsonify({"error": "Track not found"}), 404

    cls = db.execute("SELECT * FROM moh_classes WHERE name = ?", (name,)).fetchone()
    if cls:
        filepath = os.path.join(cls["directory"], track["filename"])
        if os.path.exists(filepath):
            os.unlink(filepath)

    db.execute("DELETE FROM moh_tracks WHERE id = ?", (track_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_track_delete", target=f"{name}/{track['filename']}", username=username,
               status="ok" if ok else "error")

    return jsonify({"deleted": track_id, "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@moh_bp.route("/moh")
@login_required
def ui_list():
    db = get_db()
    classes = db.execute("SELECT * FROM moh_classes ORDER BY name").fetchall()
    class_data = []
    for c in classes:
        tracks = db.execute(
            "SELECT * FROM moh_tracks WHERE class_name = ? ORDER BY filename",
            (c["name"],),
        ).fetchall()
        class_data.append({"cls": c, "tracks": tracks})
    return render_template("moh_list.html", classes=class_data)


@moh_bp.route("/moh/new", methods=["GET", "POST"])
@login_required
def ui_new_class():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        directory = request.form.get("directory", "").strip()
        if not directory:
            directory = os.path.join(MOH_BASE_DIR, f"moh-{name}")
        data = {"name": name, "directory": directory}
        errors = _validate_class(data, is_new=True)

        db = get_db()
        if db.execute("SELECT 1 FROM moh_classes WHERE name = ?", (name,)).fetchone():
            errors.append(f"Class '{name}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("moh_class_form.html", cls=data, is_new=True)

        os.makedirs(directory, exist_ok=True)
        _chown_asterisk(directory)

        db.execute("INSERT INTO moh_classes (name, directory) VALUES (?, ?)", (name, directory))
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("moh_class_create", target=name, after=data, username=username,
                    status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("moh.ui_list"))

    defaults = {"name": "", "directory": ""}
    return render_template("moh_class_form.html", cls=defaults, is_new=True)


@moh_bp.route("/moh/<class_name>/edit", methods=["GET", "POST"])
@login_required
def ui_edit_class(class_name):
    db = get_db()
    existing = db.execute("SELECT * FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if not existing:
        flash("MoH class not found.", "danger")
        return redirect(url_for("moh.ui_list"))

    if request.method == "POST":
        directory = request.form.get("directory", "").strip() or existing["directory"]
        data = {"name": class_name, "directory": directory}
        errors = _validate_class(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("moh_class_form.html", cls=data, is_new=False)

        os.makedirs(directory, exist_ok=True)
        _chown_asterisk(directory)

        db.execute("UPDATE moh_classes SET directory = ? WHERE name = ?", (directory, class_name))
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("moh_class_update", target=class_name, after=data, username=username,
                    status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("moh.ui_list"))

    return render_template("moh_class_form.html", cls=existing, is_new=False)


@moh_bp.route("/moh/<class_name>/delete", methods=["POST"])
@login_required
def ui_delete_class(class_name):
    db = get_db()
    existing = db.execute("SELECT * FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if not existing:
        flash("MoH class not found.", "danger")
        return redirect(url_for("moh.ui_list"))

    db.execute("DELETE FROM moh_tracks WHERE class_name = ?", (class_name,))
    db.execute("DELETE FROM moh_classes WHERE name = ?", (class_name,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_class_delete", target=class_name, username=username,
                status="ok" if ok else "error")
    flash(f"Class '{class_name}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("moh.ui_list"))


@moh_bp.route("/moh/<class_name>/upload", methods=["POST"])
@login_required
def ui_upload_track(class_name):
    db = get_db()
    cls = db.execute("SELECT * FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if not cls:
        flash("MoH class not found.", "danger")
        return redirect(url_for("moh.ui_list"))

    if "file" not in request.files or not request.files["file"].filename:
        flash("No file selected.", "danger")
        return redirect(url_for("moh.ui_list"))

    f = request.files["file"]
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        flash(f"File type '.{ext}' not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}", "danger")
        return redirect(url_for("moh.ui_list"))

    fd, tmp_src = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    f.save(tmp_src)

    if os.path.getsize(tmp_src) > MAX_UPLOAD_SIZE:
        os.unlink(tmp_src)
        flash("File too large (max 50 MB).", "danger")
        return redirect(url_for("moh.ui_list"))

    slug = _safe_filename(f.filename)
    dest_filename = f"{slug}.wav16"
    dest_path = os.path.join(cls["directory"], dest_filename)

    counter = 1
    while os.path.exists(dest_path):
        dest_filename = f"{slug}-{counter}.wav16"
        dest_path = os.path.join(cls["directory"], dest_filename)
        counter += 1

    if not _convert_to_wav16(tmp_src, dest_path):
        os.unlink(tmp_src)
        flash("Audio conversion failed. Check file format.", "danger")
        return redirect(url_for("moh.ui_list"))

    os.unlink(tmp_src)
    _chown_asterisk(dest_path)

    duration = _get_duration(dest_path)
    db.execute(
        "INSERT INTO moh_tracks (class_name, filename, duration_sec) VALUES (?, ?, ?)",
        (class_name, dest_filename, duration),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_track_upload", target=f"{class_name}/{dest_filename}", username=username,
                status="ok" if ok else "error")
    flash(f"Track '{dest_filename}' uploaded. {msg}", "info" if ok else "danger")
    return redirect(url_for("moh.ui_list"))


@moh_bp.route("/moh/<class_name>/tracks/<int:track_id>/delete", methods=["POST"])
@login_required
def ui_delete_track(class_name, track_id):
    db = get_db()
    track = db.execute(
        "SELECT * FROM moh_tracks WHERE id = ? AND class_name = ?",
        (track_id, class_name),
    ).fetchone()
    if not track:
        flash("Track not found.", "danger")
        return redirect(url_for("moh.ui_list"))

    cls = db.execute("SELECT * FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if cls:
        filepath = os.path.join(cls["directory"], track["filename"])
        if os.path.exists(filepath):
            os.unlink(filepath)

    db.execute("DELETE FROM moh_tracks WHERE id = ?", (track_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("moh_track_delete", target=f"{class_name}/{track['filename']}", username=username,
                status="ok" if ok else "error")
    flash(f"Track '{track['filename']}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("moh.ui_list"))


@moh_bp.route("/moh/<class_name>/tracks/<int:track_id>/play")
@login_required
def ui_play_track(class_name, track_id):
    db = get_db()
    track = db.execute(
        "SELECT * FROM moh_tracks WHERE id = ? AND class_name = ?",
        (track_id, class_name),
    ).fetchone()
    if not track:
        return "Not found", 404

    cls = db.execute("SELECT * FROM moh_classes WHERE name = ?", (class_name,)).fetchone()
    if not cls:
        return "Not found", 404

    filepath = os.path.join(cls["directory"], track["filename"])
    if not os.path.exists(filepath):
        return "File not found", 404

    return send_file(filepath, mimetype="audio/wav")
