# HOTAS Bridge

**Use a Saitek X52 / X52 Pro on an Xbox Series X|S — wirelessly, with one Orange Pi.**

No adapter, no second board, no soldering, no Dev Mode. The Pi reads your stick
and streams its inputs to the free **Oberon Remote** app on the Xbox, which
injects them as a controller.

```
X52  ──USB──►  Orange Pi Zero 3  ──WiFi──►  Oberon app on Xbox  ──►  game
```

Built and tuned for **Star Wars: Squadrons**, with a ready MSFS 2024 profile too.

---

## Layout

![X52 Pro layout](x52_layout.png)

One mode — the dial position doesn't matter, all layers are identical. The
throttle sits on the left stick, so it can never fire the weapon.

| Xbox | HOTAS control | Squadrons function |
|------|---------------|--------------------|
| Left stick Y | Throttle lever | Throttle (up = forward) |
| Left stick X | Stick left / right | Roll |
| Right stick Y | Stick fwd / back | Pitch |
| Right stick X | Twist | Yaw |
| RT | Main trigger | Fire |
| RB | Thumb FIRE button | Fire Right Auxiliary |
| LB | Pinkie trigger | Fire Left Auxiliary |
| A | C head button | Cycle Targets |
| B | B head button | Deploy Countermeasures |
| RS | A head button | Free Look |
| LT | D button (throttle) | Select Target Ahead |
| LS | I button (throttle) | Boost |
| Menu | T1 rocker | Menu |
| View | T2 rocker | Show Loadout |
| D-pad | POV hat | Power: up=weapon, left=engine, down=balance, right=shield |
| — | E button (throttle) | Menu-disable — freezes the throttle for menus |
| — | Upper / lower rotary | MFD / button-LED brightness |

---

## Quick start

**1. Install the Xbox app.** Microsoft Store → search **Oberon Remote Input**
(by SamsidParty) → install. Retail mode is fine, no Dev Mode needed.

**2. Install on the Pi.** Copy this folder to the Orange Pi, then:

```bash
cd hotas-bridge
sudo ./install.sh oberon
```

It installs dependencies and a service that starts on boot. It also offers to
set up the optional throttle-screen display (see below).

**3. Calibrate to your stick** (recommended — X52 and X52 Pro report axes under
different codes):

```bash
sudo python3 /opt/hotas-bridge/oberon/calibrate.py --game squadrons
```

Follow the prompts. It detects your real axes, learns their ranges, skips any
that jitter on their own, lets you pick the menu-disable button, and applies the
result automatically.

**4. Connect.** On the Xbox open **Oberon Remote**, enter the Pi's IP (printed
on startup, or shown on the throttle screen), press **Connect**, and fly.

---

## Menu mode (why the throttle doesn't wreck menus)

A throttle rests wherever you leave it — it never re-centers. Mapped to a stick
axis, a raised throttle reads as a stick held off-center, which scrolls menus
and steers radial wheels you're trying to use.

**The fix is built in.** The service starts in *menu mode*, where the throttle
is frozen to neutral while the flight sticks stay live. Navigate menus, launch
your match, then press the **E button** once to unfreeze and fly. Press it again
whenever you're back in a menu. The throttle screen shows `MENU:ON`/`OFF`, and
the button LEDs turn **amber** in menu mode, **green** when flying.

---

## Throttle display & LEDs (X52 Pro, optional)

The X52 Pro's throttle screen can show live bridge status, and the button LEDs
can reflect state. This needs the **libx52** driver
(https://github.com/nirenjan/libx52), which the installer sets up for you.

```
192.168.1.69       ← Pi IP (enter this in the Oberon app)
XBOX:ON    45ms    ← Oberon connected + poll ping
MENU:ON            ← menu mode on/off
```

- **Button LEDs:** green while flying, amber in menu mode. (The X52 Pro's FIRE
  and THROTTLE LEDs are on/off only — hardware limitation — so those don't
  change color; the A/B/D/E/T LEDs do.)
- **Brightness knobs:** the two throttle rotaries adjust MFD brightness (upper)
  and button-LED brightness (lower), live.
- The MFD backlight is green by hardware; you can't change its color, only its
  brightness.

**On Armbian / Debian / arm64** the Ubuntu PPA has no package, so the installer
builds libx52 from source automatically. To do it by hand:

```bash
sudo /opt/hotas-bridge/oberon/build_libx52.sh
sudo systemctl restart hotas-oberon
```

> Don't add the Ubuntu PPA to a Debian/Armbian `sources.list` — it can break
> apt. Build from source instead (the script above does exactly that).

Everything here is optional: if libx52 isn't installed, the bridge runs exactly
the same without the display, LEDs, or brightness knobs.

---

## In-game (Star Wars: Squadrons)

The mapping matches Squadrons' **default** control scheme, so no in-game
rebinding is needed — just set the scheme to Default. For MSFS 2024, use the
ready-made profile:

```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py \
    --config /opt/hotas-bridge/sender/profiles/sender_config.msfs2024.json --menu
```

---

## Tweaking the mapping

Everything lives in `sender/sender_config.json`:

- **`axes`** — maps an evdev axis to an Xbox target (`lx ly rx ry lt rt`).
  `invert` flips it, `deadzone` (0–1) kills centre drift, `expo` (0–1) softens
  the middle of the throw.
- **`buttons`** — one active layer here; targets are `a b x y lb rb ls rs view
  menu dpad_*` plus `lt_button` / `rt_button` for trigger taps.
- **`suspend_button`** — the button that freezes the throttle for menus.
- **`brightness_axis` / `led_brightness_axis`** — throttle rotaries for MFD and
  LED brightness (set to `""` to disable).
- **`hat_to_dpad`** — POV hat drives the d-pad when true.

Find any button or axis code with:

```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py --probe
```

After any change, restart so it reloads: `sudo systemctl restart hotas-oberon`.

---

## Troubleshooting

**Menu cursor drifts / throttle scrolls the dashboard.** You're not in menu
mode. Start the service (it boots in menu mode) or press the E button to freeze
the throttle. After any file change, `sudo systemctl restart hotas-oberon`.

**A control does the wrong thing.** Your stick's codes don't match the config —
re-run the calibration wizard.

**Xbox won't connect.** Pi and Xbox must share a network. Re-enter the Pi's IP
in Oberon. Check the server: `systemctl status hotas-oberon`.

**Watch what's happening live:** `journalctl -u hotas-oberon -f` shows mode
switches, menu toggles, and connection state. Add `--verbose` to a manual run to
see axis values on every poll.

**The display stays blank.** libx52 isn't built or `x52cli` isn't on PATH. Test
directly: `sudo x52cli mfd 0 "TEST"`. If that errors, run
`build_libx52.sh` and replug the stick to apply the udev rule.

---

## Files

```
hotas-bridge/
├── install.sh                     one-shot installer (sudo ./install.sh oberon)
├── x52_layout.png                 the layout diagram above
├── oberon/
│   ├── oberon_server.py           the server: reads X52, talks to Oberon
│   ├── mfd.py                     optional throttle-screen + LED status (libx52)
│   ├── build_libx52.sh            builds libx52 from source (Armbian/arm64)
│   ├── calibrate.py               calibration wizard
│   └── hotas-oberon.service       starts the server on boot (menu mode)
└── sender/
    ├── sender_config.json         your active config
    └── profiles/
        ├── sender_config.squadrons.json
        └── sender_config.msfs2024.json
```

The `proxy/`, `receiver/`, and `tools/` folders belong to an alternative
USB-hardware mode and aren't needed for Oberon.
