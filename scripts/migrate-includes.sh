#!/usr/bin/env bash
#===============================================================================
# migrate-includes.sh — Add WebUI managed-include directives to Asterisk configs
# Idempotent: safe to run multiple times.
# Snapshot: backs up originals before first modification.
#===============================================================================
set -euo pipefail

BACKUP_DIR="/opt/asterisk-webui/config-snapshots/pre-migration-$(date +%Y%m%d-%H%M%S)"
WEBUI_DIR="/etc/asterisk/webui"
MARKER="; --- WebUI managed includes ---"
CHANGED=0

add_include() {
    local conf="$1"
    local include_line="$2"

    if grep -qF "$include_line" "$conf" 2>/dev/null; then
        echo "  SKIP  $conf (already has: $include_line)"
        return
    fi

    # Snapshot original before first edit
    if [ ! -f "$BACKUP_DIR/$(basename "$conf")" ]; then
        mkdir -p "$BACKUP_DIR"
        cp -a "$conf" "$BACKUP_DIR/"
        echo "  SNAP  $conf -> $BACKUP_DIR/"
    fi

    # Append include block
    printf '\n%s\n%s\n' "$MARKER" "$include_line" >> "$conf"
    echo "  ADD   $conf : $include_line"
    CHANGED=1
}

echo "=== WebUI Include Migration ==="

# pjsip.conf — extensions + trunks
add_include "/etc/asterisk/pjsip.conf" "#include \"$WEBUI_DIR/pjsip_extensions.conf\""
add_include "/etc/asterisk/pjsip.conf" "#include \"$WEBUI_DIR/pjsip_trunks.conf\""

# voicemail.conf — managed boxes
add_include "/etc/asterisk/voicemail.conf" "#include \"$WEBUI_DIR/voicemail_boxes.conf\""

# musiconhold.conf — managed classes
add_include "/etc/asterisk/musiconhold.conf" "#include \"$WEBUI_DIR/musiconhold_classes.conf\""

# extensions.conf — inbound flow, time groups, ring groups, conferences, IVR, outbound
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_inbound.conf\""
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_timegroups.conf\""
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_ringgroups.conf\""
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_conferences.conf\""
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_ivr.conf\""
add_include "/etc/asterisk/extensions.conf" "#include \"$WEBUI_DIR/extensions_outbound.conf\""

# confbridge.conf — managed profiles
add_include "/etc/asterisk/confbridge.conf" "#include \"$WEBUI_DIR/confbridge_profiles.conf\""

if [ "$CHANGED" -eq 1 ]; then
    echo ""
    echo "=== Reloading Asterisk ==="
    asterisk -rx "core reload" 2>&1 || true
    echo "Done. Snapshot saved to: $BACKUP_DIR"
else
    echo ""
    echo "No changes needed — all includes already present."
fi
