"""Backup & Restore — API + UI routes.

Create and restore Asterisk configuration backups using the system
backup/restore shell scripts.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.audit import log_action
from app.auth import get_current_user, login_required

backups_bp = Blueprint("backups", __name__)

BACKUP_DIR = "/root/asterisk-project/backups"
BACKUP_SCRIPT = "/usr/local/bin/asterisk-backup.sh"
RESTORE_SCRIPT = "/usr/local/bin/asterisk-restore.sh"

FILENAME_RE = re.compile(r"^asterisk-backup-\d{8}-\d{6}\.tar\.gz$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _human_size(nbytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _parse_backup_date(filename: str) -> str:
    """Extract a display date from the backup filename."""
    m = re.search(r"(\d{8})-(\d{6})", filename)
    if not m:
        return ""
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def _list_backups() -> list[dict]:
    """Scan BACKUP_DIR for backup files, return sorted newest-first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    entries = []
    for name in os.listdir(BACKUP_DIR):
        if not FILENAME_RE.match(name):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append({
            "filename": name,
            "path": path,
            "size": _human_size(stat.st_size),
            "size_bytes": stat.st_size,
            "date": _parse_backup_date(name),
            "mtime": stat.st_mtime,
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# JSON API — /api/v1/backups
# ---------------------------------------------------------------------------

@backups_bp.route("/api/v1/backups", methods=["GET"])
@login_required
def api_list():
    backups = _list_backups()
    return jsonify([
        {
            "filename": b["filename"],
            "path": b["path"],
            "size": b["size"],
            "date": b["date"],
        }
        for b in backups
    ])


@backups_bp.route("/api/v1/backups/create", methods=["POST"])
@login_required
def api_create():
    username = get_current_user() or "system"
    try:
        result = subprocess.run(
            ["sudo", BACKUP_SCRIPT],
            capture_output=True, text=True, timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output = "Backup timed out after 60 seconds."
        success = False
    except OSError as exc:
        output = f"Failed to run backup script: {exc}"
        success = False

    log_action(
        "backup_create",
        target="backup",
        after={"output": output},
        username=username,
        status="ok" if success else "fail",
    )

    status_code = 200 if success else 500
    return jsonify({"success": success, "output": output}), status_code


@backups_bp.route("/api/v1/backups/restore", methods=["POST"])
@login_required
def api_restore():
    data = request.get_json(force=True)
    filename = data.get("filename", "")
    confirm = data.get("confirm", "")

    if confirm != "RESTORE":
        return jsonify({"error": "Confirmation required. Set confirm to 'RESTORE'."}), 400

    if not FILENAME_RE.match(filename):
        return jsonify({"error": "Invalid backup filename."}), 400

    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.isfile(backup_path):
        return jsonify({"error": "Backup file not found."}), 404

    username = get_current_user() or "system"
    try:
        result = subprocess.run(
            ["sudo", RESTORE_SCRIPT, backup_path],
            capture_output=True, text=True, timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output = "Restore timed out after 120 seconds."
        success = False
    except OSError as exc:
        output = f"Failed to run restore script: {exc}"
        success = False

    log_action(
        "backup_restore",
        target=filename,
        after={"output": output},
        username=username,
        status="ok" if success else "fail",
    )

    status_code = 200 if success else 500
    return jsonify({"success": success, "output": output}), status_code


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@backups_bp.route("/backups")
@login_required
def ui_list():
    backups = _list_backups()
    return render_template("backups.html", backups=backups)


@backups_bp.route("/backups/create", methods=["POST"])
@login_required
def ui_create():
    username = get_current_user() or "system"
    try:
        result = subprocess.run(
            ["sudo", BACKUP_SCRIPT],
            capture_output=True, text=True, timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output = "Backup timed out after 60 seconds."
        success = False
    except OSError as exc:
        output = f"Failed to run backup script: {exc}"
        success = False

    log_action(
        "backup_create",
        target="backup",
        after={"output": output},
        username=username,
        status="ok" if success else "fail",
    )

    if success:
        flash("Backup created successfully.", "info")
    else:
        flash(f"Backup failed: {output}", "danger")

    return redirect(url_for("backups.ui_list"))


@backups_bp.route("/backups/restore", methods=["POST"])
@login_required
def ui_restore():
    filename = request.form.get("filename", "")
    confirm = request.form.get("confirm", "")

    if confirm != "RESTORE":
        flash("You must type RESTORE to confirm.", "danger")
        return redirect(url_for("backups.ui_list"))

    if not FILENAME_RE.match(filename):
        flash("Invalid backup filename.", "danger")
        return redirect(url_for("backups.ui_list"))

    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.isfile(backup_path):
        flash("Backup file not found.", "danger")
        return redirect(url_for("backups.ui_list"))

    username = get_current_user() or "system"
    try:
        result = subprocess.run(
            ["sudo", RESTORE_SCRIPT, backup_path],
            capture_output=True, text=True, timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        output = "Restore timed out after 120 seconds."
        success = False
    except OSError as exc:
        output = f"Failed to run restore script: {exc}"
        success = False

    log_action(
        "backup_restore",
        target=filename,
        after={"output": output},
        username=username,
        status="ok" if success else "fail",
    )

    if success:
        flash(f"Restored from {filename} successfully.", "info")
    else:
        flash(f"Restore failed: {output}", "danger")

    return redirect(url_for("backups.ui_list"))
