#!/usr/bin/env bash

set -e

ASTERISK_MAJOR_VERSION="22"
ASTERISK_VERSION="${ASTERISK_MAJOR_VERSION}-current"
ASTERISK_URL="http://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz"

echo "===== Updating system ====="
sudo apt update

echo "===== Installing dependencies ====="
sudo apt install -y build-essential git wget subversion 
libncurses5-dev libssl-dev libxml2-dev libsqlite3-dev 
uuid-dev libjansson-dev

echo "===== Downloading Asterisk ====="
cd /usr/src
sudo wget -q ${ASTERISK_URL}
sudo tar xvf asterisk-${ASTERISK_VERSION}.tar.gz

cd asterisk-${ASTERISK_MAJOR_VERSION}.*

echo "===== Installing prerequisites ====="
sudo contrib/scripts/install_prereq install

echo "===== Configuring ====="
./configure

echo "===== Compiling ====="
make -j$(nproc)

echo "===== Installing ====="
sudo make install
sudo make samples
sudo make config
sudo ldconfig

echo "===== Setting French tones ====="
sudo sed -i 's/^country=.*/country=fr/' /etc/asterisk/indications.conf

echo "===== Starting Asterisk ====="
sudo systemctl daemon-reexec
sudo systemctl start asterisk
sudo systemctl enable asterisk

echo "===== Done ====="
echo "Connect with: sudo asterisk -rvv"
