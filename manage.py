#!/usr/bin/env python3
"""CLI management commands for AsterUIX."""

import argparse
import getpass
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from app.db import get_db
from app.auth import hash_password


def create_admin(args):
    app = create_app()
    with app.app_context():
        db = get_db()

        username = args.username or input("Admin username: ").strip()
        if not username:
            print("Error: username cannot be empty.", file=sys.stderr)
            sys.exit(1)

        password = args.password or getpass.getpass("Admin password: ")
        if len(password) < 8:
            print("Error: password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)

        pw_hash = hash_password(password)
        db.execute(
            "INSERT INTO ui_users (username, password_hash, role, enabled) "
            "VALUES (?, ?, 'admin', 1) "
            "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, enabled=1",
            (username, pw_hash),
        )
        db.commit()
        print(f"Admin user '{username}' created/updated.")


def import_extensions(args):
    """Import existing extensions from pjsip.conf and voicemail.conf into the DB."""
    import re

    app = create_app()
    with app.app_context():
        db = get_db()

        # Parse pjsip.conf for extension blocks
        pjsip_path = args.pjsip_conf or "/etc/asterisk/pjsip.conf"
        vm_path = args.voicemail_conf or "/etc/asterisk/voicemail.conf"

        with open(pjsip_path) as f:
            pjsip_raw = f.read()

        # Find endpoint sections that use endpoint-defaults template
        # Pattern: [XXXX](endpoint-defaults) ... password = ... in auth section
        ext_pattern = re.compile(
            r"^\[(\d{3,6})\]\(endpoint-defaults\)\s*\n"
            r"(.*?)(?=\n\[|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        auth_pattern = re.compile(
            r"^\[(\d{3,6})-auth\]\(auth-defaults\)\s*\n"
            r"(.*?)(?=\n\[|\Z)",
            re.MULTILINE | re.DOTALL,
        )

        extensions = {}
        for m in ext_pattern.finditer(pjsip_raw):
            ext = m.group(1)
            body = m.group(2)
            callerid_match = re.search(r'callerid\s*=\s*"([^"]*)"', body)
            callerid_name = callerid_match.group(1) if callerid_match else f"Ext {ext}"
            extensions[ext] = {"callerid_name": callerid_name}

        for m in auth_pattern.finditer(pjsip_raw):
            ext = m.group(1)
            body = m.group(2)
            pw_match = re.search(r"password\s*=\s*(\S+)", body)
            if ext in extensions and pw_match:
                extensions[ext]["sip_password"] = pw_match.group(1)

        # Parse voicemail.conf for PINs
        vm_pins = {}
        with open(vm_path) as f:
            for line in f:
                vm_match = re.match(r"^(\d{3,6})\s*=>\s*(\d+),(.*)$", line.strip())
                if vm_match:
                    vm_pins[vm_match.group(1)] = {
                        "pin": vm_match.group(2),
                        "name": vm_match.group(3).split(",")[0].strip(),
                    }

        count = 0
        for ext, data in sorted(extensions.items()):
            if "sip_password" not in data:
                print(f"  Skipping {ext}: no password found in auth section")
                continue

            vm = vm_pins.get(ext, {})
            db.execute(
                "INSERT INTO extensions (ext, callerid_name, sip_password, vm_pin, "
                "enabled, max_contacts, codecs, language, dtmf_mode, musicclass) "
                "VALUES (?, ?, ?, ?, 1, 3, 'g722,ulaw,alaw', 'fr', 'rfc4733', 'default') "
                "ON CONFLICT(ext) DO UPDATE SET callerid_name=excluded.callerid_name, "
                "sip_password=excluded.sip_password, vm_pin=excluded.vm_pin",
                (ext, data["callerid_name"], data["sip_password"],
                 vm.get("pin", "1234")),
            )
            db.execute(
                "INSERT INTO voicemail_boxes (mailbox, pin, name) VALUES (?, ?, ?) "
                "ON CONFLICT(mailbox) DO UPDATE SET pin=excluded.pin, name=excluded.name",
                (ext, vm.get("pin", "1234"), vm.get("name", data["callerid_name"])),
            )
            count += 1
            print(f"  Imported {ext} ({data['callerid_name']})")

        db.commit()
        print(f"\n{count} extension(s) imported.")
        if args.generate:
            from app.generators import write_pjsip_extensions, write_voicemail_boxes
            write_pjsip_extensions()
            write_voicemail_boxes()
            print("Managed config files generated.")


def import_moh(args):
    """Import existing MoH classes from musiconhold.conf and scan track files."""
    import re

    app = create_app()
    with app.app_context():
        db = get_db()

        conf_path = args.moh_conf or "/etc/asterisk/musiconhold.conf"
        with open(conf_path) as f:
            raw = f.read()

        # Parse [class] sections with mode=files and directory=...
        section_re = re.compile(
            r"^\[([a-zA-Z0-9_-]+)\]\s*\n(.*?)(?=\n\[|\Z)",
            re.MULTILINE | re.DOTALL,
        )

        count = 0
        for m in section_re.finditer(raw):
            name = m.group(1)
            body = m.group(2)
            mode_match = re.search(r"mode\s*=\s*(\S+)", body)
            dir_match = re.search(r"directory\s*=\s*(\S+)", body)
            if not mode_match or mode_match.group(1) != "files":
                continue
            if not dir_match:
                continue
            directory = dir_match.group(1)

            db.execute(
                "INSERT INTO moh_classes (name, directory) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET directory=excluded.directory",
                (name, directory),
            )
            count += 1
            print(f"  Imported class '{name}' -> {directory}")

            # Scan for existing tracks in the directory
            if os.path.isdir(directory):
                for fn in sorted(os.listdir(directory)):
                    if fn.endswith((".wav16", ".wav", ".gsm", ".sln", ".sln16")):
                        existing = db.execute(
                            "SELECT 1 FROM moh_tracks WHERE class_name = ? AND filename = ?",
                            (name, fn),
                        ).fetchone()
                        if not existing:
                            filepath = os.path.join(directory, fn)
                            duration = None
                            try:
                                import subprocess
                                result = subprocess.run(
                                    ["soxi", "-D", filepath],
                                    capture_output=True, text=True, timeout=10,
                                )
                                duration = round(float(result.stdout.strip()), 1)
                            except Exception:
                                pass
                            db.execute(
                                "INSERT INTO moh_tracks (class_name, filename, duration_sec) "
                                "VALUES (?, ?, ?)",
                                (name, fn, duration),
                            )
                            print(f"    Track: {fn} ({duration}s)" if duration else f"    Track: {fn}")

        db.commit()
        print(f"\n{count} MoH class(es) imported.")

        if args.generate:
            from app.generators import write_musiconhold_classes
            write_musiconhold_classes()
            print("Managed MoH config file generated.")


def import_announcements(args):
    """Import existing announcement files from the sounds directory into the DB."""
    import subprocess as _sp

    app = create_app()
    with app.app_context():
        db = get_db()

        sounds_dir = args.sounds_dir or "/var/lib/asterisk/sounds/fr"
        prefix = args.prefix or "custom-"

        if not os.path.isdir(sounds_dir):
            print(f"Error: directory {sounds_dir} does not exist.", file=sys.stderr)
            sys.exit(1)

        count = 0
        for fn in sorted(os.listdir(sounds_dir)):
            if not fn.startswith(prefix):
                continue
            if not fn.endswith(".wav16"):
                continue

            key_name = fn.rsplit(".", 1)[0]  # strip .wav16

            existing = db.execute(
                "SELECT 1 FROM announcements WHERE key_name = ?", (key_name,)
            ).fetchone()
            if existing:
                print(f"  Skipping '{key_name}' (already in DB)")
                continue

            duration = None
            filepath = os.path.join(sounds_dir, fn)
            try:
                result = _sp.run(
                    ["soxi", "-D", filepath],
                    capture_output=True, text=True, timeout=10,
                )
                duration = round(float(result.stdout.strip()), 1)
            except Exception:
                pass

            # First imported announcement is set active
            is_first = count == 0 and db.execute(
                "SELECT COUNT(*) AS c FROM announcements"
            ).fetchone()["c"] == 0
            active = 1 if is_first else 0

            db.execute(
                "INSERT INTO announcements (key_name, filename, language, active) "
                "VALUES (?, ?, 'fr', ?)",
                (key_name, fn, active),
            )
            count += 1
            status = " [ACTIVE]" if active else ""
            dur_str = f" ({duration}s)" if duration else ""
            print(f"  Imported '{key_name}' -> {fn}{dur_str}{status}")

        db.commit()
        print(f"\n{count} announcement(s) imported.")


def import_timegroups(args):
    """Import current business hours from extensions.conf into the DB."""
    import re as _re

    app = create_app()
    with app.app_context():
        db = get_db()

        conf_path = args.extensions_conf or "/etc/asterisk/extensions.conf"
        with open(conf_path) as f:
            raw = f.read()

        # Find [time-check] context and extract GotoIfTime lines
        tc_match = _re.search(
            r"^\[time-check\]\s*\n(.*?)(?=\n\[|\Z)",
            raw, _re.MULTILINE | _re.DOTALL,
        )
        if not tc_match:
            # No [time-check] context - create default business hours
            import json
            name = args.name or "Business Hours"
            rules = [{"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}]
            rules_json = json.dumps(rules)

            db.execute(
                "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET rules_json=excluded.rules_json",
                (name, "Europe/Paris", rules_json),
            )
            db.commit()

            print(f"Created default time group '{name}' (no [time-check] context found):")
            print(f"  09:00-17:00 mon,tue,wed,thu,fri")
            return

        body = tc_match.group(1)
        time_re = _re.compile(
            r"GotoIfTime\((\d{2}:\d{2})-(\d{2}:\d{2}),([^,]+),\*,\*\?open"
        )

        rules = []
        for m in time_re.finditer(body):
            start, end, day_spec = m.group(1), m.group(2), m.group(3)
            # Parse day spec
            day_spec = day_spec.strip()
            if day_spec == "*":
                days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            elif "-" in day_spec:
                day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                parts = day_spec.split("-")
                si = day_order.index(parts[0])
                ei = day_order.index(parts[1])
                days = day_order[si:ei+1]
            elif "&" in day_spec:
                days = day_spec.split("&")
            else:
                days = [day_spec]
            rules.append({"start": start, "end": end, "days": days})

        if not rules:
            print("No GotoIfTime rules found in [time-check].")
            sys.exit(1)

        import json
        name = args.name or "Business Hours"
        rules_json = json.dumps(rules)

        db.execute(
            "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET rules_json=excluded.rules_json",
            (name, "Europe/Paris", rules_json),
        )
        db.commit()

        print(f"Imported time group '{name}' with {len(rules)} rule(s):")
        for r in rules:
            print(f"  {r['start']}-{r['end']} {','.join(r['days'])}")

        if args.generate:
            from app.generators import write_timegroups
            write_timegroups()
            print("Managed config file generated.")


def import_inbound(args):
    """Create the initial inbound route entry from current live config."""
    import re as _re

    app = create_app()
    with app.app_context():
        db = get_db()

        name = args.name

        # Check if route already exists
        if db.execute("SELECT 1 FROM inbound_routes WHERE name = ?", (name,)).fetchone():
            print(f"Route '{name}' already exists. Skipping.")
            return

        # Find the time group (should exist from import-timegroups)
        tg = db.execute("SELECT id, name FROM time_groups ORDER BY id LIMIT 1").fetchone()
        tg_id = tg["id"] if tg else None
        if tg:
            print(f"  Using time group: {tg['name']} (id={tg['id']})")
        else:
            print("  Warning: No time groups found. Set time_group_id manually.")

        # Find blast config
        blast = db.execute("SELECT id, mailbox_list FROM blast_config ORDER BY id LIMIT 1").fetchone()
        blast_id = blast["id"] if blast else None
        if blast:
            print(f"  Using blast profile: #{blast['id']} ({blast['mailbox_list']})")

        # Find active announcement
        ann = db.execute(
            "SELECT key_name FROM announcements WHERE active = 1 LIMIT 1"
        ).fetchone()
        closed_ann = ann["key_name"] if ann else "custom-closed"
        print(f"  Closed announcement: {closed_ann}")

        # Parse open target from extensions.conf
        open_target = "4900"
        try:
            with open("/etc/asterisk/extensions.conf") as f:
                raw = f.read()
            m = _re.search(r"\[open\].*?Goto\(internal,(\d+),1\)", raw, _re.DOTALL)
            if m:
                open_target = m.group(1)
        except Exception:
            pass
        print(f"  Open target: {open_target}")

        db.execute(
            "INSERT INTO inbound_routes (name, open_target, closed_announcement, "
            "blast_profile, spam_family, fixed_holiday_family, variable_holiday_family, "
            "time_group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, open_target, closed_ann, blast_id,
             "spam-prefix", "holidays-fixed", "holidays-variable", tg_id),
        )
        db.commit()
        print(f"\nInbound route '{name}' created.")

        if args.generate:
            from app.generators import write_inbound_flow
            content = write_inbound_flow()
            print("Managed config file generated.")
            print(f"\nGenerated dialplan:\n{content}")


def import_conference(args):
    """Import existing conference room from confbridge.conf into the DB."""
    app = create_app()
    with app.app_context():
        db = get_db()

        ext = args.extension
        if db.execute("SELECT 1 FROM conference_rooms WHERE extension = ?", (ext,)).fetchone():
            print(f"Conference room {ext} already exists. Skipping.")
            return

        db.execute(
            "INSERT INTO conference_rooms "
            "(extension, bridge_profile, user_profile, menu_profile, "
            "max_members, moh_class, announce_join_leave, music_on_hold_when_empty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ext, "default_bridge", "default_user", "default_menu",
             10, "default", 1, 1),
        )
        db.commit()
        print(f"Conference room {ext} imported.")

        if args.generate:
            from app.generators import write_confbridge_profiles
            write_confbridge_profiles()
            print("Managed ConfBridge config file generated.")


def create_timegroup(args):
    """Create a time group."""
    import json
    app = create_app()
    with app.app_context():
        db = get_db()

        name = args.name or "Business Hours"
        start, end = args.time.split("-")
        days = args.weekdays.split(",")

        rules = [{"start": start, "end": end, "days": days}]
        rules_json = json.dumps(rules)

        db.execute(
            "INSERT INTO time_groups (name, timezone, rules_json) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET rules_json=excluded.rules_json",
            (name, "Europe/Paris", rules_json),
        )
        db.commit()
        print(f"Time group '{name}' created.")


def create_announcement(args):
    """Create an announcement (file or TTS)."""
    app = create_app()
    with app.app_context():
        db = get_db()

        name = args.name
        key_name = f"custom-{name.lower().replace(' ', '-')}"

        if args.type == "tts":
            # Store TTS text in announcement_text table
            db.execute(
                "INSERT INTO announcements (key_name, filename, language, active, is_tts) "
                "VALUES (?, ?, 'fr', 1, 1) "
                "ON CONFLICT(key_name) DO NOTHING",
                (key_name, f"{key_name}.wav16"),
            )
            db.execute(
                "INSERT OR REPLACE INTO announcement_texts (announcement_key, text) VALUES (?, ?)",
                (key_name, args.text),
            )
            db.commit()
            print(f"TTS announcement '{name}' created.")
        else:
            db.execute(
                "INSERT INTO announcements (key_name, filename, language, active) VALUES (?, ?, 'fr', 1) "
                "ON CONFLICT(key_name) DO NOTHING",
                (key_name, f"{key_name}.wav16"),
            )
            db.commit()
            print(f"Announcement '{name}' created.")


def create_extension(args):
    """Create a SIP extension."""
    app = create_app()
    with app.app_context():
        db = get_db()

        ext = args.extension
        name = args.name or f"Extension {ext}"
        secret = args.secret or ext

        db.execute(
            "INSERT INTO extensions (ext, callerid_name, sip_password, vm_pin, "
            "enabled, max_contacts, codecs, language, dtmf_mode, musicclass) "
            "VALUES (?, ?, ?, ?, 1, 3, 'g722,ulaw,alaw', 'fr', 'rfc4733', 'default') "
            "ON CONFLICT(ext) DO UPDATE SET callerid_name=excluded.callerid_name, "
            "sip_password=excluded.sip_password",
            (ext, name, secret, "1234"),
        )
        db.execute(
            "INSERT OR REPLACE INTO voicemail_boxes (mailbox, pin, name) VALUES (?, ?, ?)",
            (ext, "1234", name),
        )
        db.commit()
        print(f"Extension {ext} ({name}) created.")


def create_inbound(args):
    """Create an inbound route."""
    app = create_app()
    with app.app_context():
        db = get_db()

        name = args.name or "Default Route"
        destination = args.destination or "extension:4900"

        # Parse destination
        dest_type, dest_value = destination.split(":") if ":" in destination else ("extension", destination)

        # Get first time group if exists
        tg = db.execute("SELECT id FROM time_groups ORDER BY id LIMIT 1").fetchone()
        tg_id = tg["id"] if tg else None

        # Get first active announcement for closed
        ann = db.execute(
            "SELECT key_name FROM announcements WHERE active = 1 LIMIT 1"
        ).fetchone()
        closed_ann = ann["key_name"] if ann else "custom-closed"

        db.execute(
            "INSERT INTO inbound_routes (name, open_target, closed_announcement, time_group_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET open_target=excluded.open_target",
            (name, dest_value, closed_ann, tg_id),
        )
        db.commit()
        print(f"Inbound route '{name}' created.")


def main():
    parser = argparse.ArgumentParser(description="AsterUIX management")
    sub = parser.add_subparsers(dest="command")

    p_admin = sub.add_parser("create-admin", help="Create or reset an admin account")
    p_admin.add_argument("--username", "-u", help="Admin username")
    p_admin.add_argument("--password", "-p", help="Admin password (prompted if omitted)")
    p_admin.set_defaults(func=create_admin)

    p_import = sub.add_parser("import-extensions",
                              help="Import extensions from pjsip.conf + voicemail.conf into DB")
    p_import.add_argument("--pjsip-conf", default="/etc/asterisk/pjsip.conf")
    p_import.add_argument("--voicemail-conf", default="/etc/asterisk/voicemail.conf")
    p_import.add_argument("--generate", action="store_true",
                          help="Also generate managed config files")
    p_import.set_defaults(func=import_extensions)

    p_moh = sub.add_parser("import-moh",
                          help="Import MoH classes from musiconhold.conf into DB")
    p_moh.add_argument("--moh-conf", default="/etc/asterisk/musiconhold.conf")
    p_moh.add_argument("--generate", action="store_true",
                       help="Also generate managed MoH config file")
    p_moh.set_defaults(func=import_moh)

    p_ann = sub.add_parser("import-announcements",
                          help="Import announcement files from sounds directory into DB")
    p_ann.add_argument("--sounds-dir", default="/var/lib/asterisk/sounds/fr")
    p_ann.add_argument("--prefix", default="custom-",
                       help="Only import files starting with this prefix (default: custom-)")
    p_ann.set_defaults(func=import_announcements)

    p_tg = sub.add_parser("import-timegroups",
                          help="Import time-check rules from extensions.conf into DB")
    p_tg.add_argument("--extensions-conf", default="/etc/asterisk/extensions.conf")
    p_tg.add_argument("--name", default="Business Hours",
                      help="Name for the imported time group")
    p_tg.add_argument("--generate", action="store_true",
                      help="Also generate managed config file")
    p_tg.set_defaults(func=import_timegroups)

    p_inbound = sub.add_parser("import-inbound",
                              help="Create initial inbound route from current extensions.conf")
    p_inbound.add_argument("--name", default="Main Trunk",
                          help="Name for the imported route")
    p_inbound.add_argument("--generate", action="store_true",
                          help="Also generate managed config file")
    p_inbound.set_defaults(func=import_inbound)

    p_conf = sub.add_parser("import-conference",
                            help="Import conference room into DB")
    p_conf.add_argument("--extension", default="4949",
                        help="Conference room extension (default: 4949)")
    p_conf.add_argument("--generate", action="store_true",
                        help="Also generate managed ConfBridge config file")
    p_conf.set_defaults(func=import_conference)

    # Create commands for fresh installations
    p_ctg = sub.add_parser("create-timegroup", help="Create a time group")
    p_ctg.add_argument("--name", default="Business Hours", help="Time group name")
    p_ctg.add_argument("--time", required=True, help="Time range (e.g., 09:00-17:00)")
    p_ctg.add_argument("--weekdays", required=True, help="Comma-separated weekdays (mon,tue,wed,thu,fri)")
    p_ctg.set_defaults(func=create_timegroup)

    p_cann = sub.add_parser("create-announcement", help="Create an announcement")
    p_cann.add_argument("--name", required=True, help="Announcement name")
    p_cann.add_argument("--type", choices=["file", "tts"], default="file", help="Announcement type")
    p_cann.add_argument("--text", help="TTS text (required for type=tts)")
    p_cann.set_defaults(func=create_announcement)

    p_cext = sub.add_parser("create-extension", help="Create a SIP extension")
    p_cext.add_argument("--extension", required=True, help="Extension number")
    p_cext.add_argument("--name", help="Caller ID name")
    p_cext.add_argument("--secret", help="SIP password (defaults to extension number)")
    p_cext.add_argument("--context", default="from-internal", help="Dialplan context")
    p_cext.set_defaults(func=create_extension)

    p_cinb = sub.add_parser("create-inbound", help="Create an inbound route")
    p_cinb.add_argument("--name", help="Route name")
    p_cinb.add_argument("--destination", help="Destination (extension:XXXX or conference:XXXX)")
    p_cinb.set_defaults(func=create_inbound)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
