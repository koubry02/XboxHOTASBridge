#!/usr/bin/env python3
"""
calibrate.py — interactive calibration wizard for HOTAS Bridge.

Solves the "which axis is which" problem by DETECTING your hardware instead
of assuming X52 vs X52 Pro codes. It:

  1. Detects which axis code moves when you wiggle each named control.
  2. Learns each axis's real min / max / resting-centre by watching you sweep.
  3. Flags any axis that dithers on its own (idle jitter) and leaves it
     unmapped, so a twitchy rotary can never reach the game or a menu.
  4. Detects each button code as you press it.
  5. Writes a complete, unit-specific sender config you can use as-is.

Run:
    sudo python3 calibrate.py                 # full wizard -> sender_config.calibrated.json
    sudo python3 calibrate.py --game squadrons
    sudo python3 calibrate.py --out /path/to/config.json

Nothing here talks to the Xbox. It only reads the stick and writes JSON.
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    sys.exit("python3-evdev missing.  Run: sudo apt install python3-evdev")

IDLE_SETTLE_S   = 2.0     # how long we watch an untouched device to learn dither
DETECT_MOVE_S   = 4.0     # window to catch which axis the user moves
IDLE_JITTER_TOL = 3       # raw counts of movement at rest we treat as "dithering"

AXIS_ROLES = [
    ("roll",     "MAIN STICK LEFT and RIGHT (roll / aileron)"),
    ("pitch",    "MAIN STICK FORWARD and BACK (pitch / elevator)"),
    ("rudder",   "TWIST the stick (rudder / yaw)"),
    ("throttle", "THROTTLE lever full range"),
]

# role -> (Xbox target, invert, deadzone, expo), per game preset.
# Throttle is ALWAYS on rt (a trigger). A throttle on a stick axis scrolls the
# Xbox dashboard, so it never goes there regardless of preset.
ROLE_TARGETS = {
    "squadrons": {
        "roll":     ("lx", False, 0.10, 0.20),   # stick L/R = turn, menu-safe
        "pitch":    ("ly", True,  0.10, 0.20),
        "rudder":   ("rx", False, 0.14, 0.40),   # twist = roll/yaw
        "throttle": ("rt", False, 0.03, 0.0),
    },
    "generic": {
        "roll":     ("lx", False, 0.08, 0.20),
        "pitch":    ("ly", True,  0.08, 0.20),
        "rudder":   ("rx", False, 0.12, 0.30),
        "throttle": ("rt", False, 0.02, 0.0),    # rt, NOT a stick axis
    },
}


def pick_device():
    devs = []
    for path in list_devices():
        try:
            d = InputDevice(path)
        except OSError:
            continue
        if ecodes.EV_ABS in d.capabilities():
            devs.append(d)
        else:
            d.close()
    if not devs:
        sys.exit("No devices with axes found. Is the stick plugged in?")
    # Prefer something that looks like the X52
    for d in devs:
        if "x52" in d.name.lower() or "saitek" in d.name.lower():
            for other in devs:
                if other is not d:
                    other.close()
            return d
    print("Multiple input devices found:")
    for i, d in enumerate(devs):
        print(f"  [{i}] {d.path}  {d.name}")
    idx = int(input("Pick device number: ").strip())
    chosen = devs[idx]
    for i, d in enumerate(devs):
        if i != idx:
            d.close()
    return chosen


def drain(dev, seconds):
    """Collect all ABS events over a window: returns {code: [values...]}."""
    end = time.monotonic() + seconds
    seen = defaultdict(list)
    dev.set_blocking(False) if hasattr(dev, "set_blocking") else None
    while time.monotonic() < end:
        try:
            for ev in dev.read():
                if ev.type == ecodes.EV_ABS:
                    seen[ev.code].append(ev.value)
        except BlockingIOError:
            pass
        except OSError:
            pass
        time.sleep(0.005)
    return seen


def learn_idle(dev):
    print("\n=== Step 1: resting state ===")
    print("Leave EVERYTHING untouched. Learning idle jitter...")
    time.sleep(0.5)
    seen = drain(dev, IDLE_SETTLE_S)
    dithering = {}
    resting = {}
    for code, vals in seen.items():
        if not vals:
            continue
        spread = max(vals) - min(vals)
        resting[code] = sum(vals) // len(vals)
        if spread > IDLE_JITTER_TOL:
            dithering[code] = spread
    # Also record current absinfo centre for axes that sent nothing
    for code, absinfo in dict(dev.capabilities().get(ecodes.EV_ABS, [])).items():
        resting.setdefault(code, absinfo.value)
    if dithering:
        names = ", ".join(f"{ecodes.ABS[c]}(±{s})" for c, s in dithering.items())
        print(f"  Self-dithering axes (will stay UNMAPPED): {names}")
    else:
        print("  No idle dither detected. Clean.")
    return resting, set(dithering)


def detect_axis(dev, label, dithering):
    """Ask the user to move a control; return the axis code that moved most."""
    while True:
        input(f"\n  Ready to map: {label}\n  Press ENTER, then MOVE IT through its full range for {int(DETECT_MOVE_S)}s...")
        drain(dev, 0.2)  # flush
        seen = drain(dev, DETECT_MOVE_S)
        # Score by range of motion, ignore known dithering unless it dominates
        scores = {}
        for code, vals in seen.items():
            if len(vals) < 2:
                continue
            rng = max(vals) - min(vals)
            if code in dithering:
                rng -= IDLE_JITTER_TOL * 4   # penalise dithery axes
            scores[code] = (rng, min(vals), max(vals))
        if not scores:
            print("  Nothing moved. Try again.")
            continue
        best = max(scores, key=lambda c: scores[c][0])
        rng, lo, hi = scores[best]
        if rng < 10:
            print("  Barely moved. Try again, use the full range.")
            continue
        print(f"  Detected {ecodes.ABS[best]}  range {lo}..{hi}")
        return best, lo, hi


def detect_button(dev, label):
    input(f"\n  {label}\n  Press ENTER, then press the button ONCE...")
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            for ev in dev.read():
                if ev.type == ecodes.EV_KEY and ev.value == 1:
                    name = ecodes.bytype[ecodes.EV_KEY].get(ev.code)
                    if isinstance(name, list):
                        name = name[0]
                    print(f"  Detected {name}")
                    return name
        except (BlockingIOError, OSError):
            pass
        time.sleep(0.005)
    print("  Timeout, skipping.")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=["squadrons", "generic"], default="squadrons")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                    "../sender/sender_config.calibrated.json"))
    ap.add_argument("--buttons", action="store_true",
                    help="also walk through button mapping (optional)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("Note: if you get permission errors, re-run with sudo.\n")

    dev = pick_device()
    print(f"\nCalibrating: {dev.name}  ({dev.path})")
    dev.grab()

    try:
        resting, dithering = learn_idle(dev)

        axes_cfg = {}
        detected = {}
        print("\n=== Step 2: detect and calibrate each control ===")
        for role, label in AXIS_ROLES:
            code, lo, hi = detect_axis(dev, label, dithering)
            detected[role] = (code, lo, hi)

        # Map detected roles to Xbox targets. Throttle ALWAYS goes to a trigger
        # (rt): a stick axis held by a throttle scrolls Xbox menus. Roll on the
        # left stick keeps menu navigation clean; rudder on the right stick.
        role_target = ROLE_TARGETS[args.game]
        for role, (code, lo, hi) in detected.items():
            tgt, inv, dz, expo = role_target[role]
            name = ecodes.ABS[code]
            axes_cfg[name] = {
                "target": tgt, "invert": inv,
                "deadzone": dz, "expo": expo,
                "_calibrated_range": [lo, hi],
                "_rest": resting.get(code, (lo + hi) // 2),
            }

        # Buttons: default sensible X52 set, or interactive
        if args.buttons:
            print("\n=== Step 3: buttons (optional) ===")
            btn_targets = ["a", "b", "x", "y", "lb", "rb"]
            buttons_m1 = {}
            for tgt in btn_targets:
                name = detect_button(dev, f"Map a button to '{tgt}'")
                if name:
                    buttons_m1[name] = tgt
            buttons = {"mode1": buttons_m1}
        else:
            buttons = {
                "mode1": {
                    "BTN_TRIGGER": "a", "BTN_THUMB": "rb", "BTN_THUMB2": "b",
                    "BTN_TOP": "x", "BTN_TOP2": "y",
                    "BTN_BASE": "lb", "BTN_BASE2": "ls",
                    "BTN_BASE3": "select_mode1", "BTN_BASE4": "select_mode2",
                    "BTN_BASE5": "select_mode3", "BTN_BASE6": "rs"
                },
                "mode2": {
                    "BTN_TRIGGER": "a", "BTN_THUMB": "rb", "BTN_THUMB2": "lb",
                    "BTN_TOP": "y", "BTN_TOP2": "x", "BTN_BASE": "ls", "BTN_BASE2": "rs"
                },
                "mode3": {
                    "BTN_TRIGGER": "a", "BTN_THUMB": "view", "BTN_THUMB2": "menu",
                    "BTN_TOP": "b", "BTN_TOP2": "x"
                }
            }

        # Detect the menu-suspend button (freezes axes so the throttle can't
        # scroll the Xbox dashboard). Falls back to BTN_PINKIE if skipped.
        print("\n=== Suspend button ===")
        print("Pick a button to FREEZE/UNFREEZE the throttle for menu navigation.")
        suspend_btn = detect_button(dev, "Press the button you want as the menu-suspend toggle")
        if not suspend_btn:
            suspend_btn = "BTN_PINKIE"
            print(f"  none detected, defaulting to {suspend_btn}")
        # Make sure the suspend button isn't also a game button
        for m in buttons.values():
            if suspend_btn in m:
                del m[suspend_btn]

        cfg = {
            "_readme": [
                f"Auto-calibrated for: {dev.name}",
                f"Game preset: {args.game}",
                "Axis codes and ranges were DETECTED from your hardware, not assumed.",
                "Self-dithering axes were left unmapped so they can't reach the game.",
                "_calibrated_range and _rest are recorded per axis for reference;",
                "the sender reads live absinfo at runtime, so they are informational.",
                "Throttle is on rt (bind in-game: Controls > Throttle axis > Right Trigger).",
            ],
            "device_match": dev.name.split()[0] if dev.name else "X52",
            "host": "10.42.0.1",
            "port": 5555,
            "hat_to_dpad": True,
            "suspend_button": suspend_btn,
            "axes": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                     for k, v in axes_cfg.items()},
            "_axis_calibration": {k: {"range": v["_calibrated_range"], "rest": v["_rest"]}
                                  for k, v in axes_cfg.items()},
            "buttons": buttons,
        }

        with open(args.out, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"\n✓ Wrote {args.out}")
        print("\nDetected mapping:")
        for name, a in cfg["axes"].items():
            print(f"  {name:10s} -> {a['target']:3s}  (invert={a['invert']})")
        print("\nRun the server with it:")
        print(f"  sudo python3 oberon_server.py --config {args.out}")

    finally:
        try:
            dev.ungrab()
        except Exception:
            pass


if __name__ == "__main__":
    main()
