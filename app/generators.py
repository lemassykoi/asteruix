"""Config file generators — render managed Asterisk config from DB state."""

from __future__ import annotations

import os
import shutil
import tempfile

from app.apply import sanitize_config_value as _scv
from app.db import get_db

WEBUI_CONF_DIR = "/etc/asterisk/webui"


def _atomic_write(path: str, content: str):
    """Write content to *path* atomically via temp file + rename.

    Sets ownership to asterisk:asterisk (uid/gid looked up once).
    """
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        shutil.move(tmp, path)
        # Best-effort chown to asterisk user
        try:
            import pwd
            pw = pwd.getpwnam("asterisk")
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except (KeyError, PermissionError):
            pass
    except Exception:
        os.close(fd) if not os.path.exists(tmp) else None
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def generate_pjsip_extensions() -> str:
    """Render PJSIP endpoint/auth/aor blocks for all enabled extensions."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM extensions WHERE enabled = 1 ORDER BY ext"
    ).fetchall()

    lines = [
        "; ---- WebUI-managed PJSIP extensions ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    for r in rows:
        ext = _scv(r["ext"])
        callerid_name = _scv(r["callerid_name"]) or f"Ext {ext}"
        codecs = _scv(r["codecs"]) or "g722,ulaw,alaw"
        codec_lines = "\n".join(f"allow = {c.strip()}" for c in codecs.split(","))

        lines.append(f"; --- Extension {ext} ---")

        # Endpoint
        lines.append(f"[{ext}]")
        lines.append("type = endpoint")
        lines.append("context = internal")
        lines.append("disallow = all")
        lines.append(codec_lines)
        lines.append(f"dtmf_mode = {_scv(r['dtmf_mode'])}")
        lines.append(f"language = {_scv(r['language'])}")
        lines.append("rtp_symmetric = no")
        lines.append("force_rport = no")
        lines.append("rewrite_contact = no")
        lines.append("direct_media = yes")
        lines.append(f"auth = {ext}-auth")
        lines.append(f"aors = {ext}")
        lines.append(f'callerid = "{callerid_name}" <{ext}>')
        lines.append(f"mailboxes = {ext}@default")
        if r["musicclass"] and r["musicclass"] != "default":
            lines.append(f"music_on_hold_class = {r['musicclass']}")
        lines.append("")

        # Auth
        lines.append(f"[{ext}-auth]")
        lines.append("type = auth")
        lines.append("auth_type = userpass")
        lines.append(f"username = {ext}")
        lines.append(f"password = {_scv(r['sip_password'])}")
        lines.append("")

        # AoR
        lines.append(f"[{ext}]")
        lines.append("type = aor")
        lines.append(f"max_contacts = {r['max_contacts']}")
        lines.append("remove_existing = yes")
        lines.append("qualify_frequency = 30")
        lines.append("")

    return "\n".join(lines)


def generate_voicemail_boxes() -> str:
    """Render voicemail.conf mailbox lines for all voicemail boxes."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM voicemail_boxes ORDER BY mailbox"
    ).fetchall()

    lines = [
        "; ---- WebUI-managed voicemail boxes ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    for r in rows:
        # Format: mailbox => pin,name,email,pager,options
        options_parts = []
        if r["attach"]:
            options_parts.append("attach=yes")
        if r["delete_after_email"]:
            options_parts.append("delete=yes")
        options = "|".join(options_parts)

        email = _scv(r["email"])
        name = _scv(r["name"])
        lines.append(f"{_scv(r['mailbox'])} => {_scv(r['pin'])},{name},{email},,{options}")

    lines.append("")
    return "\n".join(lines)


def generate_pjsip_trunks() -> str:
    """Render PJSIP endpoint/auth/aor/registration/identify blocks for all enabled trunks."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM trunks WHERE enabled = 1 ORDER BY name"
    ).fetchall()

    lines = [
        "; ---- WebUI-managed PJSIP trunks ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    for r in rows:
        name = _scv(r["name"])
        trunk_type = _scv(r["type"])  # 'registration' or 'identify'
        host = _scv(r["host"])
        contact = _scv(r["contact_uri"]) or f"sip:{host}"

        lines.append(f"; --- Trunk: {name} ({trunk_type}) ---")

        # Endpoint
        lines.append(f"[{name}]")
        lines.append("type = endpoint")
        lines.append("context = from-trunk")
        lines.append("disallow = all")
        lines.append("allow = g722")
        lines.append("allow = ulaw")
        lines.append("allow = alaw")
        lines.append(f"aors = {name}")
        if trunk_type == "registration" and r["username"]:
            lines.append(f"outbound_auth = {name}-auth")
        if r["from_domain"]:
            lines.append(f"from_domain = {_scv(r['from_domain'])}")
        lines.append("")

        # Auth (registration trunks only)
        if trunk_type == "registration" and r["username"]:
            lines.append(f"[{name}-auth]")
            lines.append("type = auth")
            lines.append("auth_type = userpass")
            lines.append(f"username = {_scv(r['username'])}")
            lines.append(f"password = {_scv(r['password'])}")
            lines.append("")

        # AoR
        lines.append(f"[{name}]")
        lines.append("type = aor")
        lines.append(f"contact = {contact}")
        lines.append("qualify_frequency = 30")
        lines.append("")

        # Registration (registration trunks only)
        if trunk_type == "registration":
            server_uri = _scv(r["registration_server_uri"]) or f"sip:{host}"
            client_uri = _scv(r["registration_client_uri"]) or f"sip:{_scv(r['username'])}@{host}"
            lines.append(f"[{name}-reg]")
            lines.append("type = registration")
            lines.append(f"outbound_auth = {name}-auth")
            lines.append(f"server_uri = {server_uri}")
            lines.append(f"client_uri = {client_uri}")
            lines.append("retry_interval = 30")
            lines.append("")

        # Identify
        match = _scv(r["identify_match"]) or host
        lines.append(f"[{name}-identify]")
        lines.append("type = identify")
        lines.append(f"endpoint = {name}")
        lines.append(f"match = {match}")
        lines.append("")

    return "\n".join(lines)


def generate_musiconhold_classes() -> str:
    """Render musiconhold.conf class definitions for all WebUI-managed MoH classes."""
    db = get_db()
    rows = db.execute("SELECT * FROM moh_classes ORDER BY name").fetchall()

    lines = [
        "; ---- WebUI-managed MoH classes ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    for r in rows:
        lines.append(f"[{_scv(r['name'])}]")
        lines.append("mode = files")
        lines.append(f"directory = {_scv(r['directory'])}")
        lines.append("")

    return "\n".join(lines)


def write_musiconhold_classes():
    """Generate and atomically write the managed MoH classes file."""
    content = generate_musiconhold_classes()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "musiconhold_classes.conf"), content)
    return content


def write_pjsip_extensions():
    """Generate and atomically write the managed PJSIP extensions file."""
    content = generate_pjsip_extensions()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "pjsip_extensions.conf"), content)
    return content


def write_voicemail_boxes():
    """Generate and atomically write the managed voicemail boxes file."""
    content = generate_voicemail_boxes()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "voicemail_boxes.conf"), content)
    return content


def write_pjsip_trunks():
    """Generate and atomically write the managed PJSIP trunks file."""
    content = generate_pjsip_trunks()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "pjsip_trunks.conf"), content)
    return content


def generate_timegroups() -> str:
    """Render dialplan time-check context from time_groups table.

    Each time group generates GotoIfTime() lines that jump to a label
    ``open-<tg_id>`` when the current time matches.  A fallback
    ``Goto(closed,s,1)`` is emitted at the end so that unmatched times
    route to the closed context.

    The generated context is included from extensions.conf via
    ``#include "/etc/asterisk/webui/extensions_timegroups.conf"``.
    """
    import json as _json

    db = get_db()
    rows = db.execute("SELECT * FROM time_groups ORDER BY id").fetchall()

    lines = [
        "; ---- WebUI-managed time-check context ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    if not rows:
        # Empty — leave the file as a no-op comment so #include doesn't error
        return "\n".join(lines)

    # We override [time-check] here.  The original hand-written context
    # in extensions.conf should be commented out or removed after migration.
    lines.append("[time-check]")
    lines.append("exten => s,1,NoOp(WebUI time-check for ${CALLERID(all)})")
    priority = 2

    # Holiday checks first (always present)
    lines.append(f" same => n,Set(TODAY_MMDD=${{STRFTIME(${{EPOCH}},,%m%d)}})")
    lines.append(f" same => n,Set(TODAY_YYYYMMDD=${{STRFTIME(${{EPOCH}},,%Y%m%d)}})")
    lines.append(f" same => n,GotoIf(${{DB_EXISTS(holidays-fixed/${{TODAY_MMDD}})}}?closed,s,1)")
    lines.append(f" same => n,GotoIf(${{DB_EXISTS(holidays-variable/${{TODAY_YYYYMMDD}})}}?closed,s,1)")

    DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    for row in rows:
        rules = _json.loads(row["rules_json"])
        for rule in rules:
            start = rule.get("start", "")
            end = rule.get("end", "")
            days = rule.get("days", [])
            # Format days for Asterisk GotoIfTime
            sorted_days = sorted(days, key=lambda d: DAY_ORDER.index(d) if d in DAY_ORDER else 99)
            if len(sorted_days) == 7:
                day_spec = "*"
            else:
                indices = [DAY_ORDER.index(d) for d in sorted_days]
                if indices == list(range(indices[0], indices[0] + len(indices))):
                    day_spec = f"{DAY_ORDER[indices[0]]}-{DAY_ORDER[indices[-1]]}"
                else:
                    day_spec = "&".join(sorted_days)

            lines.append(f" same => n,GotoIfTime({start}-{end},{day_spec},*,*?open,s,1)")

    lines.append(" same => n,Goto(closed,s,1)")
    lines.append("")

    return "\n".join(lines)


def write_timegroups():
    """Generate and atomically write the managed time groups dialplan file."""
    content = generate_timegroups()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "extensions_timegroups.conf"), content)
    return content


def generate_inbound_flow() -> str:
    """Render the from-trunk, spam-blocked, open, and closed dialplan contexts.

    Each inbound route produces a from-trunk entry that:
      1. Checks caller ID prefix against AstDB spam family
      2. Jumps to time-check (which handles holidays + business hours)
      3. Open target routes to an extension via internal context
      4. Closed target plays announcement + voicemail blast
    """
    import json as _json

    db = get_db()
    routes = db.execute(
        "SELECT r.*, t.name AS tg_name, t.rules_json "
        "FROM inbound_routes r "
        "LEFT JOIN time_groups t ON r.time_group_id = t.id "
        "ORDER BY r.id"
    ).fetchall()

    lines = [
        "; ---- WebUI-managed inbound flow ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    if not routes:
        return "\n".join(lines)

    # --- [from-trunk] context ---
    lines.append("[from-trunk]")
    lines.append("exten => _X.,1,NoOp(Incoming call from trunk: ${CALLERID(all)})")

    for route in routes:
        spam_family = _scv(route["spam_family"]) or "spam-prefix"
        lines.append(f" same => n,Set(PREFIX=${{CALLERID(num):0:4}})")
        lines.append(f" same => n,GotoIf(${{DB_EXISTS({spam_family}/${{PREFIX}})}}?spam-blocked,s,1)")

    lines.append(" same => n,Goto(time-check,s,1)")
    lines.append(" same => n,Hangup()")
    lines.append("")

    # --- [spam-blocked] context ---
    lines.append("[spam-blocked]")
    lines.append("exten => s,1,NoOp(SPAM BLOCKED: ${CALLERID(num)} prefix ${PREFIX})")
    lines.append(" same => n,Log(WARNING,Blocked spam call from ${CALLERID(num)} - prefix ${PREFIX})")
    lines.append(" same => n,Hangup(21)")
    lines.append("")

    # --- [open] context ---
    # Use the first route's open_target (single-route SOHO design)
    route = routes[0]
    open_target = _scv(route["open_target"]) or "4900"
    lines.append("[open]")
    lines.append(f"exten => s,1,NoOp(Office OPEN — routing to {open_target})")
    lines.append(f" same => n,Goto(internal,{open_target},1)")
    lines.append("")

    # --- [closed] context ---
    closed_announcement = _scv(route["closed_announcement"]) or "custom-closed"
    blast_id = route["blast_profile"]
    blast_row = None
    if blast_id:
        blast_row = db.execute(
            "SELECT * FROM blast_config WHERE id = ?", (blast_id,)
        ).fetchone()

    mailbox_list = "4900&4901&4902&4903&4904"
    vm_flags = "su"
    if blast_row:
        mailbox_list = _scv(blast_row["mailbox_list"]) or mailbox_list
        vm_flags = _scv(blast_row["voicemail_flags"]) or vm_flags

    lines.append("[closed]")
    lines.append("exten => s,1,NoOp(Office CLOSED — announcement + voicemail)")
    lines.append(" same => n,Answer()")
    lines.append(" same => n,Set(CHANNEL(language)=fr)")
    lines.append(f" same => n,Playback({closed_announcement})")
    lines.append(f" same => n,VoiceMail({mailbox_list}@default,{vm_flags})")
    lines.append(" same => n,Hangup()")
    lines.append("")

    return "\n".join(lines)


def write_inbound_flow():
    """Generate and atomically write the managed inbound flow dialplan file."""
    content = generate_inbound_flow()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "extensions_inbound.conf"), content)
    return content


def generate_confbridge_profiles() -> str:
    """Render ConfBridge bridge/user/menu profile sections from conference_rooms table.

    Each room generates a bridge profile (with max_members) and a user profile
    (with music_on_hold_class, announce_join_leave, music_on_hold_when_empty).
    Menu profiles use the existing default_menu defined in confbridge.conf.
    """
    db = get_db()
    rows = db.execute("SELECT * FROM conference_rooms ORDER BY extension").fetchall()

    lines = [
        "; ---- WebUI-managed ConfBridge profiles ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    for r in rows:
        ext = _scv(r["extension"])
        bridge_name = _scv(r["bridge_profile"])
        user_name = _scv(r["user_profile"])

        # Bridge profile
        lines.append(f"[{bridge_name}]")
        lines.append("type = bridge")
        lines.append(f"max_members = {r['max_members']}")
        lines.append("")

        # User profile — music_on_hold_class is a user profile option
        lines.append(f"[{user_name}]")
        lines.append("type = user")
        lines.append(f"music_on_hold_class = {_scv(r['moh_class'])}")
        lines.append(f"music_on_hold_when_empty = {'yes' if r['music_on_hold_when_empty'] else 'no'}")
        lines.append(f"announce_user_count = yes")
        lines.append(f"announce_join_leave = {'yes' if r['announce_join_leave'] else 'no'}")
        lines.append("dtmf_passthrough = no")
        lines.append("")

    return "\n".join(lines)


def write_confbridge_profiles():
    """Generate and atomically write the managed ConfBridge profiles file."""
    content = generate_confbridge_profiles()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "confbridge_profiles.conf"), content)
    return content


def generate_ivr_menus() -> str:
    """Render IVR menu dialplan contexts from ivr_menus table.

    Each IVR menu generates a context [ivr-<name>] with:
      - Answer() and Background(greeting) to play audio while accepting DTMF
      - WaitExten(timeout) to wait for digit input
      - Per-digit exten entries with Goto() to targets
      - exten => i for invalid input (with retry counter)
      - exten => t for timeout (treated same as invalid)
    """
    import json as _json

    db = get_db()
    rows = db.execute("SELECT * FROM ivr_menus ORDER BY name").fetchall()

    lines = [
        "; ---- WebUI-managed IVR menus ----",
        "; Auto-generated — do not edit manually",
        "",
    ]

    if not rows:
        return "\n".join(lines)

    for r in rows:
        name = _scv(r["name"])
        ctx = f"ivr-{name}"
        greeting = _scv(r["greeting"]) or "demo-congrats"
        timeout = r["timeout"] or 5
        invalid_retries = r["invalid_retries"] or 3
        options = _json.loads(r["options_json"])

        lines.append(f"[{ctx}]")
        lines.append(f"exten => s,1,NoOp(IVR menu: {name})")
        lines.append(f" same => n,Answer()")
        lines.append(f" same => n,Set(IVR_RETRIES=0)")
        lines.append(f" same => n(menu),Background({greeting})")
        lines.append(f" same => n,WaitExten({timeout})")
        lines.append("")

        # Per-digit entries
        for opt in options:
            digit = _scv(opt.get("digit", ""))
            action = _scv(opt.get("action", ""))
            target = _scv(opt.get("target", ""))

            if not digit:
                continue

            if action == "goto_extension":
                lines.append(f"exten => {digit},1,NoOp(IVR {name}: digit {digit} -> extension {target})")
                lines.append(f" same => n,Goto(internal,{target},1)")
            elif action == "goto_context":
                lines.append(f"exten => {digit},1,NoOp(IVR {name}: digit {digit} -> context {target})")
                lines.append(f" same => n,Goto({target},s,1)")
            elif action == "hangup":
                lines.append(f"exten => {digit},1,NoOp(IVR {name}: digit {digit} -> hangup)")
                lines.append(f" same => n,Playback(vm-goodbye)")
                lines.append(f" same => n,Hangup()")
            lines.append("")

        # Invalid input handler
        lines.append(f"exten => i,1,NoOp(IVR {name}: invalid input)")
        lines.append(f" same => n,Set(IVR_RETRIES=$[${{IVR_RETRIES}}+1])")
        lines.append(f" same => n,GotoIf($[${{IVR_RETRIES}}>={invalid_retries}]?toolong)")
        lines.append(f" same => n,Playback(invalid)")
        lines.append(f" same => n,Goto(s,menu)")
        lines.append(f" same => n(toolong),Playback(vm-goodbye)")
        lines.append(f" same => n,Hangup()")
        lines.append("")

        # Timeout handler
        lines.append(f"exten => t,1,NoOp(IVR {name}: timeout)")
        lines.append(f" same => n,Set(IVR_RETRIES=$[${{IVR_RETRIES}}+1])")
        lines.append(f" same => n,GotoIf($[${{IVR_RETRIES}}>={invalid_retries}]?toolong)")
        lines.append(f" same => n,Goto(s,menu)")
        lines.append(f" same => n(toolong),Playback(vm-goodbye)")
        lines.append(f" same => n,Hangup()")
        lines.append("")

    return "\n".join(lines)


def write_ivr_menus():
    """Generate and atomically write the managed IVR menus dialplan file."""
    content = generate_ivr_menus()
    _atomic_write(os.path.join(WEBUI_CONF_DIR, "extensions_ivr.conf"), content)
    return content
