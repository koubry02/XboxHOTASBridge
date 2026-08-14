#!/bin/bash
# build_libx52.sh — build & install libx52 (X52 Pro MFD/LED control) from source.
#
# Use this on Armbian / Debian / arm64, where the Ubuntu PPA has no package.
# It installs the build dependencies, clones libx52, builds it with meson,
# installs it, sets up the udev rule so a normal user can talk to the MFD,
# and verifies the `x52cli` binary works.
#
#   sudo ./build_libx52.sh
#
# Safe to re-run. Does NOT touch your apt sources.
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo"; exit 1
fi

echo "=== libx52 source build (Armbian/arm64-friendly) ==="

echo "--- Installing build dependencies (all standard Debian packages) ---"
apt-get update -qq
apt-get install -y -qq \
    git meson ninja-build pkg-config gcc make \
    libusb-1.0-0-dev libhidapi-dev libudev-dev libevdev-dev libinih-dev \
    automake autoconf gettext autopoint libtool \
    python3 || {
        echo "ERROR: could not install build deps. Check your apt is working."
        exit 1
    }

SRC=/opt/hotas-bridge/.build/libx52
mkdir -p "$(dirname "$SRC")"

if [ -d "$SRC/.git" ]; then
    echo "--- Updating existing libx52 checkout ---"
    git -C "$SRC" pull --ff-only || true
else
    echo "--- Cloning libx52 ---"
    rm -rf "$SRC"
    git clone --depth 1 https://github.com/nirenjan/libx52.git "$SRC"
fi

cd "$SRC"

echo "--- Configuring & building (meson/ninja) ---"
# Fresh build dir each time to avoid stale-config issues
rm -rf builddir
meson setup builddir --prefix=/usr/local
ninja -C builddir
echo "--- Installing ---"
ninja -C builddir install
ldconfig

# udev rule so the MFD is writable without sudo (libx52 ships one; make sure
# it's active). The Saitek/MadCatz vendor id is 06a3.
UDEV=/etc/udev/rules.d/99-x52pro.rules
if [ ! -f "$UDEV" ]; then
    echo "--- Installing udev rule ($UDEV) ---"
    cat > "$UDEV" <<'RULE'
# Saitek/MadCatz X52 Pro — allow MFD/LED access for local users
SUBSYSTEM=="usb", ATTRS{idVendor}=="06a3", MODE="0666"
RULE
    udevadm control --reload-rules || true
    udevadm trigger || true
fi

echo ""
echo "=== Verifying ==="
if command -v x52cli >/dev/null 2>&1; then
    echo "  x52cli installed at: $(command -v x52cli)"
    echo "  Testing a write to the MFD (line 0)..."
    x52cli mfd 0 "HOTAS BRIDGE" 2>/dev/null \
        && echo "  OK — check your throttle screen for 'HOTAS BRIDGE'." \
        || echo "  (write returned non-zero; if the screen is blank, unplug/replug the X52 to apply the udev rule, then retry)"
else
    echo "  WARNING: x52cli not on PATH after install."
    echo "  It may be at /usr/local/bin — check: ls -l /usr/local/bin/x52*"
fi

echo ""
echo "Done. Restart the bridge so it picks up the display:"
echo "  sudo systemctl restart hotas-oberon"
