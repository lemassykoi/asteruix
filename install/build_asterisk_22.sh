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
apt update -qq

echo "===== 1. Installing dependencies ====="
apt install -yqq build-essential git wget subversion libncurses5-dev libssl-dev libxml2-dev libsqlite3-dev uuid-dev libjansson-dev

echo "===== 2. Downloading Asterisk ====="
cd /usr/src
wget -q ${ASTERISK_URL}
tar xvf asterisk-${ASTERISK_VERSION}.tar.gz

cd asterisk-${ASTERISK_MAJOR_VERSION}.*

echo "===== 3. Installing prerequisites ====="
contrib/scripts/install_prereq install

echo "===== 4. Configuring ====="
./configure

echo "===== 5. Compiling ====="
make -j$(nproc)

echo "===== 6. Installing ====="
make install
make samples
make config
ldconfig

echo "===== Setting French tones ====="
sed -i 's/^country=.*/country=fr/' /etc/asterisk/indications.conf

echo "===== 7. Starting Asterisk ====="
systemctl daemon-reexec
systemctl start asterisk
systemctl enable asterisk

echo "===== Done ====="
echo "Connect with: sudo asterisk -rvv"
exit 0
