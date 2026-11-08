#!/bin/bash

set -e
set -x

git submodule update --init --recursive

# Increase swap size to 1024MB if it is currently less than 1000MB
# Allows the NPM build to complete successfully on systems with 500MB of RAM
# (e.g. Raspberry Pi Zero 2 W)
SWAP_KB=$(awk 'NR>1 {sum += $3} END {print sum+0}' /proc/swaps)
if [ "$SWAP_KB" -ge 976000 ]; then
    echo "Swap is already >1000MB"
elif command -v dphys-swapfile >/dev/null 2>&1; then
    # Raspberry Pi OS Bookworm and older
    echo "Swap is less than 1000MB. Increasing to 1024MB (dphys-swapfile)."
    sudo dphys-swapfile swapoff
    sudo sed -i '/CONF_SWAPSIZE=.*/c\CONF_SWAPSIZE=1024' /etc/dphys-swapfile
    sudo dphys-swapfile setup
    sudo dphys-swapfile swapon
elif [ -d /etc/rpi ] && dpkg -s rpi-swap >/dev/null 2>&1; then
    # Raspberry Pi OS Trixie and newer: swap is managed by rpi-swap
    # Applied at the next boot (this script reboots at the end)
    echo "Swap is less than 1000MB. Setting a 1024MB swap file (rpi-swap)."
    sudo mkdir -p /etc/rpi/swap.conf.d
    printf '[Main]\nMechanism=swapfile\n\n[File]\nFixedSizeMiB=1024\n' | sudo tee /etc/rpi/swap.conf.d/80-mappi.conf >/dev/null
else
    echo "Swap is less than 1000MB. Creating a 1024MB /swapfile."
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

## Pi5 uses a different UART for the 40-pin header (/dev/ttyAMA0)
# See https://forums.raspberrypi.com/viewtopic.php?t=359132
if [ -e "/proc/device-tree/compatible" ]; then
    if grep -q "5-model-bbrcm" "/proc/device-tree/compatible"; then
        echo "dtparam=uart0=on" | sudo tee -a /boot/firmware/config.txt >/dev/null
    else
        # Enable serial, disable console
        sudo raspi-config nonint do_serial 2
    fi
fi

./install_common_libraries.sh

# Also need gstreamer1.0-libcamera, as the libcamerasrc gst element has moved in bookworm
source /etc/os-release
if [[ "$ID" == "debian" || "$ID" == "raspbian" ]] && [ "$VERSION_CODENAME" == "bookworm" ]; then
    sudo apt install -y  gstreamer1.0-libcamera
fi

## Only install picamera2 on RaspiOS
sudo apt -y install python3-picamera2 python3-libcamera python3-kms++

sudo systemctl disable dnsmasq
sudo systemctl enable NetworkManager

## Configure nmcli to not need sudo
sudo sed -i.bak -e '/^\[main\]/aauth-polkit=false' /etc/NetworkManager/NetworkManager.conf

## For wireguard. Must be installed last as it messes the DNS resolutions
sudo apt install -y resolvconf

sudo reboot
