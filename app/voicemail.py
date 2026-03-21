"""Voicemail operations — message listing, playback, delete + blast config."""

from __future__ import annotations

import configparser
import os
import re

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request,
    send_file, url_for,
)

from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db

voicemail_bp = Blueprint("voicemail", __name__)

VM_SPOOL = "/var/spool/asterisk/voicemail"
VM_CONTEXT = "default"
VM_FOLDERS = ("INBOX", "Old", "Urgent", "Work", "Family", "Friends", "Tmp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_mailbox_dir(mailbox: str, folder: str = "INBOX") -> str:
    return os.path.join(VM_SPOOL, VM_CONTEXT, mailbox, folder)


def _parse_msg_txt(txt_path: str) -> dict:
    """Parse an Asterisk voicemail msg*.txt metadata file."""
    meta = {}
    try:
        cp = configparser.ConfigParser()
        cp.read(txt_path)
        if cp.has_section("message"):
            for key in cp.options("message"):
                meta[key] = cp.get("message", key)
    except Exception:
        pass
    return meta


def _list_messages(mailbox: str, folder: str = "INBOX") -> list[dict]:
    """List all voicemail messages in a mailbox folder."""
    folder_dir = _get_mailbox_dir(mailbox, folder)
    if not os.path.isdir(folder_dir):
        return []

    messages = []
    seen = set()
    for fn in sorted(os.listdir(folder_dir)):
        m = re.match(r"^(msg\d+)\.txt$", fn)
        if not m:
            continue
        msg_id = m.group(1)
        if msg_id in seen:
            continue
        seen.add(msg_id)

        txt_path = os.path.join(folder_dir, fn)
        meta = _parse_msg_txt(txt_path)

        # Find audio file — prefer .wav, fallback to any available
        audio_file = None
        for ext in (".wav", ".wav49", ".WAV", ".gsm", ".g722"):
            candidate = os.path.join(folder_dir, f"{msg_id}{ext}")
            if os.path.exists(candidate):
                audio_file = f"{msg_id}{ext}"
                break

        duration = meta.get("duration", "")
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = None

        messages.append({
            "msg_id": msg_id,
            "callerid": meta.get("callerid", "Unknown"),
            "origdate": meta.get("origdate", ""),
            "origtime": meta.get("origtime", ""),
            "duration": duration,
            "audio_file": audio_file,
            "folder": folder,
        })

    return messages


def _count_messages(mailbox: str) -> dict[str, int]:
    """Return message count per folder for a mailbox."""
    counts = {}
    base = os.path.join(VM_SPOOL, VM_CONTEXT, mailbox)
    if not os.path.isdir(base):
        return counts
    for folder in VM_FOLDERS:
        folder_dir = os.path.join(base, folder)
        if os.path.isdir(folder_dir):
            count = len([f for f in os.listdir(folder_dir) if f.endswith(".txt")])
            if count > 0:
                counts[folder] = count
    return counts


def _validate_mailbox(mailbox: str) -> bool:
    return bool(re.match(r"^\d{3,6}$", mailbox))


def _validate_folder(folder: str) -> bool:
    return folder in VM_FOLDERS


def _validate_msg_id(msg_id: str) -> bool:
    return bool(re.match(r"^msg\d{4}$", msg_id))


# ---------------------------------------------------------------------------
# JSON API — /api/v1/voicemail
# ---------------------------------------------------------------------------

@voicemail_bp.route("/api/v1/voicemail/messages", methods=["GET"])
@login_required
def api_list_messages():
    mailbox = request.args.get("mailbox", "").strip()
    folder = request.args.get("folder", "INBOX").strip()

    if not mailbox or not _validate_mailbox(mailbox):
        return jsonify({"error": "Valid mailbox number required."}), 400
    if not _validate_folder(folder):
        return jsonify({"error": f"Invalid folder. Use one of: {', '.join(VM_FOLDERS)}"}), 400

    messages = _list_messages(mailbox, folder)
    return jsonify({"mailbox": mailbox, "folder": folder, "messages": messages})


@voicemail_bp.route("/api/v1/voicemail/messages/<mailbox>/<msg_id>/stream")
@login_required
def api_stream_message(mailbox, msg_id):
    if not _validate_mailbox(mailbox):
        return jsonify({"error": "Invalid mailbox"}), 400
    if not _validate_msg_id(msg_id):
        return jsonify({"error": "Invalid message ID"}), 400

    folder = request.args.get("folder", "INBOX").strip()
    if not _validate_folder(folder):
        return jsonify({"error": "Invalid folder"}), 400

    folder_dir = _get_mailbox_dir(mailbox, folder)

    # Find audio file
    for ext in (".wav", ".wav49", ".WAV", ".gsm", ".g722"):
        filepath = os.path.join(folder_dir, f"{msg_id}{ext}")
        if os.path.exists(filepath):
            mimetype = "audio/wav" if ext.lower() in (".wav", ".wav49") else "audio/basic"
            return send_file(filepath, mimetype=mimetype)

    return jsonify({"error": "Audio file not found"}), 404


@voicemail_bp.route("/api/v1/voicemail/messages/<mailbox>/<msg_id>", methods=["DELETE"])
@login_required
def api_delete_message(mailbox, msg_id):
    if not _validate_mailbox(mailbox):
        return jsonify({"error": "Invalid mailbox"}), 400
    if not _validate_msg_id(msg_id):
        return jsonify({"error": "Invalid message ID"}), 400

    folder = request.args.get("folder", "INBOX").strip()
    if not _validate_folder(folder):
        return jsonify({"error": "Invalid folder"}), 400

    folder_dir = _get_mailbox_dir(mailbox, folder)
    deleted = []

    for fn in os.listdir(folder_dir) if os.path.isdir(folder_dir) else []:
        if fn.startswith(f"{msg_id}."):
            filepath = os.path.join(folder_dir, fn)
            os.unlink(filepath)
            deleted.append(fn)

    if not deleted:
        return jsonify({"error": "Message not found"}), 404

    username = get_current_user() or "system"
    log_action("voicemail_message_delete", target=f"{mailbox}/{folder}/{msg_id}",
               username=username, status="ok")

    return jsonify({"deleted": msg_id, "mailbox": mailbox, "folder": folder, "files": deleted})


@voicemail_bp.route("/api/v1/voicemail/blast", methods=["GET"])
@login_required
def api_get_blast():
    db = get_db()
    row = db.execute("SELECT * FROM blast_config ORDER BY id LIMIT 1").fetchone()
    if not row:
        return jsonify({"mailbox_list": "", "voicemail_flags": "su"})
    return jsonify({k: row[k] for k in row.keys()})


@voicemail_bp.route("/api/v1/voicemail/blast", methods=["PUT"])
@login_required
def api_update_blast():
    data = request.get_json(force=True)
    mailbox_list = data.get("mailbox_list", "").strip()
    voicemail_flags = data.get("voicemail_flags", "su").strip()

    # Validate mailbox list format: mailbox&mailbox&... or comma-separated
    if mailbox_list:
        sep = "&" if "&" in mailbox_list else ","
        mailboxes = [m.strip() for m in mailbox_list.split(sep) if m.strip()]
        for mb in mailboxes:
            if not re.match(r"^\d{3,6}(@\w+)?$", mb):
                return jsonify({"error": f"Invalid mailbox in list: {mb}"}), 400
        mailbox_list = "&".join(m.split("@")[0] for m in mailboxes)

    # Validate flags
    valid_flags = set("bsuj")
    for c in voicemail_flags:
        if c not in valid_flags:
            return jsonify({"error": f"Invalid voicemail flag: '{c}'. Valid: b, s, u, j"}), 400

    db = get_db()
    existing = db.execute("SELECT * FROM blast_config ORDER BY id LIMIT 1").fetchone()
    before = {k: existing[k] for k in existing.keys()} if existing else None

    if existing:
        db.execute(
            "UPDATE blast_config SET mailbox_list = ?, voicemail_flags = ? WHERE id = ?",
            (mailbox_list, voicemail_flags, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO blast_config (mailbox_list, voicemail_flags) VALUES (?, ?)",
            (mailbox_list, voicemail_flags),
        )
    db.commit()

    username = get_current_user() or "system"
    after = {"mailbox_list": mailbox_list, "voicemail_flags": voicemail_flags}
    log_action("blast_config_update", target="blast", before=before, after=after,
               username=username, status="ok")

    return jsonify(after)


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@voicemail_bp.route("/voicemail")
@login_required
def ui_list():
    db = get_db()
    boxes = db.execute(
        "SELECT * FROM voicemail_boxes ORDER BY mailbox"
    ).fetchall()

    mailbox_data = []
    for box in boxes:
        counts = _count_messages(box["mailbox"])
        mailbox_data.append({
            "mailbox": box["mailbox"],
            "name": box["name"],
            "counts": counts,
            "total": sum(counts.values()),
        })

    return render_template("voicemail_list.html", mailboxes=mailbox_data)


@voicemail_bp.route("/voicemail/<mailbox>")
@login_required
def ui_mailbox(mailbox):
    if not _validate_mailbox(mailbox):
        flash("Invalid mailbox.", "danger")
        return redirect(url_for("voicemail.ui_list"))

    db = get_db()
    box = db.execute(
        "SELECT * FROM voicemail_boxes WHERE mailbox = ?", (mailbox,)
    ).fetchone()
    if not box:
        flash("Mailbox not found.", "danger")
        return redirect(url_for("voicemail.ui_list"))

    folder = request.args.get("folder", "INBOX")
    if not _validate_folder(folder):
        folder = "INBOX"

    messages = _list_messages(mailbox, folder)
    counts = _count_messages(mailbox)

    return render_template("voicemail_mailbox.html",
                           mailbox=mailbox, box=box, folder=folder,
                           messages=messages, counts=counts,
                           folders=VM_FOLDERS)


@voicemail_bp.route("/voicemail/<mailbox>/<msg_id>/play")
@login_required
def ui_play_message(mailbox, msg_id):
    if not _validate_mailbox(mailbox):
        return "Invalid mailbox", 400
    if not _validate_msg_id(msg_id):
        return "Invalid message ID", 400

    folder = request.args.get("folder", "INBOX")
    if not _validate_folder(folder):
        return "Invalid folder", 400

    folder_dir = _get_mailbox_dir(mailbox, folder)
    for ext in (".wav", ".wav49", ".WAV", ".gsm", ".g722"):
        filepath = os.path.join(folder_dir, f"{msg_id}{ext}")
        if os.path.exists(filepath):
            mimetype = "audio/wav" if ext.lower() in (".wav", ".wav49") else "audio/basic"
            return send_file(filepath, mimetype=mimetype)

    return "Audio file not found", 404


@voicemail_bp.route("/voicemail/<mailbox>/<msg_id>/delete", methods=["POST"])
@login_required
def ui_delete_message(mailbox, msg_id):
    if not _validate_mailbox(mailbox):
        flash("Invalid mailbox.", "danger")
        return redirect(url_for("voicemail.ui_list"))
    if not _validate_msg_id(msg_id):
        flash("Invalid message ID.", "danger")
        return redirect(url_for("voicemail.ui_mailbox", mailbox=mailbox))

    folder = request.form.get("folder", "INBOX")
    if not _validate_folder(folder):
        folder = "INBOX"

    folder_dir = _get_mailbox_dir(mailbox, folder)
    deleted = []
    if os.path.isdir(folder_dir):
        for fn in os.listdir(folder_dir):
            if fn.startswith(f"{msg_id}."):
                filepath = os.path.join(folder_dir, fn)
                os.unlink(filepath)
                deleted.append(fn)

    if deleted:
        username = get_current_user() or "system"
        log_action("voicemail_message_delete", target=f"{mailbox}/{folder}/{msg_id}",
                   username=username, status="ok")
        flash(f"Message {msg_id} deleted ({len(deleted)} files).", "info")
    else:
        flash("Message not found.", "danger")

    return redirect(url_for("voicemail.ui_mailbox", mailbox=mailbox, folder=folder))


@voicemail_bp.route("/voicemail/blast", methods=["GET", "POST"])
@login_required
def ui_blast():
    db = get_db()

    if request.method == "POST":
        mailbox_list = request.form.get("mailbox_list", "").strip()
        voicemail_flags = request.form.get("voicemail_flags", "su").strip()

        # Normalize separator
        if mailbox_list:
            sep = "&" if "&" in mailbox_list else ","
            mailboxes = [m.strip() for m in mailbox_list.split(sep) if m.strip()]
            errors = []
            for mb in mailboxes:
                if not re.match(r"^\d{3,6}$", mb):
                    errors.append(f"Invalid mailbox: {mb}")
            if errors:
                for e in errors:
                    flash(e, "danger")
                return redirect(url_for("voicemail.ui_blast"))
            mailbox_list = "&".join(mailboxes)

        valid_flags = set("bsuj")
        for c in voicemail_flags:
            if c not in valid_flags:
                flash(f"Invalid voicemail flag: '{c}'. Valid flags: b, s, u, j", "danger")
                return redirect(url_for("voicemail.ui_blast"))

        existing = db.execute("SELECT * FROM blast_config ORDER BY id LIMIT 1").fetchone()
        before = {k: existing[k] for k in existing.keys()} if existing else None

        if existing:
            db.execute(
                "UPDATE blast_config SET mailbox_list = ?, voicemail_flags = ? WHERE id = ?",
                (mailbox_list, voicemail_flags, existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO blast_config (mailbox_list, voicemail_flags) VALUES (?, ?)",
                (mailbox_list, voicemail_flags),
            )
        db.commit()

        username = get_current_user() or "system"
        after = {"mailbox_list": mailbox_list, "voicemail_flags": voicemail_flags}
        log_action("blast_config_update", target="blast", before=before, after=after,
                   username=username, status="ok")
        flash("Blast voicemail configuration saved.", "info")
        return redirect(url_for("voicemail.ui_blast"))

    # GET
    blast = db.execute("SELECT * FROM blast_config ORDER BY id LIMIT 1").fetchone()
    boxes = db.execute("SELECT mailbox, name FROM voicemail_boxes ORDER BY mailbox").fetchall()

    blast_data = {
        "mailbox_list": blast["mailbox_list"] if blast else "",
        "voicemail_flags": blast["voicemail_flags"] if blast else "su",
    }
    return render_template("voicemail_blast.html", blast=blast_data, boxes=boxes)
