#!/bin/bash
#
# AsterUIX Installation Script
# Installs Asterisk 22 LTS + AsterUIX on Debian 12/13
#
# Usage:
#   sudo ./install.sh [-y] [--restore <backup.tar.gz>]
#
# Options:
#   -y      Skip confirmation prompts
#   --restore <file>  Restore from backup after installation
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

readonly SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOG_FILE="/var/log/asteruix-install.log"
readonly WEBUI_DIR="/opt/asterisk-webui"
readonly DEBIAN_VERSIONS=("12" "13")

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m' # No Color

# Flags
SKIP_CONFIRM=false
RESTORE_FILE=""

# =============================================================================
# Logging & Output Functions
# =============================================================================

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "[$timestamp] $*" | tee -a "$LOG_FILE"
}

info() {
    log "${GREEN}[INFO]${NC} $*"
}

warn() {
    log "${YELLOW}[WARN]${NC} $*"
}

error() {
    log "${RED}[ERROR]${NC} $*"
}

die() {
    error "$*"
    exit 1
}

# =============================================================================
# Error Handler
# =============================================================================

error_handler() {
    local line_no=$1
    local exit_code=$?
    error "Script failed at line $line_no with exit code $exit_code"
    error "Last command: $BASH_COMMAND"
    error "Check $LOG_FILE for details"
    if [[ -f "$LOG_FILE" ]]; then
        error "Last log entries:"
        tail -20 "$LOG_FILE" | while read -r line; do
            error "  $line"
        done
    fi
    exit 1
}

trap 'error_handler ${LINENO}' ERR

# =============================================================================
# Helper Functions
# =============================================================================

require_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root (use sudo)"
    fi
}

# =============================================================================
# Pre-flight Checks
# =============================================================================

preflight_checks() {
    info "Running pre-flight checks..."

    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root"
    fi
    info "Running as root"

    if [[ ! -f /etc/debian_version ]]; then
        die "This script requires Debian Linux"
    fi

    local debian_version
    debian_version=$(cat /etc/debian_version)
    local major_version
    major_version=$(echo "$debian_version" | cut -d'.' -f1)

    local version_supported=false
    for version in "${DEBIAN_VERSIONS[@]}"; do
        if [[ "$major_version" == "$version" ]]; then
            version_supported=true
            break
        fi
    done

    if [[ "$version_supported" != "true" ]]; then
        die "Unsupported Debian version: $debian_version (requires Debian 12 or 13)"
    fi
    info "Detected Debian $major_version"

    local arch
    arch=$(uname -m)
    if [[ "$arch" != "x86_64" ]]; then
        warn "Unexpected architecture: $arch (x86_64 expected)"
    else
        info "Architecture: $arch"
    fi
}

# =============================================================================
# Confirmation Prompt
# =============================================================================

prompt_confirmation() {
    if [[ "$SKIP_CONFIRM" == "true" ]]; then
        info "Skipping confirmation (auto-yes mode)"
        return
    fi

    echo ""
    echo "=============================================="
    echo "  AsterUIX Installation"
    echo "=============================================="
    echo ""
    echo "This script will install:"
    echo "  - Asterisk 22 LTS (using build_asterisk_22.sh)"
    echo "  - AsterUIX WebUI"
    echo ""
    echo "Estimated time: 15-25 minutes"
    echo ""
    read -rp "Proceed with installation? [y/N] " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Installation cancelled by user"
        exit 0
    fi

    info "User confirmed"
}

# =============================================================================
# Phase 1 - Install Asterisk 22 using build_asterisk_22.sh
# =============================================================================

install_asterisk() {
    info "=== Phase 1: Installing Asterisk 22 LTS ==="

    local build_script="$SCRIPT_DIR/build_asterisk_22.sh"

    if [[ ! -f "$build_script" ]]; then
        die "Build script not found: $build_script"
    fi

    if [[ ! -x "$build_script" ]]; then
        info "Making build script executable..."
        chmod +x "$build_script"
    fi

    info "Running Asterisk build script: $build_script"
    
    # Run the build script
    bash "$build_script"

    if [[ $? -eq 0 ]]; then
        info "Asterisk 22 installation completed successfully"
        sleep 2
    else
        die "Asterisk installation failed"
    fi

    # Configure asterisk.ctl for CLI access (required by AsterUIX)
    configure_asterisk_ctl

    # Verify Asterisk is running
    if systemctl is-active --quiet asterisk; then
        local version
        version=$(asterisk -rx "core show version" 2>/dev/null | head -1)
        info "Asterisk version: $version"
    else
        warn "Asterisk is not running - check logs"
    fi
}

configure_asterisk_ctl() {
    info "Configuring asterisk.ctl for CLI access..."

    local asterisk_conf="/etc/asterisk/asterisk.conf"

    if [[ -f "$asterisk_conf" ]]; then
        # Uncomment [files] section
        sed -i 's/^;\[files\]/[files]/' "$asterisk_conf"
        # Uncomment astctl settings
        sed -i 's/^;astctlpermissions/astctlpermissions/' "$asterisk_conf"
        sed -i 's/^;astctlowner/astctlowner/' "$asterisk_conf"
        sed -i 's/^;astctlgroup/astctlgroup/' "$asterisk_conf"
        sed -i 's/^;astctl/astctl/' "$asterisk_conf"
        # Set correct group (asterisk instead of apache)
        sed -i 's/^astctlgroup = apache/astctlgroup = asterisk/' "$asterisk_conf"

        info "asterisk.ctl configured"
    else
        warn "asterisk.conf not found - skipping asterisk.ctl configuration"
    fi
}

# =============================================================================
# Phase 2 - Install WebUI Dependencies
# =============================================================================

install_webui_dependencies() {
    info "=== Phase 2: Installing WebUI Dependencies ==="

    info "Installing WebUI-specific packages..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        sox \
        libsox-fmt-mp3 \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        fail2ban

    info "WebUI dependencies installed successfully"
}

# =============================================================================
# Phase 3 - Install AsterUIX WebUI
# =============================================================================

install_asteruix() {
    info "=== Phase 3: Installing AsterUIX WebUI ==="

    # Clone the repo if not already present
    if [[ ! -d "$WEBUI_DIR/.git" ]]; then
        git clone https://github.com/lemassykoi/asteruix.git "$WEBUI_DIR"
        info "Cloned AsterUIX to $WEBUI_DIR"
    else
        info "AsterUIX already installed at $WEBUI_DIR"
    fi

    cd "$WEBUI_DIR"

    # Create Python venv if not exists
    if [[ ! -d "$WEBUI_DIR/venv" ]]; then
        python3 -m venv venv
        info "Created Python virtual environment"
    else
        info "Python virtual environment already exists"
    fi

    # Activate venv and install dependencies
    source "$WEBUI_DIR/venv/bin/activate"
    pip install -q -r requirements.txt
    info "Python dependencies installed"
    deactivate
}

setup_database() {
    info "Setting up AsterUIX database..."

    local db_dir="/var/lib/asterisk-webui"
    mkdir -p "$db_dir"
    chown asterisk:asterisk "$db_dir"
    info "Database directory created: $db_dir"

    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    if python3 manage.py list-admins 2>/dev/null | grep -q "admin"; then
        info "Admin user already exists"
    else
        local admin_password=""
        if [[ "$SKIP_CONFIRM" == "true" ]]; then
            admin_password="admin123"
            warn "Using default admin password: admin123 (change after login!)"
        else
            echo ""
            echo "Create admin user for AsterUIX WebUI"
            echo "--------------------------------------"
            read -rp "Enter admin password: " -s admin_password
            echo ""
            while [[ -z "$admin_password" ]]; do
                warn "Password cannot be empty"
                read -rp "Enter admin password: " -s admin_password
                echo ""
            done
        fi

        python3 manage.py create-admin -u admin -p "$admin_password"
        info "Admin user created"
    fi

    deactivate
}

migrate_includes() {
    info "Migrating Asterisk configs for WebUI includes..."

    cd "$WEBUI_DIR"

    info "Creating empty WebUI config placeholders..."
    mkdir -p /etc/asterisk/webui
    touch /etc/asterisk/webui/pjsip_extensions.conf
    touch /etc/asterisk/webui/pjsip_trunks.conf
    touch /etc/asterisk/webui/voicemail_boxes.conf
    touch /etc/asterisk/webui/musiconhold_classes.conf
    touch /etc/asterisk/webui/extensions_inbound.conf
    touch /etc/asterisk/webui/extensions_timegroups.conf
    touch /etc/asterisk/webui/confbridge_profiles.conf

    if [[ -f "$WEBUI_DIR/scripts/migrate-includes.sh" ]]; then
        bash "$WEBUI_DIR/scripts/migrate-includes.sh"
        info "Asterisk config migration complete"
    else
        warn "migrate-includes.sh not found - skipping"
    fi
}

import_config() {
    info "Importing existing Asterisk configuration..."

    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    local import_commands=(
        "import-extensions"
        "import-moh"
        "import-announcements"
        "import-timegroups"
        "import-inbound"
        "import-conference"
    )

    for cmd in "${import_commands[@]}"; do
        if python3 manage.py "$cmd" 2>/dev/null; then
            info "Imported: $cmd"
        else
            warn "Import failed or skipped: $cmd"
        fi
    done

    deactivate
}

create_default_config() {
    info "Creating default configuration..."

    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    info "Creating default time group (Business Hours)..."
    python3 manage.py create-timegroup \
        --name "Business Hours" \
        --time "09:00-17:00" \
        --weekdays "mon,tue,wed,thu,fri" 2>/dev/null || \
    info "Time group created or already exists"

    info "Creating welcome announcement..."
    python3 manage.py create-announcement \
        --name "Welcome" \
        --type "tts" \
        --text "Welcome to your new Asterisk phone system. Please contact your administrator for extension setup." 2>/dev/null || \
    info "Welcome announcement created or already exists"

    info "Creating default extension 4900..."
    python3 manage.py create-extension \
        --extension "4900" \
        --name "Default User" \
        --secret "4900" \
        --context "from-internal" 2>/dev/null || \
    info "Extension 4900 created or already exists"

    info "Creating default inbound route..."
    python3 manage.py create-inbound \
        --name "Default Route" \
        --destination "extension:4900" 2>/dev/null || \
    info "Default inbound route created or already exists"

    info "Populating spam database with French spam prefixes..."
    python3 manage.py populate-spam-db 2>/dev/null || \
    info "Spam database populated (or already exists)"

    info "Reloading Asterisk configuration..."
    asterisk -rx "core reload" 2>/dev/null || true

    deactivate

    info "Default configuration complete"
    info ""
    info "=== Default Configuration ==="
    info "Extension: 4900"
    info "Password:  4900"
    info "Time Group: Business Hours (Mon-Fri, 9am-5pm)"
    info ""
}

install_webui_service() {
    info "Installing AsterUIX WebUI systemd service..."

    cat > /etc/systemd/system/asterisk-webui.service << 'EOF'
[Unit]
Description=Asterisk WebUI
After=network.target asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/asterisk-webui
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/asterisk-webui/venv/bin"
ExecStart=/opt/asterisk-webui/venv/bin/waitress-serve --host=0.0.0.0 --port=8081 wsgi:application
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    info "AsterUIX WebUI systemd service installed"
}

start_webui() {
    info "Starting AsterUIX WebUI..."

    systemctl daemon-reload
    systemctl enable asterisk-webui
    systemctl start asterisk-webui

    sleep 2

    if systemctl is-active --quiet asterisk-webui; then
        info "AsterUIX WebUI started successfully"

        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/login 2>/dev/null || echo "000")
        if [[ "$http_code" == "200" ]]; then
            info "WebUI login page responding (HTTP $http_code)"
        else
            warn "WebUI login page not responding (HTTP $http_code)"
        fi
    else
        warn "AsterUIX WebUI failed to start - check logs"
    fi
}

# =============================================================================
# Phase 4 - Backup Scripts & Optional Restore
# =============================================================================

install_backup_scripts() {
    info "Installing backup/restore scripts..."

    if [[ -f "$WEBUI_DIR/scripts/asterisk-backup.sh" ]]; then
        install -m 755 "$WEBUI_DIR/scripts/asterisk-backup.sh" /usr/local/bin/
        info "Installed asterisk-backup.sh"
    else
        warn "asterisk-backup.sh not found"
    fi

    if [[ -f "$WEBUI_DIR/scripts/asterisk-restore.sh" ]]; then
        install -m 755 "$WEBUI_DIR/scripts/asterisk-restore.sh" /usr/local/bin/
        info "Installed asterisk-restore.sh"
    else
        warn "asterisk-restore.sh not found"
    fi
}

restore_backup() {
    if [[ -z "$RESTORE_FILE" ]]; then
        info "No backup file specified - skipping restore"
        return
    fi

    info "=== Restoring from Backup ==="
    info "Restoring from: $RESTORE_FILE"

    if [[ ! -f "$RESTORE_FILE" ]]; then
        die "Backup file not found: $RESTORE_FILE"
    fi

    systemctl stop asterisk || true

    info "Extracting backup..."
    tar -xzf "$RESTORE_FILE" -C /

    info "Fixing permissions..."
    chown -R asterisk:asterisk \
        /etc/asterisk \
        /var/spool/asterisk/voicemail \
        /var/lib/asterisk

    if [[ -f "$WEBUI_DIR/scripts/migrate-includes.sh" ]]; then
        info "Re-running migrate-includes.sh..."
        bash "$WEBUI_DIR/scripts/migrate-includes.sh"
    fi

    info "Starting Asterisk..."
    systemctl start asterisk

    info "Restarting WebUI..."
    systemctl restart asterisk-webui

    info "Importing configuration into WebUI database..."
    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    local import_commands=(
        "import-extensions"
        "import-moh"
        "import-announcements"
        "import-timegroups"
        "import-inbound"
        "import-conference"
    )

    for cmd in "${import_commands[@]}"; do
        if python3 manage.py "$cmd" 2>/dev/null; then
            info "Imported: $cmd"
        else
            warn "Import failed or skipped: $cmd"
        fi
    done

    deactivate

    info "Backup restore completed successfully"
}

# =============================================================================
# Phase 5 - Post-Install Verification & Summary
# =============================================================================

verify_codecs() {
    info "Verifying codec availability..."

    if ! systemctl is-active --quiet asterisk; then
        warn "Asterisk is not running - skipping codec verification"
        return
    fi

    sleep 1

    local codec_output
    codec_output=$(asterisk -rx "core show codecs" 2>&1)

    if echo "$codec_output" | grep -qi "g722"; then
        info "  [OK] codec_g722 (G.722)"
    else
        warn "  [MISSING] codec_g722 (G.722)"
    fi

    if echo "$codec_output" | grep -qi "g729"; then
        info "  [OK] codec_g729 (G.729)"
    else
        warn "  [MISSING] codec_g729 (G.729)"
    fi

    if echo "$codec_output" | grep -qi "opus"; then
        info "  [OK] codec_opus (Opus)"
    else
        warn "  [MISSING] codec_opus (Opus)"
    fi
}

print_summary() {
    echo ""
    echo "=============================================="
    echo "  Installation Complete!"
    echo "=============================================="
    echo ""

    local version
    version=$(asterisk -rx "core show version" 2>/dev/null | head -1)
    echo "Asterisk: $version"
    echo ""

    echo "Codecs:"
    local codec_list
    codec_list=$(asterisk -rx "core show codecs" 2>/dev/null | grep -E "g722|g729|opus")
    if [[ -n "$codec_list" ]]; then
        echo "$codec_list" | awk '{print "  - " $2 " (" $4 ")"}'
    else
        echo "  (unable to query)"
    fi
    echo ""

    local hostname
    hostname=$(hostname -f 2>/dev/null || hostname)
    echo "AsterUIX WebUI: http://$hostname:8081/"
    echo "  Login: admin"
    if [[ "$SKIP_CONFIRM" == "true" ]]; then
        echo "  Password: admin123 (CHANGE THIS!)"
    else
        echo "  Password: (as set during installation)"
    fi
    echo ""

    echo "Firewall Configuration:"
    echo "  - Port 5060/udp  : SIP signaling"
    echo "  - Port 10000-20000/udp : RTP media"
    echo "  - Port 8081/tcp  : WebUI"
    echo ""

    echo "Backup & Restore:"
    echo "  - Backup location: /var/backups/asterisk/"
    echo "  - Create backup:   asterisk-backup.sh"
    echo "  - Restore backup:  asterisk-restore.sh <file.tar.gz>"
    echo ""

    echo "Useful Commands:"
    echo "  - asterisk -rx 'core show channels'  : Show active channels"
    echo "  - asterisk -rx 'pjsip show endpoints': Show PJSIP endpoints"
    echo "  - systemctl status asterisk          : Check Asterisk status"
    echo "  - systemctl status asterisk-webui    : Check WebUI status"
    echo ""
    echo "Logs:"
    echo "  - Installation log: $LOG_FILE"
    echo "  - Asterisk logs:    /var/log/asterisk/"
    echo ""
    echo "=============================================="
}

# =============================================================================
# Parse Command Line Arguments
# =============================================================================

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -y|--yes)
                SKIP_CONFIRM=true
                shift
                ;;
            --restore)
                if [[ -n "${2:-}" ]]; then
                    RESTORE_FILE="$2"
                    shift 2
                else
                    die "Option --restore requires a file path argument"
                fi
                ;;
            -h|--help)
                echo "Usage: $SCRIPT_NAME [-y] [--restore <backup.tar.gz>]"
                echo ""
                echo "Options:"
                echo "  -y, --yes           Skip confirmation prompts"
                echo "  --restore <file>    Restore from backup after installation"
                echo "  -h, --help          Show this help message"
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done
}

# =============================================================================
# Main Entry Point
# =============================================================================

main() {
    parse_args "$@"

    mkdir -p "$(dirname "$LOG_FILE")"
    : > "$LOG_FILE"

    echo ""
    echo "=============================================="
    echo "  AsterUIX Installation Script"
    echo "  Log file: $LOG_FILE"
    echo "=============================================="
    echo ""

    # Pre-flight
    preflight_checks
    prompt_confirmation

    # Phase 1: Install Asterisk 22
    install_asterisk
    echo ""

    # Phase 2: Install WebUI dependencies
    install_webui_dependencies
    echo ""

    # Phase 3: Install AsterUIX WebUI
    info "=== Phase 3: AsterUIX WebUI Installation ==="
    install_asteruix
    setup_database
    migrate_includes
    import_config
    create_default_config
    install_webui_service
    start_webui
    info "Phase 3 completed successfully"
    echo ""

    # Phase 4: Backup scripts & optional restore
    info "=== Phase 4: Backup/Restore ==="
    install_backup_scripts
    restore_backup
    echo ""

    # Phase 5: Verification & Summary
    info "=== Phase 5: Post-Install Verification ==="
    verify_codecs
    print_summary

    info "Installation completed successfully!"
}

# Run main function
main "$@"
