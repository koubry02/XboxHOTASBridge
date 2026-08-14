#!/usr/bin/env python3
"""
oberon_server.py — single-Pi Oberon Remote server for the Saitek X52.

Reads the X52 via evdev and serves controller state on port 26401 in the
Oberon Remote protocol. The official Oberon Remote app on the Xbox connects
here and injects the input as a virtual controller. No USB proxy, no second
board, no auth handshake.

Install Oberon Remote on your Xbox first:
  Microsoft Store -> search "Oberon Remote Input" (developer: SamsidParty)
  Direct link: https://apps.microsoft.com/detail/9pk5stjzff3s

Protocol (reverse-engineered from OberonRemote open-source):
  On connect  server -> client: [0x0A] + hostname bytes   (handshake)
  Poll        client -> server: [0xFA] (optionally + 16 rumble bytes, ignored)
  Response    server -> client: 100-byte controller state buffer

Buffer layout (4 controller slots x 25 bytes each):
  Slot 0 (our X52), slots 1-3 zeroed (no controllers):
    byte  0:    0xFF = connected
    bytes 1-2:  LX  int16 LE  floor(lx  *  32767)
    bytes 3-4:  LY  int16 LE  floor(ly  * -32767)   <- Oberon inverts Y
    bytes 5-6:  RX  int16 LE  floor(rx  *  32767)
    bytes 7-8:  RY  int16 LE  floor(ry  * -32767)   <- Oberon inverts Y
    bytes 9-10: LT  uint16 LE floor((lt+1)/2 * 32767)
    bytes 11-12:RT  uint16 LE floor((rt+1)/2 * 32767)
    byte 13:    button group 1: A B X Y LB RB Menu View   (bit7..bit0)
    byte 14:    button group 2: - LS RS Up Dn Lt Rt Guide  (bit7..bit0)
    bytes 15-24: zero

Usage:
    sudo python3 oberon_server.py                    # uses default config
    sudo python3 oberon_server.py --config /path/to/sender_config.json
    sudo python3 oberon_server.py --list             # show input devices
    sudo python3 oberon_server.py --probe            # live event names
    sudo python3 oberon_server.py --verbose          # print state on each poll
"""

import argparse
import asyncio
import json
import math
import os
import socket
import sys
import threading
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python3-evdev missing.  Run: sudo apt install python3-evdev")

try:
    import websockets
    from websockets.server import serve as ws_serve
except ImportError:
    sys.exit("websockets missing.  Run: pip3 install --break-system-packages websockets")

PORT          = 26401
AXIS_TARGETS  = ("lx", "ly", "rx", "ry", "lt", "rt")

# ─── Oberon button bit positions ──────────────────────────────────────────────
# (group, mask)  group 1 = byte 13, group 2 = byte 14 of the slot
OBERON_BTNS = {
    "a":          (1, 0x80),
    "b":          (1, 0x40),
    "x":          (1, 0x20),
    "y":          (1, 0x10),
    "lb":         (1, 0x08),
    "rb":         (1, 0x04),
    "menu":       (1, 0x02),
    "view":       (1, 0x01),
    "ls":         (2, 0x40),
    "rs":         (2, 0x20),
    "dpad_up":    (2, 0x10),
    "dpad_down":  (2, 0x08),
    "dpad_left":  (2, 0x04),
    "dpad_right": (2, 0x02),
}

# Digital buttons that drive a trigger to full deflection
TRIGGER_BTNS = {"lt_button": "lt", "rt_button": "rt"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def code_name(etype, code):
    name = ecodes.bytype.get(etype, {}).get(code)
    if isinstance(name, list):
        name = name[0]
    return name or f"{etype}:{code}"


def find_device(match):
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        if match.lower() in dev.name.lower() and ecodes.EV_ABS in dev.capabilities():
            return dev
        dev.close()
    return None


def shape(raw, absinfo, cfg):
    """Normalise a raw evdev value to -1..1 with deadzone + expo."""
    lo, hi = absinfo.min, absinfo.max
    v = (raw - lo) / ((hi - lo) or 1) * 2.0 - 1.0
    if cfg.get("invert"):
        v = -v
    dz = cfg.get("deadzone", 0.0)
    if abs(v) < dz:
        return 0.0
    v = math.copysign((abs(v) - dz) / (1.0 - dz), v)
    expo = cfg.get("expo", 0.0)
    if expo > 0:
        v = (1 - expo) * v + expo * (v ** 3)
    return max(-1.0, min(1.0, v))


def build_packet(axes, g1, g2):
    """Build a 100-byte Oberon state buffer from normalised axis values."""
    buf = bytearray(100)

    def s16(v):
        # floor + two's-complement uint16 LE
        vi = int(math.floor(v)) & 0xFFFF
        buf[pos]     = vi & 0xFF
        buf[pos + 1] = (vi >> 8) & 0xFF

    def u16(v):
        vi = max(0, min(32767, int(math.floor(v))))
        buf[pos]     = vi & 0xFF
        buf[pos + 1] = (vi >> 8) & 0xFF

    # Slot 0: our controller
    buf[0] = 0xFF
    pos = 1;  s16(axes["lx"] * 32767)
    pos = 3;  s16(axes["ly"] * -32767)   # Oberon protocol inverts Y
    pos = 5;  s16(axes["rx"] * 32767)
    pos = 7;  s16(axes["ry"] * -32767)
    pos = 9;  u16((axes["lt"] + 1.0) / 2.0 * 32767)
    pos = 11; u16((axes["rt"] + 1.0) / 2.0 * 32767)
    buf[13] = g1
    buf[14] = g2
    # Slots 1-3 remain zero (not connected)

    return bytes(buf)


# ─── Thread-safe HOTAS state ─────────────────────────────────────────────────

class HOTASState:
    def __init__(self):
        self._lock = threading.Lock()
        self._axes = {t: 0.0 for t in AXIS_TARGETS}
        self._g1 = 0
        self._g2 = 0

    def update(self, axes, g1, g2):
        with self._lock:
            self._axes = dict(axes)
            self._g1   = g1
            self._g2   = g2

    def snapshot(self):
        with self._lock:
            return dict(self._axes), self._g1, self._g2


# ─── evdev reader (runs in daemon thread) ────────────────────────────────────

def evdev_reader(dev, axis_cfg, mode_sel, button_cfg, trigger_btn_cfg,
                 hat_dpad, state, suspend_code=None, start_suspended=False):
    axes    = {t: 0.0 for t in AXIS_TARGETS}
    pressed = set()
    hat     = [0, 0]
    mode    = 1
    suspended = start_suspended   # when True, all axes report neutral (menu nav)

    while True:
        try:
            for ev in dev.read_loop():
                if ev.type == ecodes.EV_ABS:
                    if ev.code in axis_cfg:
                        tgt, acfg, info = axis_cfg[ev.code]
                        axes[tgt] = shape(ev.value, info, acfg)
                    elif hat_dpad and ev.code == ecodes.ABS_HAT0X:
                        hat[0] = ev.value
                    elif hat_dpad and ev.code == ecodes.ABS_HAT0Y:
                        hat[1] = ev.value

                elif ev.type == ecodes.EV_KEY:
                    if suspend_code is not None and ev.code == suspend_code and ev.value == 1:
                        suspended = not suspended   # toggle on initial press only
                    elif ev.code in mode_sel and ev.value:
                        mode = mode_sel[ev.code]
                    elif ev.value:
                        pressed.add(ev.code)
                    else:
                        pressed.discard(ev.code)

                # Rebuild button bytes every event
                g1, g2 = 0, 0
                trig_hold = {"lt": False, "rt": False}

                for code in pressed:
                    binfo = button_cfg.get((mode, code))
                    if binfo:
                        grp, mask = binfo
                        if grp == 1: g1 |= mask
                        else:        g2 |= mask
                    tb = trigger_btn_cfg.get((mode, code))
                    if tb:
                        trig_hold[tb] = True

                # POV hats report -1/0/+1. Require a full ±1 before emitting a
                # d-pad direction so a hat resting slightly off-neutral can
                # never hold a direction (which would block menu navigation).
                if hat[0] <= -1: g2 |= OBERON_BTNS["dpad_left"][1]
                if hat[0] >= 1:  g2 |= OBERON_BTNS["dpad_right"][1]
                if hat[1] <= -1: g2 |= OBERON_BTNS["dpad_up"][1]
                if hat[1] >= 1:  g2 |= OBERON_BTNS["dpad_down"][1]

                if suspended:
                    # Freeze every axis at neutral so the throttle (on rt) can't
                    # scroll the Xbox dashboard. Buttons and d-pad still work,
                    # so you can navigate and launch a game. Press again to resume.
                    ax = {t: 0.0 for t in AXIS_TARGETS}
                else:
                    ax = dict(axes)
                    if trig_hold["lt"]: ax["lt"] = 1.0
                    if trig_hold["rt"]: ax["rt"] = 1.0

                state.update(ax, g1, g2)

        except OSError:
            time.sleep(1)  # device unplugged; keep trying


# ─── WebSocket server ─────────────────────────────────────────────────────────

def make_handler(state, verbose):
    hostname = socket.gethostname()
    handshake = bytes([0x0A]) + hostname.encode("utf-8")

    async def handler(websocket):
        addr = websocket.remote_address
        print(f"[oberon] connected from {addr[0]}")
        await websocket.send(handshake)

        try:
            async for msg in websocket:
                if not isinstance(msg, (bytes, bytearray)) or not msg:
                    continue
                if msg[0] != 0xFA:
                    continue

                axes, g1, g2 = state.snapshot()
                pkt = build_packet(axes, g1, g2)
                await websocket.send(pkt)

                if verbose:
                    lt = (axes["lt"] + 1) / 2
                    rt = (axes["rt"] + 1) / 2
                    print(f"  lx={axes['lx']:+.2f} ly={axes['ly']:+.2f} "
                          f"rx={axes['rx']:+.2f} lt={lt:.2f} rt={rt:.2f} "
                          f"g1={g1:08b} g2={g2:08b}")

        except websockets.exceptions.ConnectionClosed:
            pass
        print(f"[oberon] disconnected from {addr[0]}")

    return handler


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Oberon Remote WebSocket server for Saitek X52")
    ap.add_argument("--config",  default=os.path.join(os.path.dirname(__file__),
                                                       "../sender/sender_config.json"))
    ap.add_argument("--device",  help="/dev/input/eventX path")
    ap.add_argument("--port",    type=int, default=PORT)
    ap.add_argument("--list",    action="store_true", help="list input devices and exit")
    ap.add_argument("--probe",   action="store_true", help="print live events and exit")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--menu", action="store_true",
                    help="start with axes SUSPENDED (throttle/sticks frozen) so "
                         "the Xbox dashboard doesn't scroll; press the suspend "
                         "button once in-game to start flying")
    args = ap.parse_args()

    if args.list:
        for path in list_devices():
            try:
                d = InputDevice(path); print(f"{path}  {d.name}"); d.close()
            except OSError:
                pass
        return

    with open(args.config) as f:
        cfg = json.load(f)

    dev = InputDevice(args.device) if args.device \
        else find_device(cfg.get("device_match", "X52"))
    if dev is None:
        sys.exit(f"No device matching '{cfg.get('device_match')}'. "
                 f"Run --list, then re-run with --device /dev/input/eventX")
    print(f"[evdev]  {dev.path}  {dev.name}")

    if args.probe:
        print("Move axes / press buttons. Ctrl-C to stop.\n")
        for ev in dev.read_loop():
            if ev.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                print(f"{code_name(ev.type, ev.code):28s}  value={ev.value}")
        return

    # Build axis config
    absinfo  = dict(dev.capabilities().get(ecodes.EV_ABS, []))
    axis_cfg = {}
    for name, acfg in cfg.get("axes", {}).items():
        code = ecodes.ecodes.get(name)
        if code is None or code not in absinfo:
            print(f"  [!] axis {name} not on this device, skipped")
            continue
        if acfg["target"] not in AXIS_TARGETS:
            sys.exit(f"axis {name}: unknown target '{acfg['target']}'")
        axis_cfg[code] = (acfg["target"], acfg, absinfo[code])

    # Build button config
    mode_sel        = {}
    button_cfg      = {}
    trigger_btn_cfg = {}
    for mode_name, bmap in cfg.get("buttons", {}).items():
        mode = int(mode_name.replace("mode", "") or 0)
        for name, target in bmap.items():
            code = ecodes.ecodes.get(name)
            if code is None:
                print(f"  [!] button code '{name}' unknown, skipped")
                continue
            if target.startswith("select_mode"):
                mode_sel[code] = int(target[-1])
            elif target in OBERON_BTNS:
                button_cfg[(mode, code)] = OBERON_BTNS[target]
            elif target in TRIGGER_BTNS:
                trigger_btn_cfg[(mode, code)] = TRIGGER_BTNS[target]
            else:
                print(f"  [!] button '{name}': unknown target '{target}', skipped")

    dev.grab()

    # Optional: a button that toggles axis-suspend (freezes throttle/sticks so
    # they can't scroll the Xbox dashboard). Set "suspend_button" in the config
    # to an evdev button name, e.g. "BTN_PINKIE".
    suspend_code = None
    sb = cfg.get("suspend_button")
    if sb:
        suspend_code = ecodes.ecodes.get(sb)
        if suspend_code is None:
            print(f"  [!] suspend_button '{sb}' unknown, ignored")
        else:
            print(f"  Menu-suspend toggle: press {sb} to freeze/unfreeze axes")

    if args.menu and suspend_code is None:
        print("  [!] --menu set but no working suspend_button — you won't be able\n"
              "      to UNFREEZE. Set suspend_button in the config first.")

    if args.menu:
        print("  Starting SUSPENDED (menu-safe). Axes are frozen; navigate the\n"
              "  dashboard, launch your game, then press the suspend button once.")

    state = HOTASState()
    threading.Thread(
        target=evdev_reader,
        args=(dev, axis_cfg, mode_sel, button_cfg, trigger_btn_cfg,
              cfg.get("hat_to_dpad", True), state, suspend_code, args.menu),
        daemon=True
    ).start()

    # Print this board's IP so the user can enter it into the Xbox app
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "unknown"

    async def run():
        handler = make_handler(state, args.verbose)
        async with ws_serve(handler, "0.0.0.0", args.port):
            print(f"[oberon] WebSocket server on port {args.port}")
            print(f"[oberon] Board IP : {local_ip}")
            print(f"[oberon] On Xbox  : open Oberon Remote -> enter {local_ip} -> Connect")
            await asyncio.Future()

    asyncio.run(run())


if __name__ == "__main__":
    main()
