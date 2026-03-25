#!/bin/bash
#
# Asterisk Restore Script
# Restores Asterisk configuration and data from a backup
#

set -euo pipefail

# Check arguments
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <backup-file.tar.gz>"
    echo ""
    echo "Example: $0 /var/backups/asterisk/asterisk-backup-20260325-120000.tar.gz"
    exit 1
fi

readonly BACKUP_FILE="$1"
readonly ASTERISK_USER="asterisk"
readonly ASTERISK_GROUP="asterisk"

echo "=== Asterisk Restore ==="
echo "Backup file: $BACKUP_FILE"
echo ""

# Verify backup file exists
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Confirm restore
echo "WARNING: This will overwrite current Asterisk configuration!"
read -rp "Are you sure you want to proceed? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Restore cancelled."
    exit 0
fi

# Stop Asterisk
echo "Stopping Asterisk..."
systemctl stop asterisk
sleep 2

# Extract backup
echo "Restoring configuration..."
tar -xzf "$BACKUP_FILE" -C /

# Fix permissions
echo "Fixing permissions..."
chown -R "$ASTERISK_USER":"$ASTERISK_GROUP" \
    /etc/asterisk \
    /var/spool/asterisk/voicemail \
    /var/lib/asterisk

# Start Asterisk
echo "Starting Asterisk..."
systemctl start asterisk
sleep 2

# Verify Asterisk is running
if systemctl is-active --quiet asterisk; then
    echo ""
    echo "Restore completed successfully!"
    echo ""
    echo "Asterisk status:"
    asterisk -rx "core show version" 2>/dev/null || true
else
    echo ""
    echo "WARNING: Asterisk failed to start after restore!"
    echo "Check logs: /var/log/asterisk/full"
    exit 1
fi

echo ""
echo "Done!"
