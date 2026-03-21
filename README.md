# Asterisk WebUI

A lightweight web interface for managing an Asterisk 22 LTS PBX, built with Flask. No FreePBX/VitalPBX — pure Asterisk with a minimal, purpose-built management layer.

## Features

- **Dashboard** — Live system status: Asterisk version, uptime, registered endpoints, active calls, Fail2ban bans
- **Extensions** — CRUD for PJSIP endpoints (4900–4904) with voicemail box sync
- **Trunks** — Registration-based and identify-only SIP trunk management
- **Music on Hold** — MoH class/track management with automatic MP3/WAV/OGG → 16kHz WAV conversion
- **Announcements** — Upload and activate closed-hours announcements (auto-converted to G722-compatible WAV)
- **Voicemail** — Per-mailbox message listing, in-browser playback, delete, blast configuration
- **Time Groups** — Business hours rules with day/time selectors and overlap detection
- **Holidays** — Fixed (MMDD) and variable (YYYYMMDD) holiday management via AstDB
- **Spam Blocking** — 4-digit caller ID prefix blacklist via AstDB, single + bulk import
- **Inbound Routes** — Visual 5-step call flow editor (spam → holidays → time → open/closed routing)
- **Conference Rooms** — ConfBridge room settings (max members, MoH class, announce join/leave)
- **Dialplan Visualization** — Read-only CSS flowchart of the inbound call path with live config values
- **Backup/Restore** — Create and restore full Asterisk config backups from the UI

## Architecture

- **No GUI database for Asterisk config** — the WebUI generates native Asterisk `.conf` files via `#include` directives, so Asterisk always reads plain config files
- **AstDB as source of truth** for runtime data (spam prefixes, holidays) — changes take effect immediately without reload
- **Atomic config writes** — temp file + rename to avoid partial writes
- **Config snapshots** — automatic snapshots before every config change for easy rollback
- **Audit logging** — dual-write to SQLite + file log for all mutations

## Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.11 |
| Framework | Flask 3.1.1 |
| Database | SQLite (WAL mode) |
| Auth | bcrypt + session cookies (HttpOnly, SameSite=Strict, 30-min timeout) |
| CSRF | Flask-WTF |
| Frontend | Server-rendered Jinja2 + vanilla CSS/JS |
| Process | Waitress WSGI behind systemd |

## Requirements

- Debian 12 (bookworm)
- Asterisk 22 LTS (compiled from source with PJSIP)
- Python 3.11+
- sox + libsox-fmt-mp3 (for audio conversion)
- Fail2ban (optional, for dashboard display)

## Installation

```bash
# Clone
git clone https://github.com/lemassykoi/asterisk-webui.git /opt/asterisk-webui
cd /opt/asterisk-webui

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Migrate Asterisk config to use #include directives
bash scripts/migrate-includes.sh

# Initialize database & create admin user
python manage.py create-admin -u admin -p <password>

# Import existing Asterisk config into the database
python manage.py import-extensions
python manage.py import-moh
python manage.py import-announcements
python manage.py import-timegroups
python manage.py import-inbound
python manage.py import-conference

# Start
systemctl enable --now asterisk-webui
```

The WebUI listens on `0.0.0.0:8081` (all interfaces) so it can be accessed from the LAN — the Asterisk server typically has no GUI or local browser.

## Project Structure

```
app/
├── __init__.py          # Flask app factory
├── asterisk_cmd.py      # Asterisk CLI adapter (allowlisted commands, typed parsers)
├── audit.py             # Audit logging (SQLite + file)
├── auth.py              # Login/logout, bcrypt, session management
├── db.py                # SQLite schema, migrations
├── generators.py        # Config file generators (PJSIP, dialplan, MoH, ConfBridge)
├── snapshots.py         # Config snapshot/restore
├── extensions.py        # Extensions CRUD + API
├── trunks.py            # Trunks CRUD + API
├── moh.py               # Music on Hold management
├── announcements.py     # Announcements management
├── voicemail.py         # Voicemail operations + blast config
├── timegroups.py        # Time group rules
├── holidays.py          # Fixed/variable holidays (AstDB)
├── spam.py              # Spam prefix blacklist (AstDB)
├── inbound.py           # Inbound route flow editor
├── conference.py        # ConfBridge room settings
├── dialplan.py          # Dialplan visualization
├── backups.py           # Backup/restore integration
├── system.py            # Dashboard + system status API
├── routes.py            # Core routes
├── templates/           # Jinja2 templates
└── static/css/          # Stylesheet
manage.py                # CLI management commands
wsgi.py                  # WSGI entry point
scripts/
├── migrate-includes.sh  # Add #include directives to Asterisk configs
└── rollback-includes.sh # Remove #include directives
tests/
└── test_asterisk_cmd.py # Unit tests for Asterisk adapter
```

## License

Private — not licensed for redistribution.
