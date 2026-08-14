#!/bin/bash
# setup_opi_b.sh — run on OPi B (console side), from inside the proxy/ folder.
# Installs build deps, clones usb-proxy at a known-good commit, drops in the
# HOTAS merge script + injection config, and builds.
set -e

PINNED_COMMIT="a08301d"

echo "== Installing dependencies =="
sudo apt update
sudo apt install -y build-essential git pkg-config libjsoncpp-dev liblua5.4-dev python3

# NOTE: do NOT install luajit/libluajit dev packages on this board. The
# usb-proxy Makefile prefers luajit if present, and the merge script needs
# Lua 5.3+ integer/bitwise operators.
if pkg-config --exists luajit 2>/dev/null; then
    echo "!! libluajit-dev is installed and would be picked over lua5.4."
    echo "!! Remove it (sudo apt remove libluajit-*-dev) and re-run."
    exit 1
fi

echo "== Cloning usb-proxy (pinned: $PINNED_COMMIT) =="
if [ ! -d usb-proxy ]; then
    git clone https://github.com/AristoChen/usb-proxy.git
fi
cd usb-proxy
git checkout "$PINNED_COMMIT"

echo "== Installing HOTAS merge files =="
cp ../injection.json .
cp ../scripts/gip_hotas_merge.lua scripts/

echo "== Building =="
make clean || true
make
grep -q "HAVE_LUA" Makefile && ./usb-proxy --help >/dev/null 2>&1 || true
echo
echo "Build done. Confirm the line 'Lua scripting: enabled (lua5.4)' appeared above."
echo "Next: sudo ./run_proxy.sh   (from the proxy/ folder)"
