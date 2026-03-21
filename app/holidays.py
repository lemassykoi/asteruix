"""Holidays (Time Conditions) CRUD — API + UI routes.

Manages fixed holidays (MMDD) and variable holidays (YYYYMMDD) directly
in AstDB.  No config file generation needed — changes take effect
immediately via ``database put``/``database del``.
"""

from __future__ import annotations

import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.asterisk_cmd import AsteriskCommandError, get_database, run_command
from app.audit import log_action
from app.auth import get_current_user, login_required

holidays_bp = Blueprint("holidays", __name__)

FIXED_FAMILY = "holidays-fixed"
VARIABLE_FAMILY = "holidays-variable"

MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
YYYYMMDD_RE = re.compile(r"^(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
VALUE_RE = re.compile(r"^[a-zA-Z0-9_ -]{0,64}$")

MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_mmdd(mmdd: str) -> str:
    """Format MMDD as 'Mon DD' for display."""
    mm, dd = int(mmdd[:2]), int(mmdd[2:])
    return f"{MONTH_NAMES[mm]} {dd:02d}"


def _format_yyyymmdd(yyyymmdd: str) -> str:
    """Format YYYYMMDD as 'YYYY-MM-DD' for display."""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _get_holidays(family: str) -> list[dict]:
    """Fetch holidays from AstDB, return list of {key, value}."""
    try:
        entries = get_database(family)
        return [{"key": e.key, "value": e.value} for e in entries]
    except AsteriskCommandError:
        return []


# ---------------------------------------------------------------------------
# JSON API — Fixed holidays /api/v1/holidays/fixed
# ---------------------------------------------------------------------------

@holidays_bp.route("/api/v1/holidays/fixed", methods=["GET"])
@login_required
def api_list_fixed():
    holidays = _get_holidays(FIXED_FAMILY)
    return jsonify(holidays)


@holidays_bp.route("/api/v1/holidays/fixed", methods=["POST"])
@login_required
def api_create_fixed():
    data = request.get_json(force=True)
    mmdd = data.get("key", "").strip()
    name = data.get("value", "").strip() or "1"

    if not MMDD_RE.match(mmdd):
        return jsonify({"errors": ["Invalid MMDD format. Use 4 digits, e.g. 0101 for Jan 1."]}), 400
    if not VALUE_RE.match(name):
        return jsonify({"errors": ["Invalid name. Only letters, digits, spaces, hyphens, underscores (max 64 chars)."]}), 400

    try:
        run_command(f"database put {FIXED_FAMILY} {mmdd} {name}")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("holiday_fixed_create", target=mmdd,
               after={"key": mmdd, "value": name}, username=username)
    return jsonify({"key": mmdd, "value": name, "message": "Fixed holiday added."}), 201


@holidays_bp.route("/api/v1/holidays/fixed/<mmdd>", methods=["DELETE"])
@login_required
def api_delete_fixed(mmdd):
    if not MMDD_RE.match(mmdd):
        return jsonify({"error": "Invalid MMDD format."}), 400

    try:
        run_command(f"database del {FIXED_FAMILY} {mmdd}")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("holiday_fixed_delete", target=mmdd,
               before={"key": mmdd}, username=username)
    return jsonify({"deleted": mmdd, "message": "Fixed holiday removed."})


# ---------------------------------------------------------------------------
# JSON API — Variable holidays /api/v1/holidays/variable
# ---------------------------------------------------------------------------

@holidays_bp.route("/api/v1/holidays/variable", methods=["GET"])
@login_required
def api_list_variable():
    holidays = _get_holidays(VARIABLE_FAMILY)
    return jsonify(holidays)


@holidays_bp.route("/api/v1/holidays/variable", methods=["POST"])
@login_required
def api_create_variable():
    data = request.get_json(force=True)
    yyyymmdd = data.get("key", "").strip()
    name = data.get("value", "").strip() or "1"

    if not YYYYMMDD_RE.match(yyyymmdd):
        return jsonify({"errors": ["Invalid YYYYMMDD format. Use 8 digits, e.g. 20260413."]}), 400
    if not VALUE_RE.match(name):
        return jsonify({"errors": ["Invalid name. Only letters, digits, spaces, hyphens, underscores (max 64 chars)."]}), 400

    try:
        run_command(f"database put {VARIABLE_FAMILY} {yyyymmdd} {name}")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("holiday_variable_create", target=yyyymmdd,
               after={"key": yyyymmdd, "value": name}, username=username)
    return jsonify({"key": yyyymmdd, "value": name, "message": "Variable holiday added."}), 201


@holidays_bp.route("/api/v1/holidays/variable/<yyyymmdd>", methods=["DELETE"])
@login_required
def api_delete_variable(yyyymmdd):
    if not YYYYMMDD_RE.match(yyyymmdd):
        return jsonify({"error": "Invalid YYYYMMDD format."}), 400

    try:
        run_command(f"database del {VARIABLE_FAMILY} {yyyymmdd}")
    except AsteriskCommandError as exc:
        return jsonify({"errors": [str(exc)]}), 502

    username = get_current_user() or "system"
    log_action("holiday_variable_delete", target=yyyymmdd,
               before={"key": yyyymmdd}, username=username)
    return jsonify({"deleted": yyyymmdd, "message": "Variable holiday removed."})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@holidays_bp.route("/holidays")
@login_required
def ui_list():
    fixed = _get_holidays(FIXED_FAMILY)
    variable = _get_holidays(VARIABLE_FAMILY)

    # Enrich with display labels
    for h in fixed:
        h["display"] = _format_mmdd(h["key"])
    for h in variable:
        h["display"] = _format_yyyymmdd(h["key"])

    # Sort: fixed by MMDD, variable by YYYYMMDD
    fixed.sort(key=lambda h: h["key"])
    variable.sort(key=lambda h: h["key"])

    return render_template("holidays_list.html", fixed=fixed, variable=variable)


@holidays_bp.route("/holidays/fixed/add", methods=["POST"])
@login_required
def ui_add_fixed():
    month = request.form.get("month", "").strip()
    day = request.form.get("day", "").strip()
    name = request.form.get("name", "").strip() or "1"

    mmdd = f"{month}{day}"
    if not MMDD_RE.match(mmdd):
        flash("Invalid date. Month must be 01-12, day must be 01-31.", "danger")
        return redirect(url_for("holidays.ui_list"))
    if not VALUE_RE.match(name):
        flash("Invalid name. Only letters, digits, spaces, hyphens, underscores (max 64 chars).", "danger")
        return redirect(url_for("holidays.ui_list"))

    try:
        run_command(f"database put {FIXED_FAMILY} {mmdd} {name}")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("holidays.ui_list"))

    username = get_current_user() or "system"
    log_action("holiday_fixed_create", target=mmdd,
               after={"key": mmdd, "value": name}, username=username)
    flash(f"Fixed holiday {_format_mmdd(mmdd)} ({name}) added.", "info")
    return redirect(url_for("holidays.ui_list"))


@holidays_bp.route("/holidays/fixed/<mmdd>/delete", methods=["POST"])
@login_required
def ui_delete_fixed(mmdd):
    if not MMDD_RE.match(mmdd):
        flash("Invalid MMDD format.", "danger")
        return redirect(url_for("holidays.ui_list"))

    try:
        run_command(f"database del {FIXED_FAMILY} {mmdd}")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("holidays.ui_list"))

    username = get_current_user() or "system"
    log_action("holiday_fixed_delete", target=mmdd,
               before={"key": mmdd}, username=username)
    flash(f"Fixed holiday {_format_mmdd(mmdd)} removed.", "info")
    return redirect(url_for("holidays.ui_list"))


@holidays_bp.route("/holidays/variable/add", methods=["POST"])
@login_required
def ui_add_variable():
    date_str = request.form.get("date", "").strip().replace("-", "")
    name = request.form.get("name", "").strip() or "1"

    if not YYYYMMDD_RE.match(date_str):
        flash("Invalid date. Use YYYY-MM-DD format with a year 20xx.", "danger")
        return redirect(url_for("holidays.ui_list"))
    if not VALUE_RE.match(name):
        flash("Invalid name. Only letters, digits, spaces, hyphens, underscores (max 64 chars).", "danger")
        return redirect(url_for("holidays.ui_list"))

    try:
        run_command(f"database put {VARIABLE_FAMILY} {date_str} {name}")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("holidays.ui_list"))

    username = get_current_user() or "system"
    log_action("holiday_variable_create", target=date_str,
               after={"key": date_str, "value": name}, username=username)
    flash(f"Variable holiday {_format_yyyymmdd(date_str)} ({name}) added.", "info")
    return redirect(url_for("holidays.ui_list"))


@holidays_bp.route("/holidays/variable/<yyyymmdd>/delete", methods=["POST"])
@login_required
def ui_delete_variable(yyyymmdd):
    if not YYYYMMDD_RE.match(yyyymmdd):
        flash("Invalid YYYYMMDD format.", "danger")
        return redirect(url_for("holidays.ui_list"))

    try:
        run_command(f"database del {VARIABLE_FAMILY} {yyyymmdd}")
    except AsteriskCommandError as exc:
        flash(f"AstDB error: {exc}", "danger")
        return redirect(url_for("holidays.ui_list"))

    username = get_current_user() or "system"
    log_action("holiday_variable_delete", target=yyyymmdd,
               before={"key": yyyymmdd}, username=username)
    flash(f"Variable holiday {_format_yyyymmdd(yyyymmdd)} removed.", "info")
    return redirect(url_for("holidays.ui_list"))
