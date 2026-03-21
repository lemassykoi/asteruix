"""Spam Prefix Blacklist CRUD — API + UI routes.

Manages 4-digit spam prefixes directly in AstDB family ``spam-prefix``.
No config file generation needed — changes take effect immediately.
"""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.asterisk_cmd import AsteriskCommandError, get_database, run_command
from app.audit import log_action
from app.auth import get_current_user, login_required

spam_bp = Blueprint("spam", __name__)

SPAM_FAMILY = "spam-prefix"
PREFIX_RE = re.compile(r"^[0-9]{4}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_prefixes() -> list[dict]:
    """Fetch spam prefixes from AstDB, return list of {key, value}."""
    try:
        entries = get_database(SPAM_FAMILY)
        return [{"key": e.key, "value": e.value} for e in entries]
    except AsteriskCommandError:
        return []


# ---------------------------------------------------------------------------
# JSON API — /api/v1/spam-prefixes
# ---------------------------------------------------------------------------

@spam_bp.route("/api/v1/spam-prefixes", methods=["GET"])
@login_required
def api_list():
    prefixes = _get_prefixes()
    return jsonify(prefixes)


@spam_bp.route("/api/v1/spam-prefixes", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    prefix = data.get("prefix", "").strip()

    if not PREFIX_RE.match(prefix):
        return jsonify({"errors": ["Invalid prefix. Must be exactly 4 digits."]}), 400

    try:
        run_command(f"database put {SPAM_FAMILY} {prefix} 1")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("spam_prefix_create", target=prefix,
               after={"prefix": prefix}, username=username)
    return jsonify({"prefix": prefix, "message": "Spam prefix added."}), 201


@spam_bp.route("/api/v1/spam-prefixes/<prefix>", methods=["DELETE"])
@login_required
def api_delete(prefix):
    if not PREFIX_RE.match(prefix):
        return jsonify({"error": "Invalid prefix. Must be exactly 4 digits."}), 400

    try:
        run_command(f"database del {SPAM_FAMILY} {prefix}")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("spam_prefix_delete", target=prefix,
               before={"prefix": prefix}, username=username)
    return jsonify({"deleted": prefix, "message": "Spam prefix removed."})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@spam_bp.route("/spam-prefixes")
@login_required
def ui_list():
    prefixes = _get_prefixes()
    prefixes.sort(key=lambda p: p["key"])
    return render_template("spam_list.html", prefixes=prefixes)


@spam_bp.route("/spam-prefixes/add", methods=["POST"])
@login_required
def ui_add():
    prefix = request.form.get("prefix", "").strip()

    if not PREFIX_RE.match(prefix):
        flash("Invalid prefix. Must be exactly 4 digits.", "danger")
        return redirect(url_for("spam.ui_list"))

    try:
        run_command(f"database put {SPAM_FAMILY} {prefix} 1")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("spam.ui_list"))

    username = get_current_user() or "system"
    log_action("spam_prefix_create", target=prefix,
               after={"prefix": prefix}, username=username)
    flash(f"Spam prefix {prefix} added.", "info")
    return redirect(url_for("spam.ui_list"))


@spam_bp.route("/spam-prefixes/bulk-import", methods=["POST"])
@login_required
def ui_bulk_import():
    raw = request.form.get("prefixes", "")
    # Accept comma, space, newline, semicolon as separators
    candidates = re.split(r"[,;\s]+", raw.strip())

    added = []
    invalid = []
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        if not PREFIX_RE.match(c):
            invalid.append(c)
            continue
        try:
            run_command(f"database put {SPAM_FAMILY} {c} 1")
            added.append(c)
        except AsteriskCommandError:
            invalid.append(c)

    username = get_current_user() or "system"
    if added:
        log_action("spam_prefix_bulk_import", target=f"{len(added)} prefixes",
                   after={"prefixes": added}, username=username)
        flash(f"Imported {len(added)} prefix(es): {', '.join(added)}", "info")
    if invalid:
        flash(f"Skipped {len(invalid)} invalid entry/entries: {', '.join(invalid)}", "danger")
    if not added and not invalid:
        flash("No prefixes provided.", "danger")

    return redirect(url_for("spam.ui_list"))


@spam_bp.route("/spam-prefixes/<prefix>/delete", methods=["POST"])
@login_required
def ui_delete(prefix):
    if not PREFIX_RE.match(prefix):
        flash("Invalid prefix format.", "danger")
        return redirect(url_for("spam.ui_list"))

    try:
        run_command(f"database del {SPAM_FAMILY} {prefix}")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("spam.ui_list"))

    username = get_current_user() or "system"
    log_action("spam_prefix_delete", target=prefix,
               before={"prefix": prefix}, username=username)
    flash(f"Spam prefix {prefix} removed.", "info")
    return redirect(url_for("spam.ui_list"))
