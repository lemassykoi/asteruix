"""Settings management — API + UI routes."""

from __future__ import annotations

import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db

settings_bp = Blueprint("settings", __name__)

SENSITIVE_KEYS = {"smtp_password", "telegram_bot_token"}


def _mask(value: str) -> str:
    """Mask a sensitive value for display."""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _get_all_settings() -> dict:
    """Return all settings as a dict."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _get_settings_masked() -> dict:
    """Return all settings with sensitive values masked."""
    settings = _get_all_settings()
    for key in SENSITIVE_KEYS:
        if key in settings:
            settings[key] = _mask(settings[key])
    return settings


# ---------------------------------------------------------------------------
# JSON API — /api/v1/settings
# ---------------------------------------------------------------------------

@settings_bp.route("/api/v1/settings", methods=["GET"])
@login_required
def api_get():
    return jsonify(_get_settings_masked())


@settings_bp.route("/api/v1/settings", methods=["PUT"])
@login_required
def api_update():
    data = request.get_json(force=True)
    db = get_db()
    current = _get_all_settings()
    username = get_current_user() or "system"

    changed = {}
    for key, new_value in data.items():
        if key not in current:
            continue
        new_value = str(new_value).strip()
        if new_value == current[key]:
            continue
        # Skip masked values sent back unchanged
        if key in SENSITIVE_KEYS and new_value == _mask(current[key]):
            continue
        db.execute(
            "UPDATE settings SET value = ? WHERE key = ?",
            (new_value, key),
        )
        changed[key] = new_value

    if changed:
        db.commit()
        # Mask sensitive values in audit log
        audit_changed = {
            k: ("****" if k in SENSITIVE_KEYS else v)
            for k, v in changed.items()
        }
        log_action("settings_update", target="settings",
                   after=audit_changed, username=username)

    return jsonify({"updated": list(changed.keys())})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@settings_bp.route("/settings", methods=["GET", "POST"])
@login_required
def ui_settings():
    db = get_db()

    if request.method == "POST":
        current = _get_all_settings()
        username = get_current_user() or "system"
        changed = {}

        for key in current:
            form_val = request.form.get(key)
            if form_val is None:
                # Handle checkbox (telegram_enabled, smtp_tls) — unchecked = not in form
                if key in ("telegram_enabled", "smtp_tls"):
                    form_val = "0"
                else:
                    continue
            form_val = form_val.strip()
            # Skip masked values sent back unchanged
            if key in SENSITIVE_KEYS and form_val == _mask(current[key]):
                continue
            if form_val != current[key]:
                db.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (form_val, key),
                )
                changed[key] = form_val

        # Handle checkboxes that are checked (value = "1")
        for key in ("telegram_enabled", "smtp_tls"):
            if key in request.form and request.form[key].strip() == "1":
                if current.get(key) != "1":
                    db.execute(
                        "UPDATE settings SET value = ? WHERE key = ?",
                        ("1", key),
                    )
                    changed[key] = "1"

        if changed:
            db.commit()
            audit_changed = {
                k: ("****" if k in SENSITIVE_KEYS else v)
                for k, v in changed.items()
            }
            log_action("settings_update", target="settings",
                       after=audit_changed, username=username)
            flash("Settings saved.", "info")
        else:
            flash("No changes.", "info")

        return redirect(url_for("settings.ui_settings"))

    settings = _get_all_settings()
    masked = _get_settings_masked()
    return render_template("settings.html", settings=settings, masked=masked)
