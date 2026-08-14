#!/bin/bash
# run_proxy.sh — run on OPi B with the Xbox controller plugged into the
# USB-A port and the USB-C cable going to the console.
# Loads raw_gadget, works out the UDC device/driver names for the H618's
# musb controller, and starts usb-proxy locked to Microsoft devices.
set -e
cd "$(dirname "$0")/usb-proxy"

sudo modprobe raw_gadget 2>/dev/null || {
    echo "raw_gadget module missing — see README section 'Kernel requirements'"
    exit 1
}

UDC_DEV=$(ls /sys/class/udc | head -1)
if [ -z "$UDC_DEV" ]; then
    echo "No UDC found. The USB-C port is not in peripheral mode."
    echo "Check the DTB overlay (README section 'USB-C peripheral mode')."
    exit 1
fi
# musb-hdrc.1.auto -> driver musb-hdrc
UDC_DRV=$(echo "$UDC_DEV" | sed 's/\.[0-9]*\.auto$//')

echo "UDC device: $UDC_DEV   driver: $UDC_DRV"
echo "Filtering for Microsoft (vendor 045e) on the host port."
echo

# -v 1 prints before/after bytes on every injected packet; drop it once happy.
exec sudo ./usb-proxy \
    --device "$UDC_DEV" \
    --driver "$UDC_DRV" \
    --vendor_id 045e \
    "$@"
