#!/bin/bash
# link_setup.sh — dedicated point-to-point WiFi link between the two boards.
#
#   On OPi B (console side):   sudo ./link_setup.sh ap
#   On OPi A (HOTAS side):     sudo ./link_setup.sh client
#
# Uses NetworkManager (default on Armbian). OPi B becomes an access point at
# 10.42.0.1; OPi A joins it. Both disable WiFi power save, which is the
# single biggest latency killer (50-100 ms spikes if left on).

SSID="HOTASLINK"
PSK="hotasbridge1"   # change it, then change it in both invocations

set -e
MODE="$1"

case "$MODE" in
  ap)
    nmcli connection delete hotas-ap 2>/dev/null || true
    nmcli connection add type wifi ifname wlan0 con-name hotas-ap autoconnect yes \
        ssid "$SSID" mode ap ipv4.method shared wifi.band bg
    nmcli connection modify hotas-ap wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK"
    # Disable power save (3 = off) and bring it up
    nmcli connection modify hotas-ap wifi.powersave 2
    nmcli connection up hotas-ap
    iw dev wlan0 set power_save off || true
    echo "AP '$SSID' up. This board is 10.42.0.1"
    ;;
  client)
    nmcli connection delete hotas-link 2>/dev/null || true
    nmcli device wifi rescan || true
    sleep 2
    nmcli device wifi connect "$SSID" password "$PSK" name hotas-link
    nmcli connection modify hotas-link wifi.powersave 2 connection.autoconnect yes
    iw dev wlan0 set power_save off || true
    echo "Joined '$SSID'. Receiver is at 10.42.0.1"
    echo "Test:  ping -c 20 -i 0.05 10.42.0.1   (expect ~2-5 ms, no spikes)"
    ;;
  *)
    echo "usage: sudo $0 ap|client"
    exit 1
    ;;
esac
