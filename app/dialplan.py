"""Dialplan Visualization — read-only call-flow diagram + generated config."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, url_for

from app.auth import login_required
from app.db import get_db
from app.generators import generate_inbound_flow, generate_ring_groups, generate_timegroups

dialplan_bp = Blueprint("dialplan", __name__)

_MERMAID_MAX_LABEL = 120


def sanitize_mermaid_label(value: object) -> str:
    """Sanitize a DB-derived value for safe inclusion in a Mermaid label.

    - Converts None to empty string.
    - Removes/escapes characters that could break Mermaid syntax or inject markup.
    - Replaces newlines and semicolons with spaces.
    - Trims to a reasonable length.
    """
    if value is None:
        return ""
    text = str(value)
    # Collapse newlines / carriage returns / semicolons to spaces first
    text = text.replace("\n", " ").replace("\r", " ").replace(";", " ")
    # Strip characters that break Mermaid syntax or could carry markup/scripts
    for ch in "<>\"'`{}()[]":
        text = text.replace(ch, "")
    # Collapse runs of whitespace
    text = " ".join(text.split())
    return text[:_MERMAID_MAX_LABEL]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_graph() -> dict:
    """Build a node/edge graph from DB state describing the inbound call flow."""
    db = get_db()

    route = db.execute(
        "SELECT r.*, t.name AS tg_name, t.rules_json "
        "FROM inbound_routes r "
        "LEFT JOIN time_groups t ON r.time_group_id = t.id "
        "ORDER BY r.id LIMIT 1"
    ).fetchone()

    blast_row = None
    if route and route["blast_profile"]:
        blast_row = db.execute(
            "SELECT * FROM blast_config WHERE id = ?", (route["blast_profile"],)
        ).fetchone()

    # Defaults when no route is configured
    open_target = route["open_target"] if route else ""
    closed_announcement = route["closed_announcement"] if route else ""
    spam_family = route["spam_family"] if route else "spam-prefix"
    fixed_hol = route["fixed_holiday_family"] if route else "holidays-fixed"
    variable_hol = route["variable_holiday_family"] if route else "holidays-variable"
    tg_name = route["tg_name"] if route else None
    tg_id = route["time_group_id"] if route else None
    mailbox_list = blast_row["mailbox_list"] if blast_row else "4900&4901&4902&4903&4904"
    vm_flags = blast_row["voicemail_flags"] if blast_row else "su"

    # Check if open_target is a ring group
    ring_group = None
    if open_target:
        ring_group = db.execute(
            "SELECT * FROM ring_groups WHERE extension = ?", (open_target,)
        ).fetchone()

    # Parse time rules for display
    time_rules = []
    if route and route["rules_json"]:
        try:
            time_rules = json.loads(route["rules_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    nodes = [
        {"id": "incoming", "label": "Incoming Call", "type": "entry",
         "detail": route["name"] if route else "No route configured"},
        {"id": "from-trunk", "label": "from-trunk", "type": "context",
         "detail": f"Route: {route['name']}" if route else "No route"},
        {"id": "spam-check", "label": "Spam Check", "type": "check",
         "detail": f"AstDB family: {spam_family}"},
        {"id": "spam-blocked", "label": "spam-blocked", "type": "blocked",
         "detail": "Hangup(21)"},
        {"id": "holiday-check", "label": "Holiday Check", "type": "check",
         "detail": f"Fixed: {fixed_hol}, Variable: {variable_hol}"},
        {"id": "time-check", "label": "Time Check", "type": "time",
         "detail": tg_name or "No time group set"},
        {"id": "open", "label": "Open", "type": "routing",
         "detail": f"Goto(internal,{open_target},1)"},
        {"id": "closed", "label": "Closed", "type": "routing",
         "detail": f"Playback({closed_announcement})"},
    ]

    if ring_group:
        rg_name = ring_group["name"]
        rg_members = ring_group["members"]
        rg_greeting = ring_group["greeting_announcement"]
        rg_noanswer = ring_group["noanswer_announcement"]
        rg_noanswer_action = ring_group["noanswer_action"]
        nodes.append(
            {"id": "ring-group", "label": f"Ring Group {open_target}",
             "type": "endpoint",
             "detail": f"{rg_name} — ring {rg_members} ({ring_group['strategy']}, {ring_group['ring_time']}s)"},
        )
        rg_failover_detail = rg_noanswer_action
        if rg_noanswer_action == "vmblast":
            rg_failover_detail = f"VM Blast ({mailbox_list})"
        elif rg_noanswer_action == "voicemail":
            rg_failover_detail = f"VoiceMail({ring_group['noanswer_target']})"
        elif rg_noanswer_action == "extension":
            rg_failover_detail = f"Goto ext {ring_group['noanswer_target']}"
        nodes.append(
            {"id": "rg-noanswer", "label": "No Answer",
             "type": "blocked",
             "detail": f"{'Playback(' + rg_noanswer + ') → ' if rg_noanswer else ''}{rg_failover_detail}"},
        )
    else:
        nodes.append(
            {"id": "internal-ext", "label": f"Extension {open_target}",
             "type": "endpoint",
             "detail": f"Ring ext {open_target}"},
        )

    nodes.append(
        {"id": "voicemail", "label": "Voicemail Blast", "type": "endpoint",
         "detail": f"VoiceMail({mailbox_list},{vm_flags})"},
    )

    edges = [
        {"from": "incoming", "to": "from-trunk", "label": ""},
        {"from": "from-trunk", "to": "spam-check", "label": "check prefix"},
        {"from": "spam-check", "to": "spam-blocked", "label": "match"},
        {"from": "spam-check", "to": "holiday-check", "label": "no match"},
        {"from": "holiday-check", "to": "closed", "label": "match"},
        {"from": "holiday-check", "to": "time-check", "label": "no match"},
        {"from": "time-check", "to": "open", "label": "in hours"},
        {"from": "time-check", "to": "closed", "label": "out of hours"},
    ]

    if ring_group:
        edges.append({"from": "open", "to": "ring-group", "label": ""})
        edges.append({"from": "ring-group", "to": "rg-noanswer", "label": "no answer"})
    else:
        edges.append({"from": "open", "to": "internal-ext", "label": ""})

    edges.append({"from": "closed", "to": "voicemail", "label": "after announcement"})

    config = {
        "open_target": open_target,
        "closed_announcement": closed_announcement,
        "time_group": tg_name,
        "time_group_id": tg_id,
        "blast_mailboxes": mailbox_list,
        "voicemail_flags": vm_flags,
        "spam_family": spam_family,
        "fixed_holiday_family": fixed_hol,
        "variable_holiday_family": variable_hol,
        "time_rules": time_rules,
        "ring_group": dict(ring_group) if ring_group else None,
    }

    return {"nodes": nodes, "edges": edges, "config": config}


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------

@dialplan_bp.route("/api/v1/dialplan/graph")
@login_required
def api_graph():
    """Return JSON describing the call flow as nodes and edges."""
    return jsonify(_build_graph())


@dialplan_bp.route("/api/v1/dialplan/rendered")
@login_required
def api_rendered():
    """Return the full generated dialplan text for all managed contexts."""
    tg_text = generate_timegroups()
    inbound_text = generate_inbound_flow()
    rg_text = generate_ring_groups()
    combined = tg_text + "\n" + inbound_text + "\n" + rg_text
    return jsonify({"dialplan": combined})


# ---------------------------------------------------------------------------
# UI route
# ---------------------------------------------------------------------------

def _build_mermaid(graph: dict, urls: dict) -> str:
    """Build a Mermaid flowchart TD definition from the graph data."""
    cfg = graph["config"]
    rg = cfg.get("ring_group")
    s = sanitize_mermaid_label
    lines = [
        "flowchart TD",
        # -- Nodes
        '    incoming["📞 Incoming Call"]',
        '    fromtrunk["from-trunk\\nCheck caller ID prefix"]',
        '    spamcheck{{"🔍 Spam Check\\nAstDB: %s"}}' % s(cfg["spam_family"]),
        '    blocked["🚫 spam-blocked\\nHangup 21 — call rejected"]',
        '    holcheck{{"📅 Holiday Check\\nFixed: %s\\nVariable: %s"}}'
        % (s(cfg["fixed_holiday_family"]), s(cfg["variable_holiday_family"])),
        '    timecheck{{"🕐 Time Check\\n%s"}}' % (s(cfg["time_group"]) or "⚠ No time group"),
        '    open["✅ Open\\nRoute to ext %s"]' % s(cfg["open_target"]),
        '    closed["🔴 Closed\\nPlayback %s"]' % s(cfg["closed_announcement"]),
    ]

    if rg:
        rg_members = s(rg["members"])
        rg_name = s(rg["name"])
        rg_strategy = s(rg["strategy"])
        rg_greeting = s(rg.get("greeting_announcement", ""))
        rg_noanswer = s(rg.get("noanswer_announcement", ""))
        rg_detail = f"{rg_name}\\n{rg_strategy} — {rg_members}\\n{rg['ring_time']}s with MoH"
        if rg_greeting:
            rg_detail += f"\\nGreeting: {rg_greeting}"
        lines.append('    ringgroup["🔔 Ring Group %s\\n%s"]' % (s(cfg["open_target"]), rg_detail))
        noanswer_detail = rg.get("noanswer_action", "hangup")
        if rg_noanswer:
            noanswer_detail = f"Playback {rg_noanswer} → {noanswer_detail}"
        lines.append('    rgnoanswer["⛔ No Answer\\n%s"]' % s(noanswer_detail))
    else:
        lines.append(
            '    ext["📱 Extension %s\\nGoto internal,%s,1"]'
            % (s(cfg["open_target"]), s(cfg["open_target"]))
        )

    lines.append(
        '    vm["📬 Voicemail Blast\\nVoiceMail %s,%s"]'
        % (s(cfg["blast_mailboxes"]), s(cfg["voicemail_flags"]))
    )

    lines += [
        "",
        # -- Edges
        "    incoming --> fromtrunk",
        "    fromtrunk --> spamcheck",
        '    spamcheck -- "match" --> blocked',
        '    spamcheck -- "no match" --> holcheck',
        '    holcheck -- "match (holiday)" --> closed',
        '    holcheck -- "no match" --> timecheck',
        '    timecheck -- "in hours" --> open',
        '    timecheck -- "out of hours" --> closed',
    ]

    if rg:
        lines.append("    open --> ringgroup")
        lines.append('    ringgroup -- "no answer" --> rgnoanswer')
    else:
        lines.append("    open --> ext")

    lines.append('    closed -- "after announcement" --> vm')
    lines += [
        "",
        # -- Click links
        '    click spamcheck "%s" "Manage spam list"' % urls["spam"],
        '    click blocked "%s" "Manage spam list"' % urls["spam"],
        '    click holcheck "%s" "Manage holidays"' % urls["holidays"],
        '    click incoming "%s" "Edit inbound route"' % urls["inbound"],
        '    click fromtrunk "%s" "Edit inbound route"' % urls["inbound"],
        '    click vm "%s" "Voicemail settings"' % urls["voicemail"],
    ]
    if rg and urls.get("ringgroup"):
        lines.append('    click ringgroup "%s" "Edit ring group"' % urls["ringgroup"])
    # Link time check to edit page if a time group is configured
    if urls.get("timegroup"):
        lines.append(
            '    click timecheck "%s" "Edit time group"' % urls["timegroup"]
        )
    lines += [
        "",
        # -- Styles
        "    classDef entry fill:#e7f1ff,stroke:#0d6efd,color:#0a4dbd",
        "    classDef context fill:#f3e8ff,stroke:#6610f2,color:#4a0cb0",
        "    classDef check fill:#fff3e0,stroke:#fd7e14,color:#b35a00",
        "    classDef blockedNode fill:#fff5f5,stroke:#dc3545,color:#842029",
        "    classDef openNode fill:#d1e7dd,stroke:#198754,color:#0f5132",
        "    classDef closedNode fill:#fde8e8,stroke:#dc3545,color:#842029",
        "    classDef endpoint fill:#f0f6ff,stroke:#0d6efd,color:#0a4dbd",
        "    classDef ringgroupNode fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20",
        "    classDef clickable cursor:pointer",
        "",
        "    class incoming entry",
        "    class fromtrunk context",
        "    class spamcheck,holcheck,timecheck check",
        "    class blocked blockedNode",
        "    class open openNode",
        "    class closed closedNode",
    ]
    if rg:
        lines.append("    class ringgroup ringgroupNode")
        lines.append("    class rgnoanswer blockedNode")
    else:
        lines.append("    class ext endpoint")
    lines.append("    class vm endpoint")
    return "\n".join(lines)


@dialplan_bp.route("/dialplan")
@login_required
def ui_view():
    """Main visualization page: Mermaid flow diagram + generated config text."""
    graph = _build_graph()
    cfg = graph["config"]
    tg_text = generate_timegroups()
    inbound_text = generate_inbound_flow()
    rg_text = generate_ring_groups()
    combined = tg_text + "\n" + inbound_text + "\n" + rg_text

    urls = {
        "spam": url_for("spam.ui_list"),
        "holidays": url_for("holidays.ui_list"),
        "inbound": url_for("inbound.ui_list"),
        "voicemail": url_for("voicemail.ui_list"),
        "timegroup": (
            url_for("timegroups.ui_edit", tg_id=cfg["time_group_id"])
            if cfg.get("time_group_id")
            else None
        ),
        "ringgroup": (
            url_for("ringgroups.ui_edit", extension=cfg["open_target"])
            if cfg.get("ring_group")
            else None
        ),
    }

    return render_template(
        "dialplan.html",
        mermaid_code=_build_mermaid(graph, urls),
        config=cfg,
        dialplan_text=combined,
    )
