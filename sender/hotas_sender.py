#!/usr/bin/env python3
"""
hotas_sender.py — runs on OPi A (the board the Saitek X52 is plugged into).

Reads the X52 via evdev, normalises axes using the device's own reported
ranges, applies deadzone/expo/invert per the JSON config, folds the 3-position
mode switch into button shift layers, and streams a fixed 40-byte state
record over UDP at a constant rate.

Usage:
    sudo python3 hotas_sender.py --config sender_config.json --host 10.42.0.1
    sudo python3 hotas_sender.py --list          # show input devices
    sudo python3 hotas_sender.py --probe         # print live events + code names
"""

import argparse
import json
import math
import os
import select
import socket
import struct
import sys
import time

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python3-evdev missing. Install with: sudo apt install python3-evdev")

MAGIC = b"HB01"
RECORD_FMT = "<4sI4h2HHBBI12x"   # magic, seq, lx ly rx ry, lt rt, buttons, flags, rsv, seq2, pad -> 40 bytes
SEND_HZ = 250

# GIP button bit positions, expressed as (byte, mask) over a u16:
# low byte  == GIP payload byte 0 (report offset 4)
# high byte == GIP payload byte 1 (report offset 5)
BUTTON_BITS = {
    "menu":       0x0004,
    "view":       0x0008,
    "a":          0x0010,
    "b":          0x0020,
    "x":          0x0040,
    "y":          0x0080,
    "dpad_up":    0x0100,
    "dpad_down":  0x0200,
    "dpad_left":  0x0400,
    "dpad_right": 0x0800,
    "lb":         0x1000,
    "rb":         0x2000,
    "ls":         0x4000,
    "rs":         0x8000,
}

AXIS_TARGETS = ("lx", "ly", "rx", "ry", "lt", "rt")

# Digital buttons that drive an analog trigger to full deflection. Lets the
# X52's trigger act as "fire" in games that read LT/RT as buttons.
TRIGGER_BUTTONS = {"lt_button": "lt", "rt_button": "rt"}

FLAG_OVERRIDE_LEFT  = 0x01   # lx/ly valid
FLAG_OVERRIDE_RIGHT = 0x02   # rx/ry valid
FLAG_OVERRIDE_TRIG  = 0x04   # lt/rt valid


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
        if match.lower() in dev.name.lower():
            caps = dev.capabilities()
            if ecodes.EV_ABS in caps:      # skip the X52's extra kbd node
                return dev
        dev.close()
    return None


def shape(value, absinfo, cfg):
    """Normalise raw evdev value to -1..1, apply deadzone, expo, invert."""
    lo, hi = absinfo.min, absinfo.max
    span = (hi - lo) or 1
    v = (value - lo) / span * 2.0 - 1.0
    if cfg.get("invert"):
        v = -v
    dz = cfg.get("deadzone", 0.0)
    if abs(v) < dz:
        v = 0.0
    else:
        v = math.copysign((abs(v) - dz) / (1.0 - dz), v)
    expo = cfg.get("expo", 0.0)
    if expo > 0:
        v = (1 - expo) * v + expo * (v ** 3)
    return max(-1.0, min(1.0, v))


def to_stick(v):     return int(round(v * 32767))
def to_trigger(v):   return int(round((v + 1.0) / 2.0 * 1023))   # -1..1 -> 0..1023


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sender_config.json")
    ap.add_argument("--host", help="receiver IP (overrides config)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--shm", action="store_true",
                    help="publish to /dev/shm/hotas_state locally instead of UDP "
                         "(single-Pi Oberon mode)")
    ap.add_argument("--device", help="/dev/input/eventX (overrides name match)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.list:
        for path in list_devices():
            try:
                d = InputDevice(path)
                print(f"{path}  {d.name}")
                d.close()
            except OSError:
                pass
        return

    with open(args.config) as f:
        cfg = json.load(f)

    host = args.host or cfg.get("host", "10.42.0.1")
    port = args.port or cfg.get("port", 5555)

    dev = InputDevice(args.device) if args.device else find_device(cfg.get("device_match", "X52"))
    if dev is None:
        sys.exit(f"No input device matching '{cfg.get('device_match')}'. "
                 f"Run with --list, then set device_match or use --device.")
    print(f"Using: {dev.path}  {dev.name}")

    if args.probe:
        print("Press buttons / move axes. Ctrl-C to stop.")
        for ev in dev.read_loop():
            if ev.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                print(f"{code_name(ev.type, ev.code):24s} value={ev.value}")
        return

    dev.grab()   # exclusive: nothing else on this board consumes the stick

    # Resolve config name strings -> evdev codes
    axis_cfg = {}                        # abs code -> (target, shaping cfg, absinfo)
    absinfo = dict(dev.capabilities().get(ecodes.EV_ABS, []))
    for name, acfg in cfg.get("axes", {}).items():
        code = ecodes.ecodes.get(name)
        if code is None or code not in absinfo:
            print(f"  ! axis {name} not present on this device, skipped")
            continue
        tgt = acfg["target"]
        if tgt not in AXIS_TARGETS:
            sys.exit(f"axis {name}: bad target '{tgt}'")
        axis_cfg[code] = (tgt, acfg, absinfo[code])

    hat_dpad = cfg.get("hat_to_dpad", True)

    mode_sel = {}                        # key code -> mode number
    button_cfg = {}                      # (mode, key code) -> button mask
    trigger_btn_cfg = {}                 # (mode, key code) -> "lt" | "rt"
    for mode_name, bmap in cfg.get("buttons", {}).items():
        mode = int(mode_name.replace("mode", "") or 0)
        for name, target in bmap.items():
            code = ecodes.ecodes.get(name)
            if code is None:
                print(f"  ! button {name} unknown, skipped")
                continue
            if target.startswith("select_mode"):
                mode_sel[code] = int(target[-1])
            elif target in BUTTON_BITS:
                button_cfg[(mode, code)] = BUTTON_BITS[target]
            elif target in TRIGGER_BUTTONS:
                trigger_btn_cfg[(mode, code)] = TRIGGER_BUTTONS[target]
            else:
                print(f"  ! button {name}: bad target '{target}', skipped")

    # State
    axes = {t: 0.0 for t in AXIS_TARGETS}
    pressed = set()
    hat = [0, 0]
    mode = 1
    seq = 0
    used_targets = {t for (t, _, _) in axis_cfg.values()}
    flags = 0
    if {"lx", "ly"} & used_targets: flags |= FLAG_OVERRIDE_LEFT
    if {"rx", "ry"} & used_targets: flags |= FLAG_OVERRIDE_RIGHT
    if ({"lt", "rt"} & used_targets) or trigger_btn_cfg:
        flags |= FLAG_OVERRIDE_TRIG

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
    dest = (host, port)
    shm_fd = None
    if args.shm:
        shm_fd = os.open("/dev/shm/hotas_state", os.O_RDWR | os.O_CREAT, 0o644)
        os.pwrite(shm_fd, b"\x00" * 40, 0)
        print("Publishing locally to /dev/shm/hotas_state (single-Pi mode)")
    period = 1.0 / SEND_HZ
    next_send = time.monotonic()
    print(f"Streaming to {host}:{port} at {SEND_HZ} Hz. Mode layer = {mode}")

    while True:
        timeout = max(0.0, next_send - time.monotonic())
        r, _, _ = select.select([dev.fd], [], [], timeout)
        if r:
            for ev in dev.read():
                if ev.type == ecodes.EV_ABS:
                    if ev.code in axis_cfg:
                        tgt, acfg, info = axis_cfg[ev.code]
                        axes[tgt] = shape(ev.value, info, acfg)
                    elif hat_dpad and ev.code == ecodes.ABS_HAT0X:
                        hat[0] = ev.value
                    elif hat_dpad and ev.code == ecodes.ABS_HAT0Y:
                        hat[1] = ev.value
                elif ev.type == ecodes.EV_KEY:
                    if ev.code in mode_sel and ev.value:
                        mode = mode_sel[ev.code]
                        if args.verbose:
                            print(f"mode -> {mode}")
                    elif ev.value:
                        pressed.add(ev.code)
                    else:
                        pressed.discard(ev.code)

        now = time.monotonic()
        if now >= next_send:
            next_send += period
            if now - next_send > 0.5:          # fell behind; resync
                next_send = now + period
            buttons = 0
            trig_hold = {"lt": False, "rt": False}
            for code in pressed:
                buttons |= button_cfg.get((mode, code), 0)
                tb = trigger_btn_cfg.get((mode, code))
                if tb:
                    trig_hold[tb] = True
            if hat[0] < 0: buttons |= BUTTON_BITS["dpad_left"]
            if hat[0] > 0: buttons |= BUTTON_BITS["dpad_right"]
            if hat[1] < 0: buttons |= BUTTON_BITS["dpad_up"]
            if hat[1] > 0: buttons |= BUTTON_BITS["dpad_down"]

            seq = (seq + 1) & 0xFFFFFFFF
            pkt = struct.pack(
                RECORD_FMT, MAGIC, seq,
                to_stick(axes["lx"]), to_stick(axes["ly"]),
                to_stick(axes["rx"]), to_stick(axes["ry"]),
                1023 if trig_hold["lt"] else to_trigger(axes["lt"]),
                1023 if trig_hold["rt"] else to_trigger(axes["rt"]),
                buttons, flags, 0, seq,
            )
            try:
                if shm_fd is not None:
                    os.pwrite(shm_fd, pkt, 0)
                else:
                    sock.sendto(pkt, dest)
            except OSError:
                pass       # link flap; keep going, receiver-side failsafe covers it


if __name__ == "__main__":
    main()
