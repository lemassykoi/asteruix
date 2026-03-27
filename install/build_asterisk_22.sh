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
mv /etc/locale.gen /etc/locale.gen.bak
echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
echo "fr_FR.UTF-8 UTF-8" >> /etc/locale.gen
echo "LC_ALL=fr_FR.UTF-8" >> /etc/default/locale
locale-gen

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
echo "===== 4.2.3 Disable RADIUS ====="
menuselect/menuselect --disable cdr_radius --disable cel_radius menuselect.makeopts
echo "===== 4.2.4 MP3 decoder library ====="
contrib/scripts/get_mp3_source.sh

echo "===== 5. Compiling ====="
make -j$(nproc)

echo "===== 6. Installing ====="
make install
make config  # auto start at boot
make samples  # all .conf.sample files
#make basic-pbx

ldconfig

echo "===== 6.1 Setting ownership ====="
sudo chown -R asterisk /var/run/asterisk
sudo chown -R asterisk /etc/asterisk
sudo chown -R asterisk /var/{lib,log,spool}/asterisk
sudo chown -R asterisk /usr/lib/asterisk

echo "===== 6.2 Configure Asterisk to run as asterisk user ====="
sed -i 's/^;runuser/runuser/' /etc/asterisk/asterisk.conf
sed -i 's/^;rungroup/rungroup/' /etc/asterisk/asterisk.conf
echo "defaultlanguage = fr" >> /etc/asterisk/asterisk.conf

echo "===== 6.3 Setting French tones ====="
file_path = "/etc/asterisk/indications.conf"
sed -i 's/^country = .*/country = fr/' /etc/asterisk/indications.conf
text_to_add = """
[fr]
description = France
; Reference: http://www.itu.int/ITU-T/inr/forms/files/tones-0203.pdf
ringcadence = 1500,3500
; Dialtone can also be 440+330
dial = 440
busy = 440/500,0/500
ring = 440/1500,0/3500
; CONGESTION - not specified
congestion = 440/250,0/250
callwait = 440/300,0/10000
; DIALRECALL - not specified
dialrecall = !350+440/100,!0/100,!350+440/100,!0/100,!350+440/100,!0/100,350+440
; RECORDTONE - not specified
record = 1400/500,0/15000
info = !950/330,!1400/330,!1800/330
stutter = !440/100,!0/100,!440/100,!0/100,!440/100,!0/100,!440/100,!0/100,!440/100,!0/100,!440/100,!0/100,440
"""
# Append the text to the file
with open(file_path, "a") as file:
    file.write("\n" + text_to_add)

echo "===== 7. Starting Asterisk ====="
systemctl daemon-reexec
systemctl start asterisk
systemctl enable asterisk

echo "===== Done ====="
echo "Connect with: sudo asterisk -rvv"
exit 0
