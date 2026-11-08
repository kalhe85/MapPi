#!/bin/bash

## Download compiled mavlink-router from github releases
# Set the mavlink-router version to download
MAVLINK_ROUTER_VERSION="4"
# Always save to the repository root, wherever this script is run from
cd "$(dirname "$0")"

# Download the appropriate binary based on architecture
if [ "$(uname -m)" = "aarch64" ]; then
    echo "Downloading mavlink-router for ARM64"
    wget --tries=3 -O ../mavlink-routerd \
    "https://github.com/mavlink-router/mavlink-router/releases/download/v${MAVLINK_ROUTER_VERSION}/mavlink-routerd-glibc-aarch64" 
else
    echo "Downloading mavlink-router for AMD64"
    wget --tries=3 -O ../mavlink-routerd \
    "https://github.com/mavlink-router/mavlink-router/releases/download/v${MAVLINK_ROUTER_VERSION}/mavlink-routerd-glibc-x86_64"
fi

# Make the binary executable
chmod +x ../mavlink-routerd
