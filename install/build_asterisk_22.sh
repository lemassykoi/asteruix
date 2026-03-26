#!/usr/bin/env bash

set -e

ASTERISK_MAJOR_VERSION="22"
ASTERISK_VERSION="${ASTERISK_MAJOR_VERSION}-current"
ASTERISK_URL="http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root" >&2
    echo "Usage: sudo $0 [options]" >&2
    exit 1
fi

echo "===== 0. Updating system ====="
echo "locales locales/locales_to_be_generated multiselect fr_FR.UTF-8 UTF-8" | debconf-set-selections
echo "locales locales/locales_to_be_generated multiselect en_US.UTF-8 UTF-8" | debconf-set-selections
echo "locales locales/default_environment_locale select fr_FR.UTF-8" | debconf-set-selections
rm "/etc/locale.gen"
dpkg-reconfigure --frontend noninteractive locales

apt update -qq

echo "===== 1. Installing dependencies ====="
apt install -yqq build-essential git curl wget subversion libncurses5-dev libssl-dev libxml2-dev libsqlite3-dev uuid-dev libjansson-dev libmpg123-dev

echo "===== 1.1 Creating asterisk user ====="
if ! id "asterisk" &>/dev/null; then
sudo adduser --system --group --home /var/lib/asterisk asterisk
fi

echo "===== 2. Downloading Asterisk ====="
cd /usr/src
wget -q ${ASTERISK_URL}
tar xvf asterisk-${ASTERISK_VERSION}.tar.gz

cd asterisk-${ASTERISK_MAJOR_VERSION}.*

echo "===== 3. Installing prerequisites ====="
contrib/scripts/install_prereq install

echo "===== 4. Configuring ====="
./configure

echo "===== 4.1 make menuselect ====="
make menuselect.makeopts

echo "===== 4.2 Configuring menuselect (non-interactive) ====="
echo "===== 4.2.1 Modules ====="
menuselect/menuselect --enable format_mp3 --enable codec_opus --enable codec_g729a --enable jukebox.agi menuselect.makeopts
echo "===== 4.2.2 Sounds ====="
menuselect/menuselect --enable CORE-SOUNDS-FR-G722 --enable CORE-SOUNDS-FR-G729 --enable EXTRA-SOUNDS-FR-G722 --enable EXTRA-SOUNDS-FR-G729 --enable MOH-OPSOUND-G722 --enable MOH-OPSOUND-G729 menuselect.makeopts
echo "===== 4.2.3 MP3 decoder library ====="
contrib/scripts/get_mp3_source.sh

echo "===== 5. Compiling ====="
make -j$(nproc)

echo "===== 6. Installing ====="
make install
make samples
make config
ldconfig

echo "===== 6.1 Setting ownership ====="
sudo chown -R asterisk /var/run/asterisk
sudo chown -R asterisk /etc/asterisk
sudo chown -R asterisk /var/{lib,log,spool}/asterisk
sudo chown -R asterisk /usr/lib/asterisk

echo "===== 6.2 Configure Asterisk to run as asterisk user ====="
sudo sed -i 's/^;runuser./runuser = asterisk/' /etc/asterisk/asterisk.conf
sudo sed -i 's/^;rungroup./rungroup = asterisk/' /etc/asterisk/asterisk.conf

echo "===== 6.3 Setting French tones ====="
sed -i 's/^country=.*/country=fr/' /etc/asterisk/indications.conf

echo "===== 7. Starting Asterisk ====="
systemctl daemon-reexec
systemctl start asterisk
systemctl enable asterisk

echo "===== Done ====="
echo "Connect with: sudo asterisk -rvv"
exit 0
