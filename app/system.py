"""System status API endpoints and dashboard route."""

from dataclasses import asdict

from flask import Blueprint, jsonify, render_template

from app.auth import login_required
from app.asterisk_cmd import (
    AsteriskCommandError,
    get_channels,
    get_endpoints,
    get_fail2ban_status,
    get_uptime,
    get_version,
)

system_bp = Blueprint("system", __name__)


# ---------------------------------------------------------------------------
# JSON API endpoints — /api/v1/system/*
# ---------------------------------------------------------------------------

@system_bp.route("/api/v1/system/status")
@login_required
def api_status():
    """Asterisk version, uptime, service state, fail2ban jail."""
    data: dict = {}
    try:
        data["version"] = asdict(get_version())
    except AsteriskCommandError as exc:
        data["version"] = {"error": str(exc)}

    try:
        data["uptime"] = asdict(get_uptime())
    except AsteriskCommandError as exc:
        data["uptime"] = {"error": str(exc)}

    data["fail2ban"] = asdict(get_fail2ban_status())
    return jsonify(data)


@system_bp.route("/api/v1/system/endpoints")
@login_required
def api_endpoints():
    """Registered/unregistered PJSIP endpoints."""
    try:
        eps = get_endpoints()
        return jsonify([
            {
                "name": ep.name,
                "caller_id": ep.caller_id,
                "state": ep.state,
                "channel_count": ep.channel_count,
                "contacts": [
                    {"uri": c.uri, "status": c.status, "rtt_ms": c.rtt_ms}
                    for c in ep.contacts
                ],
            }
            for ep in eps
        ])
    except AsteriskCommandError as exc:
        return jsonify({"error": str(exc)}), 502


@system_bp.route("/api/v1/system/calls")
@login_required
def api_calls():
    """Active channels/bridges."""
    try:
        chs = get_channels()
        return jsonify([
            {
                "channel": ch.channel,
                "context": ch.context,
                "extension": ch.extension,
                "state": ch.state,
                "application": ch.application,
                "caller_id": ch.caller_id,
                "duration": ch.duration,
                "bridged_to": ch.bridged_to,
            }
            for ch in chs
        ])
    except AsteriskCommandError as exc:
        return jsonify({"error": str(exc)}), 502


# ---------------------------------------------------------------------------
# Dashboard page (server-rendered)
# ---------------------------------------------------------------------------

@system_bp.route("/dashboard")
@login_required
def dashboard():
    """Render dashboard with live system data."""
    ctx: dict = {"error": None}

    try:
        ctx["version"] = get_version()
    except AsteriskCommandError as exc:
        ctx["version"] = None
        ctx["error"] = str(exc)

    try:
        ctx["uptime"] = get_uptime()
    except AsteriskCommandError as exc:
        ctx["uptime"] = None
        ctx["error"] = str(exc)

    try:
        eps = get_endpoints()
        ctx["endpoints"] = eps
        ctx["registered_count"] = sum(1 for e in eps if e.contacts)
        ctx["total_count"] = len(eps)
    except AsteriskCommandError as exc:
        ctx["endpoints"] = []
        ctx["registered_count"] = 0
        ctx["total_count"] = 0
        ctx["error"] = str(exc)

    try:
        chs = get_channels()
        ctx["channels"] = chs
        ctx["call_count"] = len(chs)
    except AsteriskCommandError as exc:
        ctx["channels"] = []
        ctx["call_count"] = 0
        ctx["error"] = str(exc)

    ctx["fail2ban"] = get_fail2ban_status()

    return render_template("dashboard.html", **ctx)
