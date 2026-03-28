#!/bin/bash
#
# Asterisk Restore Script
# Restores Asterisk configuration, data, and WebUI database from a backup
#
# Usage: asterisk-restore.sh <backup-file.tar.gz> [-y]
#
# Options:
#   -y    Skip confirmation prompt
#

set -euo pipefail

readonly WEBUI_DIR="/opt/asterisk-webui"
readonly WEBUI_DB="/var/lib/asterisk-webui/webui.db"
readonly ASTERISK_USER="asterisk"
readonly ASTERISK_GROUP="asterisk"

SKIP_CONFIRM=false

# Parse arguments
BACKUP_FILE=""
for arg in "$@"; do
    case $arg in
        -y|--yes)
            SKIP_CONFIRM=true
            ;;
        *)
            BACKUP_FILE="$arg"
            ;;
    esac
done

if [[ -z "$BACKUP_FILE" ]]; then
    echo "Usage: $0 <backup-file.tar.gz> [-y]"
    echo ""
    echo "Example: $0 /var/backups/asterisk/asterisk-backup-20260325-120000.tar.gz"
    echo ""
    echo "Available backups:"
    ls -lht /var/backups/asterisk/asterisk-backup-*.tar.gz 2>/dev/null || echo "  (none found)"
    exit 1
fi

echo "=== Asterisk Restore ==="
echo "Backup file: $BACKUP_FILE"
echo ""

# Verify backup file exists
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Check what's in the backup
HAS_WEBUI_DB=false
if tar -tzf "$BACKUP_FILE" 2>/dev/null | grep -q "var/lib/asterisk-webui/webui.db"; then
    HAS_WEBUI_DB=true
    echo "Backup contains WebUI database: YES"
else
    echo "Backup contains WebUI database: NO (will import from config files)"
fi
echo ""

# Confirm restore
if [[ "$SKIP_CONFIRM" != "true" ]]; then
    echo "WARNING: This will overwrite current Asterisk configuration!"
    read -rp "Are you sure you want to proceed? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Restore cancelled."
        exit 0
    fi
fi

# Stop services
echo "Stopping Asterisk and WebUI..."
systemctl stop asterisk-webui 2>/dev/null || true
systemctl stop asterisk || true
sleep 2

# Extract backup
echo "Restoring files from backup..."
tar -xzf "$BACKUP_FILE" -C /

# Fix permissions
echo "Fixing permissions..."
chown -R "$ASTERISK_USER":"$ASTERISK_GROUP" \
    /etc/asterisk \
    /var/spool/asterisk/voicemail \
    /var/lib/asterisk

if [[ -f "$WEBUI_DB" ]]; then
    chown "$ASTERISK_USER":"$ASTERISK_GROUP" "$WEBUI_DB" 2>/dev/null || true
fi

# Re-deploy managed extensions.conf template and create placeholder files
if [[ -f "$WEBUI_DIR/scripts/migrate-includes.sh" ]]; then
    echo "Re-running migrate-includes.sh..."
    bash "$WEBUI_DIR/scripts/migrate-includes.sh"
fi

local_ext_template="$WEBUI_DIR/install/extensions.conf.template"
if [[ -f "$local_ext_template" ]]; then
    echo "Re-deploying managed extensions.conf from template..."
    cp "$local_ext_template" /etc/asterisk/extensions.conf
    chown "$ASTERISK_USER":"$ASTERISK_GROUP" /etc/asterisk/extensions.conf
fi

# Ensure webui config dir exists with placeholders
mkdir -p /etc/asterisk/webui
for conf in pjsip_extensions pjsip_trunks voicemail_boxes musiconhold_classes \
            extensions_inbound extensions_timegroups extensions_ringgroups \
            extensions_conferences extensions_ivr extensions_outbound \
            confbridge_profiles; do
    touch "/etc/asterisk/webui/${conf}.conf"
done
chown -R "$ASTERISK_USER":"$ASTERISK_GROUP" /etc/asterisk/webui

# Regenerate all config files from DB
if [[ "$HAS_WEBUI_DB" == "true" ]]; then
    echo ""
    echo "Regenerating all config files from WebUI database..."
    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    python3 -c "
from app import create_app
from app.generators import (
    write_pjsip_extensions, write_voicemail_boxes, write_pjsip_trunks,
    write_confbridge_profiles, write_conference_extensions,
    write_ring_groups, write_ivr_menus, write_inbound_flow,
    write_timegroups, write_outbound_routes, write_musiconhold_classes,
)

app = create_app()
with app.app_context():
    generators = [
        ('pjsip_extensions.conf', write_pjsip_extensions),
        ('voicemail_boxes.conf', write_voicemail_boxes),
        ('pjsip_trunks.conf', write_pjsip_trunks),
        ('confbridge_profiles.conf', write_confbridge_profiles),
        ('extensions_conferences.conf', write_conference_extensions),
        ('extensions_ringgroups.conf', write_ring_groups),
        ('extensions_ivr.conf', write_ivr_menus),
        ('extensions_inbound.conf', write_inbound_flow),
        ('extensions_timegroups.conf', write_timegroups),
        ('extensions_outbound.conf', write_outbound_routes),
        ('musiconhold_classes.conf', write_musiconhold_classes),
    ]
    for name, writer in generators:
        try:
            writer()
            print(f'  Generated: {name}')
        except Exception as e:
            print(f'  WARN: {name} failed: {e}')
"
    deactivate
else
    # No WebUI DB in backup — import from config files (legacy backup)
    echo ""
    echo "Importing configuration from restored config files..."
    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    for cmd in import-extensions import-moh import-announcements import-timegroups \
               import-inbound import-conference; do
        if python3 manage.py "$cmd" 2>/dev/null; then
            echo "  Imported: $cmd"
        else
            echo "  WARN: Import failed or skipped: $cmd"
        fi
    done

    deactivate
fi

# Start services
echo ""
echo "Starting Asterisk..."
systemctl start asterisk
sleep 2

echo "Starting WebUI..."
systemctl start asterisk-webui 2>/dev/null || true

# Verify
if systemctl is-active --quiet asterisk; then
    echo ""
    echo "Restore completed successfully!"
    echo ""
    echo "Asterisk status:"
    asterisk -rx "core show version" 2>/dev/null || true
    echo ""
    asterisk -rx "core reload" 2>/dev/null || true
    echo "Configuration reloaded."
else
    echo ""
    echo "WARNING: Asterisk failed to start after restore!"
    echo "Check logs: /var/log/asterisk/full"
    exit 1
fi

echo ""
echo "Done!"
