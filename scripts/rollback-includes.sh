#!/usr/bin/env bash
#===============================================================================
# rollback-includes.sh — Restore Asterisk configs from a pre-migration snapshot
# Usage: rollback-includes.sh /opt/asterisk-webui/config-snapshots/pre-migration-XXXXX
#===============================================================================
set -euo pipefail

SNAPSHOT_DIR="${1:-}"

if [ -z "$SNAPSHOT_DIR" ] || [ ! -d "$SNAPSHOT_DIR" ]; then
    echo "Usage: $0 <snapshot-directory>"
    echo ""
    echo "Available snapshots:"
    ls -1d /opt/asterisk-webui/config-snapshots/pre-migration-* 2>/dev/null || echo "  (none)"
    exit 1
fi

echo "=== Rollback from: $SNAPSHOT_DIR ==="

for f in "$SNAPSHOT_DIR"/*; do
    base="$(basename "$f")"
    target="/etc/asterisk/$base"
    cp -a "$f" "$target"
    echo "  RESTORED  $target"
done

echo ""
echo "=== Reloading Asterisk ==="
asterisk -rx "core reload" 2>&1 || true
echo "Rollback complete."
