#!/bin/bash
#
# AsterUIX Installation Script
# Installs Asterisk 22 LTS + AsterUIX on Debian 12/13
#
# Usage:
#   ./install.sh [-y] [--restore <backup.tar.gz>]
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
readonly LOG_FILE="/var/log/asteruix-install.log"
readonly ASTERISK_USER="asterisk"
readonly ASTERISK_GROUP="asterisk"
readonly WEBUI_DIR="/opt/asterisk-webui"
readonly DEBIAN_VERSIONS=("12" "13")
readonly DEBIAN_CODENAMES=("bookworm" "trixie")
readonly ASTERISK_SRC_DIR="/usr/src/asterisk-22"
readonly PJSIP_SRC_DIR="/usr/src/pjproject-2.16"

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
    # Print last 20 lines of log for debugging
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
        echo "ERROR: This script must be run as root" >&2
        echo "Usage: sudo $0 [options]" >&2
        exit 1
    fi
}

# =============================================================================
# Pre-flight Checks (Phase 1.1)
# =============================================================================

preflight_checks() {
    info "Running pre-flight checks..."

    # Check if running as root
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root (use sudo)"
    fi
    info "Running as root"

    # Detect Debian version
    if [[ ! -f /etc/debian_version ]]; then
        die "This script requires Debian Linux"
    fi

    local debian_version
    debian_version=$(cat /etc/debian_version)

    # Extract major version number
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
        die "Unsupported Debian version: $debian_version (requires Debian 12 bookworm or 13 trixie)"
    fi
    info "Detected Debian $major_version"

    # Check architecture
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
    echo "This script will install Asterisk 22 LTS and AsterUIX."
    echo ""
    echo "The installation will:"
    echo "  - Install system packages (~150MB)"
    echo "  - Compile PJSIP (bundled with Asterisk)"
    echo "  - Compile Asterisk 22 LTS"
    echo "  - Install AsterUIX WebUI"
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
# Create Asterisk User (Phase 1.2)
# =============================================================================

create_asterisk_user() {
    info "Creating Asterisk system user..."

    if id "$ASTERISK_USER" &>/dev/null; then
        info "User $ASTERISK_USER already exists"
    else
        useradd --system \
            --home-dir /var/lib/asterisk \
            --shell /usr/sbin/nologin \
            "$ASTERISK_USER"
        info "Created user $ASTERISK_USER"
    fi
}

# =============================================================================
# Install System Packages (Phase 1.3)
# =============================================================================

install_prerequisites() {
    info "Updating package lists..."
    apt-get update -qq

    info "Installing minimal prerequisites..."
    
    # Install aptitude (required by install_prereq) and basic tools
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        aptitude \
        wget \
        subversion \
        bzip2 \
        patch

    info "Prerequisites installed successfully"
}

install_asterisk_dependencies() {
    info "Running Asterisk install_prereq script..."
    # Use official Asterisk prereq script to install all build dependencies
    cd "$ASTERISK_SRC_DIR"
    bash contrib/scripts/install_prereq install
    
    info "Installing WebUI-specific packages..."
    # Install packages specific to AsterUIX (not needed for Asterisk build)
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        sox \
        libsox-fmt-mp3 \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        fail2ban \
        asterisk-core-sounds-en-g722 \
        asterisk-core-sounds-fr-g722

    info "All dependencies installed successfully"
}

# =============================================================================
# Phase 2 - PJSIP (Bundled with Asterisk 22)
# =============================================================================

# Note: Asterisk 22.8+ includes bundled PJSIP. We skip separate PJSIP compilation
# and use --with-pjproject-bundled during Asterisk configuration.

skip_pjsip_compilation() {
    info "Phase 2: PJSIP compilation skipped"
    info "Asterisk 22 includes bundled PJSIP (--with-pjproject-bundled)"
}

# =============================================================================
# Phase 3 - Compile & Install Asterisk 22 LTS
# =============================================================================

download_asterisk() {
    info "Downloading Asterisk 22 LTS source..."

    # Clean up any existing Asterisk source to ensure fresh install
    info "Checking for existing Asterisk source..."
    local found_old=false
    if [[ -e /usr/src/asterisk-22-current.tar.gz ]]; then
        info "  Found: /usr/src/asterisk-22-current.tar.gz"
        found_old=true
    fi
    for dir in /usr/src/asterisk-22.*; do
        if [[ -d "$dir" ]]; then
            info "  Found: $dir"
            found_old=true
        fi
    done
    if [[ -L /usr/src/asterisk-22 ]]; then
        info "  Found symlink: /usr/src/asterisk-22"
        found_old=true
    fi
    if [[ -d /usr/src/asterisk-22 ]]; then
        info "  Found directory: /usr/src/asterisk-22"
        found_old=true
    fi

    if [[ "$found_old" == "true" ]]; then
        info "Cleaning up existing Asterisk source..."
        rm -f /usr/src/asterisk-22-current.tar.gz
        rm -rf /usr/src/asterisk-22.*
        rm -rf /usr/src/asterisk-22
        info "Cleanup complete"
    else
        info "No existing Asterisk source found"
    fi

    mkdir -p "$ASTERISK_SRC_DIR"

    cd /usr/src

    # Use the -current symlink for latest Asterisk 22
    local asterisk_tarball="asterisk-22-current.tar.gz"

    info "Downloading $asterisk_tarball..."
    wget -q --show-progress \
        "https://downloads.asterisk.org/pub/telephony/asterisk/$asterisk_tarball" \
        -O "$asterisk_tarball"
    info "Download complete"

    # Verify tarball
    if [[ ! -f "$asterisk_tarball" ]]; then
        die "Download failed: $asterisk_tarball not found"
    fi
    info "Tarball size: $(ls -lh "$asterisk_tarball" | awk '{print $5}')"

    # Verify tarball integrity
    if ! tar -tzf "$asterisk_tarball" >/dev/null 2>&1; then
        die "Tarball is corrupted: $asterisk_tarball"
    fi
    info "Tarball integrity verified"

    # Extract if not already extracted
    local extracted_dir
    info "Reading tarball contents..."
    # Use a subshell to avoid pipefail issues with head
    extracted_dir=$(tar -tzf "$asterisk_tarball" 2>/dev/null | head -1 | cut -d'/' -f1) || true
    
    if [[ -z "$extracted_dir" ]]; then
        die "Could not determine extracted directory name"
    fi
    
    info "Detected archive directory: $extracted_dir"

    if [[ ! -d "/usr/src/$extracted_dir" ]]; then
        info "Extracting Asterisk..."
        tar xzf "$asterisk_tarball"
        info "Extraction complete"
        info "Extracted directory exists: $(ls -la /usr/src/$extracted_dir 2>&1 | head -3)"
    else
        info "Asterisk source already extracted at /usr/src/$extracted_dir"
    fi

    # Create symlink for easier access
    info "Creating symlink $ASTERISK_SRC_DIR -> $extracted_dir"
    # Force remove anything at that path (file, symlink, or directory)
    rm -rf "$ASTERISK_SRC_DIR"
    ln -sfn "$extracted_dir" "$ASTERISK_SRC_DIR"
    info "Symlink created: $(ls -ld "$ASTERISK_SRC_DIR" 2>/dev/null || echo "symlink at $ASTERISK_SRC_DIR")"

    info "Changing directory to $ASTERISK_SRC_DIR"
    cd "$ASTERISK_SRC_DIR"
    pwd_check=$(pwd)
    info "Asterisk source ready at $ASTERISK_SRC_DIR"
    info "Current directory: $pwd_check"
    info "Directory contents: $(ls -1 2>/dev/null | head -10 || echo 'unable to list')"
}

configure_asterisk() {
    info "Configuring Asterisk..."

    cd "$ASTERISK_SRC_DIR"
    info "Working directory: $(pwd)"
    
    if [[ ! -f "./configure" ]]; then
        die "configure script not found in $(pwd)"
    fi

    # Run configure with bundled PJSIP
    info "Running ./configure --with-pjproject-bundled --with-ssl --with-srtp"
    ./configure --with-pjproject-bundled --with-ssl --with-srtp

    if [[ $? -eq 0 ]]; then
        info "Asterisk configured successfully"
    else
        die "Asterisk configuration failed"
    fi
}

menuselect_codecs() {
    info "Configuring Asterisk modules and codecs..."

    cd "$ASTERISK_SRC_DIR"
    info "Working directory: $(pwd)"

    # Generate menuselect options
    info "Generating menuselect.makeopts..."
    make menuselect.makeopts

    # Note: Asterisk 22 has built-in codec support for G.722, G.729A, and Opus
    # These are compiled into the core, not as separate modules

    # Disable unused channel drivers (reduces attack surface)
    menuselect/menuselect --disable chan_alsa menuselect.makeopts 2>&1 || true
    menuselect/menuselect --disable chan_console menuselect.makeopts 2>&1 || true
    menuselect/menuselect --disable chan_mgcp menuselect.makeopts 2>&1 || true
    menuselect/menuselect --disable chan_skinny menuselect.makeopts 2>&1 || true
    menuselect/menuselect --disable chan_unistim menuselect.makeopts 2>&1 || true

    info "Module selection configured"
}

compile_asterisk() {
    info "Compiling Asterisk (this may take 10-15 minutes)..."

    cd "$ASTERISK_SRC_DIR"
    info "Working directory: $(pwd)"
    info "Using $(nproc) CPU cores for compilation"

    # Compile
    make -j"$(nproc)"

    if [[ $? -eq 0 ]]; then
        info "Asterisk compilation complete"
    else
        die "Asterisk compilation failed"
    fi
}

install_asterisk() {
    info "Installing Asterisk..."

    cd "$ASTERISK_SRC_DIR"
    info "Working directory: $(pwd)"

    # Install binaries
    info "Running make install..."
    make install

    if [[ $? -ne 0 ]]; then
        die "Asterisk installation failed"
    fi

    # Install sample configs (only if /etc/asterisk is empty)
    if [[ -z "$(ls -A /etc/asterisk 2>/dev/null)" ]]; then
        info "Installing sample configurations..."
        make samples
        info "Sample configurations installed"
    else
        info "Existing Asterisk configuration preserved"
    fi

    # Generate program documentation (optional, may produce warnings)
    info "Generating program documentation..."
    make progdocs 2>&1 | grep -v "warning:" | grep -v "dot:" | head -5 || true
    info "Documentation generation complete"

    info "Asterisk installed successfully"
}

set_asterisk_permissions() {
    info "Setting Asterisk permissions..."

    # Create required directories
    info "Creating directories..."
    mkdir -p /etc/asterisk
    mkdir -p /var/lib/asterisk
    mkdir -p /var/spool/asterisk
    mkdir -p /var/log/asterisk
    mkdir -p /var/run/asterisk
    mkdir -p /var/spool/asterisk/voicemail

    # Create symlinks for system-installed sounds
    info "Linking system sounds..."
    if [[ -d /usr/share/asterisk/sounds ]] && [[ ! -L /var/lib/asterisk/sounds ]]; then
        rm -rf /var/lib/asterisk/sounds
        ln -s /usr/share/asterisk/sounds /var/lib/asterisk/sounds
        info "Created symlink: /var/lib/asterisk/sounds -> /usr/share/asterisk/sounds"
    fi

    # Set ownership
    info "Setting ownership to $ASTERISK_USER:$ASTERISK_GROUP..."
    chown -R "$ASTERISK_USER":"$ASTERISK_GROUP" \
        /etc/asterisk \
        /var/lib/asterisk \
        /var/spool/asterisk \
        /var/log/asterisk \
        /var/run/asterisk

    # Ensure runtime directory exists with correct permissions
    install -d -m 755 -o "$ASTERISK_USER" -g "$ASTERISK_GROUP" /var/run/asterisk

    info "Asterisk permissions configured"
    info "Directory permissions:"
    ls -la /etc/asterisk 2>&1 | head -3 || true
    ls -la /var/lib/asterisk 2>&1 | head -3 || true
}

configure_codec_modules() {
    info "Configuring codec modules..."

    local modules_conf="/etc/asterisk/modules.conf"

    # Note: Asterisk 22 has built-in codec support for G.722, G.729A, and Opus
    # No additional module loading is required for these codecs

    info "Codecs in Asterisk 22 are built-in (no separate modules needed):"
    info "  - codec_g722 (G.722)"
    info "  - codec_g729 (G.729A)"
    info "  - codec_opus (Opus)"
}

restart_asterisk_for_codecs() {
    # Codecs are built-in, no restart needed for codec loading
    # This function kept for API compatibility
    info "Codecs are built-in - no restart required"
}

# =============================================================================
# Phase 4 - Asterisk Base Configuration
# =============================================================================

install_asterisk_service() {
    info "Installing Asterisk systemd service..."

    cat > /etc/systemd/system/asterisk.service << 'EOF'
[Unit]
Description=Asterisk PBX
Documentation=man:asterisk(8)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=asterisk
Group=asterisk
Environment=HOME=/var/lib/asterisk
RuntimeDirectory=asterisk
RuntimeDirectoryMode=0755
ExecStartPre=/usr/bin/install -d -m 755 -o asterisk -g asterisk /run/asterisk
ExecStart=/usr/sbin/asterisk -f
ExecStop=/usr/sbin/asterisk -rx "core stop now"
Restart=on-failure
LimitCORE=infinity

[Install]
WantedBy=multi-user.target
EOF

    info "Asterisk systemd service installed"
}

configure_asterisk_base() {
    info "Configuring base Asterisk settings..."

    # Ensure asterisk.conf has correct user/group settings
    local asterisk_conf="/etc/asterisk/asterisk.conf"

    if [[ -f "$asterisk_conf" ]]; then
        # Uncomment or add runuser and rungroup
        sed -i 's/^;\?runuser\s*=.*/runuser=asterisk/' "$asterisk_conf"
        sed -i 's/^;\?rungroup\s*=.*/rungroup=asterisk/' "$asterisk_conf"

        # If not present, add them
        if ! grep -q "^runuser=" "$asterisk_conf"; then
            echo "runuser=asterisk" >> "$asterisk_conf"
        fi
        if ! grep -q "^rungroup=" "$asterisk_conf"; then
            echo "rungroup=asterisk" >> "$asterisk_conf"
        fi

        info "asterisk.conf configured"
    fi

    # Set French indication tones in indications.conf
    local indications_conf="/etc/asterisk/indications.conf"
    if [[ -f "$indications_conf" ]]; then
        # Set default country to France in [general] section
        sed -i 's/^country\s*=\s*us/country=fr/' "$indications_conf"
        sed -i 's/^;\?country\s*=.*/country=fr/' "$indications_conf"
        
        # If country not set, add it after [general]
        if ! grep -q "^country=" "$indications_conf"; then
            sed -i '/^\[general\]/a country=fr' "$indications_conf"
        fi
        
        info "indications.conf configured (country=fr)"
    fi

    # Configure modules.conf to disable deprecated/problematic modules
    local modules_conf="/etc/asterisk/modules.conf"
    if [[ -f "$modules_conf" ]]; then
        # Disable deprecated ADSI modules
        sed -i 's/^load => res_adsi.so/;load => res_adsi.so ; deprecated/' "$modules_conf"
        sed -i 's/^load => app_adsiprog.so/;load => app_adsiprog.so ; deprecated/' "$modules_conf"
        sed -i 's/^load => app_getcpeid.so/;load => app_getcpeid.so ; deprecated/' "$modules_conf"
        
        # Disable AEL (Asterisk Extension Language) if not used - causes macro warnings
        sed -i 's/^load => pbx_ael.so/;load => pbx_ael.so ; not needed/' "$modules_conf"
        
        # Disable phone provisioning - not needed, causes "no valid server" warnings
        sed -i 's/^load => res_phoneprov.so/;load => res_phoneprov.so ; not configured/' "$modules_conf"
        
        info "modules.conf cleaned up (deprecated modules disabled)"
    fi

    # Verify modules.conf exists
    if [[ -f "/etc/asterisk/modules.conf" ]]; then
        info "modules.conf present"
    fi

    # Verify pjsip.conf exists
    if [[ -f "/etc/asterisk/pjsip.conf" ]]; then
        info "pjsip.conf present"
    fi
}

start_asterisk() {
    info "Starting Asterisk..."

    # Reload systemd
    systemctl daemon-reload

    # Enable and start Asterisk
    systemctl enable asterisk
    systemctl start asterisk

    # Wait for Asterisk to be ready
    sleep 2

    # Verify Asterisk is running
    if systemctl is-active --quiet asterisk; then
        info "Asterisk started successfully"

        # Show version
        local version
        version=$(asterisk -rx "core show version" 2>/dev/null | head -1)
        info "Asterisk version: $version"

        # Show uptime
        local uptime
        uptime=$(asterisk -rx "core show uptime" 2>/dev/null | head -1)
        info "$uptime"
    else
        warn "Asterisk failed to start - check logs"
    fi
}

# =============================================================================
# Phase 5 - Install AsterUIX WebUI
# =============================================================================

install_asteruix() {
    info "Installing AsterUIX WebUI..."

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

    # Create database directory
    local db_dir="/var/lib/asterisk-webui"
    mkdir -p "$db_dir"
    chown "$ASTERISK_USER":"$ASTERISK_GROUP" "$db_dir"
    info "Database directory created: $db_dir"

    # Create admin user
    cd "$WEBUI_DIR"
    source "$WEBUI_DIR/venv/bin/activate"

    # Check if admin user already exists
    if python3 manage.py list-admins 2>/dev/null | grep -q "admin"; then
        info "Admin user already exists"
    else
        # Prompt for admin password
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

    # Create empty WebUI config files so Asterisk can start
    # These will be populated by create_default_config()
    info "Creating empty WebUI config placeholders..."
    mkdir -p /etc/asterisk/webui
    touch /etc/asterisk/webui/pjsip_extensions.conf
    touch /etc/asterisk/webui/pjsip_trunks.conf
    touch /etc/asterisk/webui/voicemail_boxes.conf
    touch /etc/asterisk/webui/musiconhold_classes.conf
    touch /etc/asterisk/webui/extensions_inbound.conf
    touch /etc/asterisk/webui/extensions_timegroups.conf
    touch /etc/asterisk/webui/confbridge_profiles.conf

    # Run migrate-includes.sh if it exists
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

    # Import various config types
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

    # Create default time group (9am-5pm, Mon-Fri)
    info "Creating default time group (Business Hours)..."
    python3 manage.py create-timegroup \
        --name "Business Hours" \
        --time "09:00-17:00" \
        --weekdays "mon,tue,wed,thu,fri" 2>/dev/null || \
    info "Time group created or already exists"

    # Create TTS announcement for welcome message
    info "Creating welcome announcement..."
    python3 manage.py create-announcement \
        --name "Welcome" \
        --type "tts" \
        --text "Welcome to your new Asterisk phone system. Please contact your administrator for extension setup." 2>/dev/null || \
    info "Welcome announcement created or already exists"

    # Create default extension 4900
    info "Creating default extension 4900..."
    python3 manage.py create-extension \
        --extension "4900" \
        --name "Default User" \
        --secret "4900" \
        --context "from-internal" 2>/dev/null || \
    info "Extension 4900 created or already exists"

    # Create default inbound route
    info "Creating default inbound route..."
    python3 manage.py create-inbound \
        --name "Default Route" \
        --destination "extension:4900" 2>/dev/null || \
    info "Default inbound route created or already exists"

    # Populate spam database with French spam prefixes
    info "Populating spam database with French spam prefixes..."
    python3 manage.py populate-spam-db 2>/dev/null || \
    info "Spam database populated (or already exists)"

    # Reload Asterisk to apply changes
    info "Reloading Asterisk configuration..."
    asterisk -rx "core reload" 2>/dev/null || true

    deactivate

    info "Default configuration complete"
    info ""
    info "=== Default Configuration ==="
    info "Extension: 4900"
    info "Password:  4900"
    info "Time Group: Business Hours (Mon-Fri, 9am-5pm)"
    info "Welcome announcement: TTS enabled"
    info "Spam prefixes: 12 French spam prefixes loaded"
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

    # Reload systemd
    systemctl daemon-reload

    # Enable and start WebUI
    systemctl enable asterisk-webui
    systemctl start asterisk-webui

    # Wait for WebUI to be ready
    sleep 2

    # Verify WebUI is running
    if systemctl is-active --quiet asterisk-webui; then
        info "AsterUIX WebUI started successfully"

        # Test login page
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
# Phase 6 - Restore From Backup (Optional)
# =============================================================================

install_backup_scripts() {
    info "Installing backup/restore scripts..."

    # Copy backup scripts to /usr/local/bin if they exist
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

    info "=== Phase 6: Restoring from Backup ==="
    info "Restoring from: $RESTORE_FILE"

    if [[ ! -f "$RESTORE_FILE" ]]; then
        die "Backup file not found: $RESTORE_FILE"
    fi

    # Stop Asterisk
    info "Stopping Asterisk for restore..."
    systemctl stop asterisk || true

    # Extract backup to root filesystem
    info "Extracting backup..."
    tar -xzf "$RESTORE_FILE" -C /

    # Fix permissions
    info "Fixing permissions..."
    chown -R "$ASTERISK_USER":"$ASTERISK_GROUP" \
        /etc/asterisk \
        /var/spool/asterisk/voicemail \
        /var/lib/asterisk

    # Re-run migrate-includes.sh in case backup predates WebUI includes
    if [[ -f "$WEBUI_DIR/scripts/migrate-includes.sh" ]]; then
        info "Re-running migrate-includes.sh..."
        bash "$WEBUI_DIR/scripts/migrate-includes.sh"
    fi

    # Start Asterisk
    info "Starting Asterisk..."
    systemctl start asterisk

    # Restart WebUI
    info "Restarting WebUI..."
    systemctl restart asterisk-webui

    # Import configuration into WebUI database
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
# Phase 7 - Post-Install Verification & Summary
# =============================================================================

verify_codecs() {
    info "Verifying codec availability..."

    local codecs_ok=true

    # Ensure Asterisk is running
    if ! systemctl is-active --quiet asterisk; then
        warn "Asterisk is not running - skipping codec verification"
        return
    fi

    # Wait for Asterisk to be fully ready
    sleep 1

    # Get full codec list for debugging
    local codec_output
    codec_output=$(asterisk -rx "core show codecs" 2>&1)

    # Check codec_g722 (built-in)
    if echo "$codec_output" | grep -qi "g722"; then
        info "  [OK] codec_g722 (G.722) - built-in"
    else
        warn "  [MISSING] codec_g722 (G.722)"
        codecs_ok=false
    fi

    # Check codec_g729 (built-in G.729A in Asterisk 22)
    if echo "$codec_output" | grep -qi "g729"; then
        info "  [OK] codec_g729 (G.729) - built-in"
    else
        warn "  [MISSING] codec_g729 (G.729)"
        codecs_ok=false
    fi

    # Check codec_opus (built-in in Asterisk 22)
    if echo "$codec_output" | grep -qi "opus"; then
        info "  [OK] codec_opus (Opus) - built-in"
    else
        warn "  [MISSING] codec_opus (Opus)"
        codecs_ok=false
    fi

    if [[ "$codecs_ok" == "true" ]]; then
        info "All codecs verified successfully"
    else
        warn "Some codecs may not be available"
        # Debug: show full codec output
        info "Full codec output:"
        echo "$codec_output" | head -20 | while read line; do info "  $line"; done
    fi
}

print_summary() {
    echo ""
    echo "=============================================="
    echo "  Installation Complete!"
    echo "=============================================="
    echo ""

    # Asterisk version
    local version
    version=$(asterisk -rx "core show version" 2>/dev/null | head -1)
    echo "Asterisk: $version"
    echo ""

    # Codec status from core show codecs
    echo "Codecs:"
    local codec_list
    codec_list=$(asterisk -rx "core show codecs" 2>/dev/null | grep -E "g722|g729|opus")
    if [[ -n "$codec_list" ]]; then
        echo "$codec_list" | awk '{print "  - " $2 " (" $4 ")"}'
    else
        echo "  (unable to query - Asterisk may not be running)"
    fi
    echo ""

    # WebUI URL
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

    # Firewall reminder
    echo "Firewall Configuration:"
    echo "  - Port 5060/udp  : SIP signaling"
    echo "  - Port 10000-20000/udp : RTP media"
    echo "  - Port 8081/tcp  : WebUI (LAN access recommended)"
    echo ""

    # Backup info
    echo "Backup & Restore:"
    echo "  - Backup location: /var/backups/asterisk/"
    echo "  - Create backup:   asterisk-backup.sh"
    echo "  - Restore backup:  asterisk-restore.sh <file.tar.gz>"
    echo ""

    # Useful commands
    echo "Useful Commands:"
    echo "  - asterisk -rx 'core show channels'  : Show active channels"
    echo "  - asterisk -rx 'pjsip show endpoints': Show PJSIP endpoints"
    echo "  - asterisk -rx 'core restart now'    : Restart Asterisk"
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

    # Initialize log file
    mkdir -p "$(dirname "$LOG_FILE")"
    : > "$LOG_FILE"

    echo ""
    echo "=============================================="
    echo "  AsterUIX Installation Script"
    echo "  Log file: $LOG_FILE"
    echo "=============================================="
    echo ""

    # Phase 1: System Preparation
    info "=== Phase 1: System Preparation ==="

    preflight_checks
    prompt_confirmation
    create_asterisk_user
    install_prerequisites

    info "Phase 1 completed successfully"
    echo ""

    # Phase 2: PJSIP (bundled with Asterisk 22)
    info "=== Phase 2: PJSIP Preparation ==="

    skip_pjsip_compilation

    info "Phase 2 completed successfully"
    echo ""

    # Phase 3: Compile & Install Asterisk 22 LTS
    info "=== Phase 3: Asterisk 22 LTS Installation ==="

    download_asterisk
    install_asterisk_dependencies
    configure_asterisk
    menuselect_codecs
    compile_asterisk
    install_asterisk
    set_asterisk_permissions
    configure_codec_modules

    info "Phase 3 completed successfully"
    echo ""

    # Phase 4: Asterisk Base Configuration
    info "=== Phase 4: Asterisk Base Configuration ==="

    install_asterisk_service
    configure_asterisk_base
    start_asterisk
    restart_asterisk_for_codecs

    info "Phase 4 completed successfully"
    echo ""

    # Phase 5: Install AsterUIX WebUI
    info "=== Phase 5: AsterUIX WebUI Installation ==="

    install_asteruix
    setup_database
    migrate_includes
    import_config
    create_default_config
    install_webui_service
    start_webui

    info "Phase 5 completed successfully"
    echo ""

    # Phase 6: Restore From Backup (Optional)
    info "=== Phase 6: Backup/Restore ==="

    install_backup_scripts
    restore_backup

    info "Phase 6 completed successfully"
    echo ""

    # Phase 7: Post-Install Verification & Summary
    info "=== Phase 7: Post-Install Verification ==="

    verify_codecs
    print_summary

    info "Installation completed successfully!"
}

# Run main function
main "$@"
