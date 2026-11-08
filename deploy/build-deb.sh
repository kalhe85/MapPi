#!/bin/bash
# Build the .deb package on this machine (e.g. the Raspberry Pi itself).
# The release workflow sets the package architecture in package.json;
# this script does the same for local builds, then restores package.json.
#
# Usage (from anywhere): ./deploy/build-deb.sh

set -e
cd "$(dirname "$0")/.."

ARCH=$(dpkg --print-architecture)
echo "Building package for architecture: $ARCH"

if [ ! -f ./mavlink-routerd ] && ! command -v mavlink-routerd >/dev/null 2>&1; then
    echo "WARNING: mavlink-routerd not found (run deploy/devExtras.sh or build it from source)."
fi

cp package.json package.json.bak
trap 'mv -f package.json.bak package.json' EXIT
jq --arg arch "$ARCH" '.node_deb.architecture = $arch' package.json.bak > package.json

rm -f rpanion-server_*.deb
npm run package

echo
echo "Built: $(ls rpanion-server_*_"$ARCH".deb)"
echo "Install with: sudo dpkg -i $(ls rpanion-server_*_"$ARCH".deb)"
