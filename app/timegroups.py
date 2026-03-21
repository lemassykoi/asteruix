"""Time Groups CRUD — API + UI routes."""

from __future__ import annotations

import json
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.asterisk_cmd import AsteriskCommandError, run_command
from app.audit import log_action
from app.auth import get_current_user, login_required
from app.db import get_db
from app.generators import write_timegroups
from app.snapshots import take_snapshot

timegroups_bp = Blueprint("timegroups", __name__)

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9 _-]{0,63}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_rule(rule: dict) -> list[str]:
    """Validate a single time rule dict {start, end, days}."""
    errors = []
    start = rule.get("start", "")
    end = rule.get("end", "")
    days = rule.get("days", [])

    if not TIME_RE.match(start):
        errors.append(f"Invalid start time: {start}")
    if not TIME_RE.match(end):
        errors.append(f"Invalid end time: {end}")

    if TIME_RE.match(start) and TIME_RE.match(end):
        if start >= end:
            errors.append(f"Start time {start} must be before end time {end}.")

    if not days:
        errors.append("At least one day must be selected.")
    else:
        for d in days:
            if d not in VALID_DAYS:
                errors.append(f"Invalid day: {d}")

    return errors


def _validate_timegroup(data: dict, is_new: bool = True) -> list[str]:
    """Return a list of validation errors (empty = OK)."""
    errors = []
    name = data.get("name", "").strip()
    if is_new:
        if not name:
            errors.append("Name is required.")
        elif not NAME_RE.match(name):
            errors.append("Name must start with a letter, max 64 chars, letters/digits/spaces/hyphens/underscores.")

    rules = data.get("rules", [])
    if not rules:
        errors.append("At least one time rule is required.")

    for i, rule in enumerate(rules):
        rule_errors = _validate_rule(rule)
        for e in rule_errors:
            errors.append(f"Rule {i+1}: {e}")

    # Check for overlapping rules on same days
    if len(rules) >= 2:
        for i in range(len(rules)):
            for j in range(i + 1, len(rules)):
                r1, r2 = rules[i], rules[j]
                shared_days = set(r1.get("days", [])) & set(r2.get("days", []))
                if shared_days:
                    s1, e1 = r1.get("start", ""), r1.get("end", "")
                    s2, e2 = r2.get("start", ""), r2.get("end", "")
                    if TIME_RE.match(s1) and TIME_RE.match(e1) and TIME_RE.match(s2) and TIME_RE.match(e2):
                        if s1 < e2 and s2 < e1:
                            days_str = ",".join(sorted(shared_days))
                            errors.append(f"Rules {i+1} and {j+1} overlap on {days_str}: {s1}-{e1} vs {s2}-{e2}")

    return errors


def _parse_rules_from_form(form) -> list[dict]:
    """Parse time rules from repeated form fields."""
    rules = []
    idx = 0
    while True:
        start = form.get(f"rule_{idx}_start", "").strip()
        end = form.get(f"rule_{idx}_end", "").strip()
        days = form.getlist(f"rule_{idx}_days")
        if not start and not end and not days:
            break
        rules.append({"start": start, "end": end, "days": days})
        idx += 1
    return rules


def _apply_config(username: str) -> tuple[bool, str]:
    """Write managed files, reload Asterisk, return (success, message)."""
    take_snapshot("pre-timegroup-apply")
    write_timegroups()
    try:
        run_command("dialplan reload")
        return True, "Config applied and dialplan reloaded."
    except AsteriskCommandError as exc:
        return False, f"Asterisk reload failed: {exc}"


def _tg_to_dict(row) -> dict:
    d = {k: row[k] for k in row.keys()}
    d["rules"] = json.loads(d.get("rules_json", "[]"))
    return d


def _format_days(days: list[str]) -> str:
    """Format day list to Asterisk-style day range (e.g. mon-sat)."""
    day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    sorted_days = sorted(days, key=lambda d: day_order.index(d) if d in day_order else 99)
    if not sorted_days:
        return "*"

    # Try to detect contiguous ranges
    indices = [day_order.index(d) for d in sorted_days]
    if len(indices) == 7:
        return "*"
    if indices == list(range(indices[0], indices[0] + len(indices))):
        return f"{day_order[indices[0]]}-{day_order[indices[-1]]}"

    return "&".join(sorted_days)


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/time-groups
# ---------------------------------------------------------------------------

@timegroups_bp.route("/api/v1/time-groups", methods=["GET"])
@login_required
def api_list():
    db = get_db()
    rows = db.execute("SELECT * FROM time_groups ORDER BY name").fetchall()
    return jsonify([_tg_to_dict(r) for r in rows])


@timegroups_bp.route("/api/v1/time-groups", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True)
    data.setdefault("rules", [])
    errors = _validate_timegroup(data, is_new=True)
    if errors:
        return jsonify({"errors": errors}), 400

    db = get_db()
    name = data["name"].strip()

    if db.execute("SELECT 1 FROM time_groups WHERE name = ?", (name,)).fetchone():
        return jsonify({"errors": [f"Time group '{name}' already exists."]}), 409

    rules_json = json.dumps(data["rules"])
    timezone = data.get("timezone", "Europe/Paris").strip()

    db.execute(
        "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?)",
        (name, timezone, rules_json),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("timegroup_create", target=name,
               after={"name": name, "timezone": timezone, "rules": data["rules"]},
               username=username, status=status)

    result = db.execute("SELECT * FROM time_groups WHERE name = ?", (name,)).fetchone()
    code = 201 if ok else 207
    return jsonify({"time_group": _tg_to_dict(result), "applied": ok, "message": msg}), code


@timegroups_bp.route("/api/v1/time-groups/<int:tg_id>", methods=["GET"])
@login_required
def api_get(tg_id):
    db = get_db()
    row = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_tg_to_dict(row))


@timegroups_bp.route("/api/v1/time-groups/<int:tg_id>", methods=["PUT"])
@login_required
def api_update(tg_id):
    db = get_db()
    existing = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    data.setdefault("rules", json.loads(existing["rules_json"]))
    data["name"] = existing["name"]  # name is immutable
    errors = _validate_timegroup(data, is_new=False)
    if errors:
        return jsonify({"errors": errors}), 400

    before = _tg_to_dict(existing)
    rules_json = json.dumps(data["rules"])
    timezone = data.get("timezone", existing["timezone"]).strip()

    db.execute(
        "UPDATE time_groups SET timezone = ?, rules_json = ? WHERE id = ?",
        (timezone, rules_json, tg_id),
    )
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("timegroup_update", target=existing["name"],
               before={"timezone": before["timezone"], "rules": before["rules"]},
               after={"timezone": timezone, "rules": data["rules"]},
               username=username, status=status)

    result = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    return jsonify({"time_group": _tg_to_dict(result), "applied": ok, "message": msg})


@timegroups_bp.route("/api/v1/time-groups/<int:tg_id>", methods=["DELETE"])
@login_required
def api_delete(tg_id):
    db = get_db()
    existing = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    if not existing:
        return jsonify({"error": "Not found"}), 404

    # Check if referenced by inbound routes
    ref = db.execute(
        "SELECT name FROM inbound_routes WHERE time_group_id = ?", (tg_id,)
    ).fetchone()
    if ref:
        return jsonify({"errors": [f"Cannot delete: referenced by inbound route '{ref['name']}'."]}), 409

    before = _tg_to_dict(existing)
    db.execute("DELETE FROM time_groups WHERE id = ?", (tg_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    status = "ok" if ok else "error"
    log_action("timegroup_delete", target=before["name"], before=before,
               username=username, status=status)

    return jsonify({"deleted": before["name"], "applied": ok, "message": msg})


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@timegroups_bp.route("/time-groups")
@login_required
def ui_list():
    db = get_db()
    rows = db.execute("SELECT * FROM time_groups ORDER BY name").fetchall()
    groups = []
    for r in rows:
        d = _tg_to_dict(r)
        # Build human-readable summary
        summaries = []
        for rule in d["rules"]:
            days = _format_days(rule.get("days", []))
            summaries.append(f"{rule['start']}–{rule['end']} {days}")
        d["summary"] = " / ".join(summaries) if summaries else "No rules"
        groups.append(d)
    return render_template("timegroups_list.html", groups=groups)


@timegroups_bp.route("/time-groups/new", methods=["GET", "POST"])
@login_required
def ui_new():
    if request.method == "POST":
        rules = _parse_rules_from_form(request.form)
        data = {
            "name": request.form.get("name", "").strip(),
            "timezone": request.form.get("timezone", "Europe/Paris").strip(),
            "rules": rules,
        }
        errors = _validate_timegroup(data, is_new=True)
        db = get_db()
        if db.execute("SELECT 1 FROM time_groups WHERE name = ?", (data["name"],)).fetchone():
            errors.append(f"Time group '{data['name']}' already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("timegroups_form.html", tg=data, is_new=True)

        rules_json = json.dumps(rules)
        db.execute(
            "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?)",
            (data["name"], data["timezone"], rules_json),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("timegroup_create", target=data["name"],
                   after=data, username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("timegroups.ui_list"))

    # GET — blank form with one default rule
    defaults = {
        "name": "",
        "timezone": "Europe/Paris",
        "rules": [{"start": "08:30", "end": "12:30", "days": ["mon", "tue", "wed", "thu", "fri", "sat"]}],
    }
    return render_template("timegroups_form.html", tg=defaults, is_new=True)


@timegroups_bp.route("/time-groups/<int:tg_id>/edit", methods=["GET", "POST"])
@login_required
def ui_edit(tg_id):
    db = get_db()
    existing = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    if not existing:
        flash("Time group not found.", "danger")
        return redirect(url_for("timegroups.ui_list"))

    if request.method == "POST":
        rules = _parse_rules_from_form(request.form)
        data = {
            "name": existing["name"],
            "timezone": request.form.get("timezone", "Europe/Paris").strip(),
            "rules": rules,
        }
        errors = _validate_timegroup(data, is_new=False)
        if errors:
            for e in errors:
                flash(e, "danger")
            data["id"] = tg_id
            return render_template("timegroups_form.html", tg=data, is_new=False)

        before = _tg_to_dict(existing)
        rules_json = json.dumps(rules)
        db.execute(
            "UPDATE time_groups SET timezone = ?, rules_json = ? WHERE id = ?",
            (data["timezone"], rules_json, tg_id),
        )
        db.commit()

        username = get_current_user() or "system"
        ok, msg = _apply_config(username)
        log_action("timegroup_update", target=existing["name"],
                   before={"timezone": before["timezone"], "rules": before["rules"]},
                   after={"timezone": data["timezone"], "rules": rules},
                   username=username, status="ok" if ok else "error")
        flash(msg, "info" if ok else "danger")
        return redirect(url_for("timegroups.ui_list"))

    tg = _tg_to_dict(existing)
    tg["id"] = existing["id"]
    return render_template("timegroups_form.html", tg=tg, is_new=False)


@timegroups_bp.route("/time-groups/<int:tg_id>/delete", methods=["POST"])
@login_required
def ui_delete(tg_id):
    db = get_db()
    existing = db.execute("SELECT * FROM time_groups WHERE id = ?", (tg_id,)).fetchone()
    if not existing:
        flash("Time group not found.", "danger")
        return redirect(url_for("timegroups.ui_list"))

    # Check if referenced by inbound routes
    ref = db.execute(
        "SELECT name FROM inbound_routes WHERE time_group_id = ?", (tg_id,)
    ).fetchone()
    if ref:
        flash(f"Cannot delete: referenced by inbound route '{ref['name']}'.", "danger")
        return redirect(url_for("timegroups.ui_list"))

    before = _tg_to_dict(existing)
    db.execute("DELETE FROM time_groups WHERE id = ?", (tg_id,))
    db.commit()

    username = get_current_user() or "system"
    ok, msg = _apply_config(username)
    log_action("timegroup_delete", target=before["name"], before=before,
               username=username, status="ok" if ok else "error")
    flash(f"Time group '{before['name']}' deleted. {msg}", "info" if ok else "danger")
    return redirect(url_for("timegroups.ui_list"))
