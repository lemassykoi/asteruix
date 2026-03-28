#!/bin/bash
#
# Asterisk Backup Script
# Creates a backup of Asterisk configuration, data, and WebUI database
#

set -euo pipefail

readonly BACKUP_DIR="/var/backups/asterisk"
readonly TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
readonly BACKUP_FILE="asterisk-backup-${TIMESTAMP}.tar.gz"

# Directories and files to backup
readonly BACKUP_PATHS=(
    "/etc/asterisk"
    "/var/spool/asterisk/voicemail"
    "/var/lib/asterisk/astdb.sqlite3"
    "/var/lib/asterisk-webui/webui.db"
)

echo "=== Asterisk Backup ==="
echo "Backup directory: $BACKUP_DIR"
echo "Backup file: $BACKUP_FILE"
echo ""

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Stop Asterisk for consistent backup
echo "Stopping Asterisk..."
systemctl stop asterisk || true
sleep 2

# Create backup
echo "Creating backup..."
tar -czf "${BACKUP_DIR}/${BACKUP_FILE}" \
    --ignore-failed-read \
    "${BACKUP_PATHS[@]}" 2>/dev/null || true

# Restart Asterisk
echo "Starting Asterisk..."
systemctl start asterisk

# Verify backup
if [[ -f "${BACKUP_DIR}/${BACKUP_FILE}" ]]; then
    backup_size=$(ls -lh "${BACKUP_DIR}/${BACKUP_FILE}" | awk '{print $5}')
    echo ""
    echo "Backup completed successfully!"
    echo "File: ${BACKUP_DIR}/${BACKUP_FILE} ($backup_size)"
    echo ""
    echo "Contents:"
    tar -tzf "${BACKUP_DIR}/${BACKUP_FILE}" | head -20
    echo "..."
else
    echo "ERROR: Backup failed!"
    exit 1
fi

# Cleanup old backups (keep last 10)
echo ""
echo "Cleaning up old backups..."
cd "$BACKUP_DIR"
ls -t asterisk-backup-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "Kept last 10 backups"

echo ""
echo "Done!"
