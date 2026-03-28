# AsterUIX Installation Script — Plan

## Goal

Create a single `scripts/install.sh` script that takes a fresh Debian 12 or 13 minimal install and produces a fully working Asterisk 22 LTS + AsterUIX system. The script should be idempotent where possible (safe to re-run).

---

## Phase 1 — System Preparation

### 1.1 Pre-flight checks
- Must run as root
- Detect Debian version (12 bookworm / 13 trixie) — abort on anything else
- Check architecture (x86_64 expected, warn on others)
- Prompt for confirmation before proceeding

### 1.2 System user
- Create `asterisk` system user/group if missing (`useradd --system --home-dir /var/lib/asterisk --shell /usr/sbin/nologin asterisk`)

### 1.3 System packages
```
# Build toolchain
build-essential pkg-config autoconf automake libtool cmake git curl wget

# Asterisk dependencies
libjansson-dev libxml2-dev libsqlite3-dev libssl-dev uuid-dev
libsrtp2-dev libspeex-dev libspeexdsp-dev libopus-dev
libncurses-dev libedit-dev libunbound-dev

# PJSIP (bundled — no system package needed)

# BCG729
libbcg729-dev

# Audio tools (MoH/announcement conversion)
sox libsox-fmt-mp3 ffmpeg

# Python
python3 python3-venv python3-dev

# Optional
fail2ban
```

> **Debian 13 note**: Package names are identical. `libbcg729-dev` exists in both 12 and 13 repos.

---

## Phase 2 — Compile & Install Asterisk 22 LTS

### 2.1 Download source
- Fetch latest 22.x tarball from `https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-22-current.tar.gz`
- Extract to `/usr/local/src/asterisk-22.*/`

### 2.2 Configure
```bash
contrib/scripts/install_prereq install        # catch any missing deps
./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
            --with-jansson --with-ssl --with-srtp --with-pjproject-bundled \
            --with-bcg729 --with-speex --with-opus --with-codec2 \
            --with-sqlite3 --with-unbound
```

Key flags:
| Flag | Why |
|------|-----|
| `--with-pjproject-bundled` | Builds PJSIP from Asterisk's bundled copy — avoids system pjproject version conflicts |
| `--with-bcg729` | Enables `codec_g729.so` (free, open-source G.729 via libbcg729) |
| `--prefix=/usr` | Installs to standard paths (`/usr/sbin/asterisk`, `/usr/lib/asterisk/modules/`) |

### 2.3 Module selection (menuselect)
```bash
make menuselect.makeopts

# Enable key modules
menuselect/menuselect --enable codec_g729 menuselect.makeopts
menuselect/menuselect --enable codec_g722 menuselect.makeopts
menuselect/menuselect --enable codec_opus menuselect.makeopts
menuselect/menuselect --enable format_ogg_vorbis menuselect.makeopts

# Disable modules we don't need
menuselect/menuselect --disable chan_alsa menuselect.makeopts
menuselect/menuselect --disable chan_console menuselect.makeopts
menuselect/menuselect --disable chan_oss menuselect.makeopts
menuselect/menuselect --disable chan_mgcp menuselect.makeopts
menuselect/menuselect --disable chan_skinny menuselect.makeopts
menuselect/menuselect --disable chan_iax2 menuselect.makeopts
menuselect/menuselect --disable chan_unistim menuselect.makeopts

# Enable French sounds (G722 format for quality)
menuselect/menuselect --enable CORE-SOUNDS-FR-G722 menuselect.makeopts
menuselect/menuselect --enable EXTRA-SOUNDS-FR-G722 menuselect.makeopts
# Also keep English as fallback
menuselect/menuselect --enable CORE-SOUNDS-EN-GSM menuselect.makeopts
```

### 2.4 Build & install
```bash
make -j$(nproc)
make install
make samples    # only if /etc/asterisk is empty (first install)
make config     # installs systemd unit (overwrite with our own later)
```

### 2.5 Set permissions
```bash
chown -R asterisk:asterisk /etc/asterisk /var/lib/asterisk \
    /var/spool/asterisk /var/log/asterisk /var/run/asterisk
chmod 750 /etc/asterisk
```

---

## Phase 3 — Asterisk Base Configuration

### 3.1 Systemd unit
Install `/etc/systemd/system/asterisk.service`:
```ini
[Unit]
Description=Asterisk PBX
After=network.target

[Service]
Type=simple
User=asterisk
Group=asterisk
RuntimeDirectory=asterisk
RuntimeDirectoryMode=0755
ExecStart=/usr/sbin/asterisk -f -C /etc/asterisk/asterisk.conf
ExecReload=/usr/sbin/asterisk -rx 'core reload'
ExecStop=/usr/sbin/asterisk -rx 'core stop now'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 3.2 Minimal safe config
- Write `modules.conf` with noload for unused channel drivers (see § 2.3)
- Set `defaultlanguage=fr` in `asterisk.conf` `[options]`
- Ensure `asterisk.conf` has correct `astrundir`, `astlogdir`, etc.

### 3.3 Start & verify
```bash
systemctl daemon-reload
systemctl enable --now asterisk
asterisk -rx "core show version"
asterisk -rx "module show like codec_g729"
asterisk -rx "module show like codec_g722"
```

---

## Phase 4 — Install AsterUIX

### 4.1 Clone the repo
```bash
git clone https://github.com/lemassykoi/asteruix.git /opt/asterisk-webui
```

### 4.2 Python venv & dependencies
```bash
python3 -m venv /opt/asterisk-webui/venv
/opt/asterisk-webui/venv/bin/pip install -r /opt/asterisk-webui/requirements.txt
```

### 4.3 Database & admin user
```bash
mkdir -p /var/lib/asterisk-webui
chown asterisk:asterisk /var/lib/asterisk-webui

# Create admin user (interactive prompt for password)
/opt/asterisk-webui/venv/bin/python /opt/asterisk-webui/manage.py create-admin -u admin
```

### 4.4 Migrate Asterisk configs for WebUI includes
```bash
bash /opt/asterisk-webui/scripts/migrate-includes.sh
```
This adds `#include` directives to the relevant Asterisk config files so the WebUI can manage its own config fragments in `/etc/asterisk/webui/`.

### 4.5 Systemd unit
Install `/etc/systemd/system/asterisk-webui.service`:
```ini
[Unit]
Description=Asterisk SOHO WebUI
After=network.target asterisk.service

[Service]
Type=simple
User=asterisk
Group=asterisk
WorkingDirectory=/opt/asterisk-webui
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/asterisk-webui/venv/bin/python /opt/asterisk-webui/wsgi.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 4.6 Start & verify
```bash
systemctl daemon-reload
systemctl enable --now asterisk-webui
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/
# Should return 200
```

---

## Phase 5 — Restore From Backup (optional)

After a fresh install, the user may want to restore a previous Asterisk configuration from a backup tarball (created by the WebUI or `asterisk-backup.sh`).

### 5.1 What gets backed up
The backup script (`asterisk-backup.sh`) creates a tarball containing:
- `/etc/asterisk/` — all Asterisk configuration files
- `/var/spool/asterisk/voicemail/` — voicemail recordings
- `/var/lib/asterisk/astdb.sqlite3` — Asterisk internal database (spam prefixes, holidays)
- `/var/lib/asterisk-webui/webui.db` — WebUI database (extensions, trunks, ring groups, IVR menus, outbound routes, settings, conference rooms, UI users, etc.)

### 5.2 Install backup/restore scripts
Copy `asterisk-backup.sh` and `asterisk-restore.sh` to `/usr/local/bin/`:
```bash
install -m 755 scripts/asterisk-backup.sh  /usr/local/bin/
install -m 755 scripts/asterisk-restore.sh /usr/local/bin/
```

### 5.3 Restore methods

#### Method 1: `--restore <file>` flag
The install script accepts an optional `--restore /path/to/asterisk-backup-YYYYMMDD-HHMMSS.tar.gz` argument.

#### Method 2: Auto-detection
Place a backup file (`asterisk-backup-*.tar.gz`) in the repo directory (`/opt/asterisk-webui/` or `install/` subfolder). The installer auto-detects it and restores after installation. These files are excluded from git via `.gitignore`.

#### Restore process
When a backup is detected (either method), after Phase 4 completes:

1. Check if backup contains `webui.db` (new format) or not (legacy)
2. Stop Asterisk
3. Extract the tarball to `/`
4. Fix permissions
5. Re-deploy `extensions.conf` from template
6. Re-run `migrate-includes.sh` (in case backup predates current include directives)
7. Ensure `/etc/asterisk/webui/` placeholder files exist
8. Start Asterisk + WebUI
9. **If webui.db present**: Regenerate all 11 managed config files from DB state (pjsip_extensions, voicemail_boxes, pjsip_trunks, confbridge_profiles, extensions_conferences, extensions_ringgroups, extensions_ivr, extensions_inbound, extensions_timegroups, extensions_outbound, musiconhold_classes)
10. **If legacy backup** (no webui.db): Import from config files via `manage.py import-*` commands
11. Reload Asterisk configuration

Since the backup includes the full SQLite database, there are no INSERT conflicts with default data created during install — the restored DB simply replaces it.

### 5.4 Without `--restore`
If no backup is provided or detected, the script leaves Asterisk with sample configs and the WebUI starts with default configuration (extension 4900) — ready for manual setup via the UI.

---

## Phase 6 — Post-Install

### 6.1 Fail2ban
- Already installed in § 1.3
- No custom jail needed (WebUI is LAN-only), but Asterisk security events can be configured later

### 6.2 Print summary
Display:
- Asterisk version + codec verification (G722, G729)
- WebUI URL: `http://<hostname>:8081/`
- Firewall reminder: port 5060/udp (SIP), port 8081/tcp (WebUI — LAN only)
- Backup location: `/var/backups/asterisk/`
- How to create a backup: `asterisk-backup.sh`
- How to restore: `asterisk-restore.sh <file>`

---

## Script Structure

```
scripts/install.sh          ← main entry point
```

### UX decisions
| Aspect | Choice |
|--------|--------|
| Interactivity | Minimal — only ask for admin password. Rest is fully automatic. Accept `-y` flag to skip confirmation prompts. |
| Logging | Tee all output to `/var/log/asteruix-install.log` |
| Error handling | `set -euo pipefail`, trap on ERR to show last command + line number |
| Colors | Green/red/yellow status messages for readability |
| Idempotency | Check before each step (user exists? package installed? already compiled?) |
| Duration | ~10–20 min on a modern VPS (compile is the bottleneck) |

### Function outline
```
main()
  ├── preflight_checks()         # root? debian? arch?
  ├── create_asterisk_user()
  ├── install_system_packages()
  ├── download_asterisk()        # fetch + extract tarball
  ├── compile_asterisk()         # configure + menuselect + make + make install
  ├── set_permissions()
  ├── install_asterisk_service()
  ├── configure_asterisk_base()  # modules.conf, asterisk.conf
  ├── start_asterisk()
  ├── install_asteruix()         # git clone + venv + pip
  ├── setup_database()           # dirs + create-admin
  ├── migrate_includes()         # run migrate-includes.sh
  ├── install_webui_service()
  ├── install_backup_scripts()
  ├── start_webui()
  ├── restore_backup()           # only if --restore <file> was given
  └── print_summary()            # URLs, codec check, firewall hints, backup commands
```

---

## Out of Scope (for now)

- **Trunk configuration** — site-specific, done via the WebUI after install
- **TLS/Let's Encrypt** — depends on whether the box is internet-facing
- **Reverse proxy** — user can set up nginx/caddy separately if needed
- **Asterisk sounds import** — `manage.py import-*` commands are only used during `--restore`, not on fresh installs
- **AI features** — will be added to the repo separately
