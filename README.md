# HOTAS Bridge — Saitek X52 on Xbox Series X, wireless

Two modes. Start with Mode A (Oberon) — it takes 10 minutes and needs no
extra hardware. If it doesn't suit your games, move to Mode B (USB Proxy),
which is bulletproof but more work.

---

## Hardware you need

| Item | Count | Notes |
|---|---|---|
| Orange Pi Zero 3 (Allwinner H618) | 1 or 2 | 1 for Oberon, 2 for USB Proxy |
| Saitek X52 / X52 Pro | 1 | |
| Xbox Series X controllers | 2 | Only needed for USB Proxy mode |
| USB-A to USB-A / Micro cables | — | For connecting stick and controller to Pi |
| USB-C data cable | 1 | For USB Proxy mode only (Pi → Xbox port) |
| MicroSD cards (≥ 8 GB) | 1–2 | Armbian per board |
| 5V/3A power supply for each Pi | 1–2 | Do NOT power OPi B from Xbox USB in proxy mode |

---

## Software you need

- **Both Pis:** Armbian (Ubuntu Noble, `edge` kernel recommended)
- **Xbox:** Oberon Remote Input from the Microsoft Store
  (`https://apps.microsoft.com/detail/9pk5stjzff3s`) — **Mode A only**
- This package: `hotas-bridge/` folder copied to each Pi

---

## Step 0 — Get Armbian on both boards

1. Download Armbian for Orange Pi Zero 3 from https://www.armbian.com/orange-pi-zero-3/
   Choose the **edge** kernel image (Ubuntu Noble base).
2. Flash to MicroSD with Balena Etcher or `dd`.
3. First boot: follow prompts to set root password and create a user.
4. `sudo armbian-config` → System → Timezone → set yours.
5. `sudo apt update && sudo apt upgrade -y`

---

## Step 1 — Copy the package to both boards

On your PC (where you downloaded this zip):

```bash
# Extract
unzip hotas-bridge.zip

# Copy to OPi A (HOTAS board)
scp -r hotas-bridge/ root@<OPI_A_IP>:/opt/

# Copy to OPi B (console board) — only if doing USB Proxy mode
scp -r hotas-bridge/ root@<OPI_B_IP>:/opt/
```

Or copy via USB stick, then `mv hotas-bridge/ /opt/`.

---

## Step 2 — Dedicated WiFi link between the two boards

Skip this step if you are only doing **Mode A (Oberon)** with a single Pi.

**On OPi B** (console side — becomes the WiFi AP):
```bash
cd /opt/hotas-bridge/tools
sudo ./link_setup.sh ap
```

**On OPi A** (HOTAS side — joins the AP):
```bash
cd /opt/hotas-bridge/tools
sudo ./link_setup.sh client
```

**Verify latency:**
```bash
# On OPi A:
ping -c 50 -i 0.05 10.42.0.1
# Expect: ~2–5 ms avg, no spikes above 20 ms
# If spiky: run   iw dev wlan0 get power_save   on both boards
# Must say "off". If not:  sudo iw dev wlan0 set power_save off
```

Addresses: OPi B = `10.42.0.1`, OPi A = `10.42.0.x` (assigned by NM).

---

---

# MODE A — Oberon Remote (single Pi, recommended first)

**How it works:** The Pi reads the X52 and serves a WebSocket on port 26401.
The Oberon app on the Xbox connects to the Pi and injects the state as a
virtual controller. No USB cable to the Xbox, no auth handshake, no second Pi.

**Limitation:** Some games detect and block synthetic inputs. Squadrons and
MSFS 2024 work fine. Competitive shooters with kernel anti-cheat may not.

### A1 — Install Oberon Remote on Xbox

1. On the Xbox, open the Microsoft Store.
2. Search **"Oberon Remote Input"** (developer: SamsidParty).
3. Install it. It's free.
4. Do **not** open it yet.

### A2 — Map your X52

Plug the X52 into OPi A's USB-A port (use a USB-A hub or OTG adapter since
the Zero 3 has one full-size USB-A port). Confirm it's seen:

```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py --list
# Look for a line containing "X52" or "Saitek"
```

Print live event names while you press every control:
```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py --probe
# Press Trigger → prints something like:  BTN_TRIGGER          value=1
# Move stick   → prints:                  ABS_X                value=23847
```

Write down the code names. Open the config file and update it:
```bash
nano /opt/hotas-bridge/sender/sender_config.json
```

Key fields:
- `device_match`: substring of the X52's evdev name (default `"X52"` usually works)
- `axes`: map ABS codes to `lx / ly / rx / ry / lt / rt`
  - `invert: true` for ABS_Y (evdev reports 0=top, but you want 1.0=up)
  - `deadzone`: 0.05 is a good start; increase to 0.12 if axes drift at rest
  - `expo`: softens centre feel, 0.20–0.30 suits a flight stick
- `buttons.mode1/2/3`: map BTN codes to Xbox button names
  - Available targets: `a b x y lb rb ls rs view menu`
    `dpad_up dpad_down dpad_left dpad_right lt_button rt_button`
  - `select_mode1/2/3`: assign to your X52 mode switch positions

**The default `sender_config.json` is the Star Wars: Squadrons profile.** It
uses the generic X52 axis codes (`ABS_X` / `ABS_Y` / `ABS_RZ`). If your unit
is an X52 **Pro** or those codes don't match your hardware, run the
auto-calibration wizard (see "Auto-calibration" below) and copy its output
over `sender_config.json` — that is the reliable way to get the right codes:

```bash
sudo python3 /opt/hotas-bridge/oberon/calibrate.py --game squadrons
cp /opt/hotas-bridge/sender/sender_config.calibrated.json \
   /opt/hotas-bridge/sender/sender_config.json
```

Squadrons default specifics: throttle is absolute on the right trigger (bind
in-game: Controls > Throttle axis > Right Trigger), left-stick deadzone is
raised to 0.10 so a resting stick doesn't hold Xbox menus, power management
is on the POV hat, and `ry` is left unmapped so it can never jam.

**Ready-made profile for MSFS 2024** is in
`/opt/hotas-bridge/sender/profiles/`. To use it:
```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py \
    --config /opt/hotas-bridge/sender/profiles/sender_config.msfs2024.json
```

### A3 — Test run (manual)

```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py
# Prints: "[oberon] Board IP: 10.x.x.x"
# Keep this terminal open
```

On the Xbox, open **Oberon Remote** → tap **Add Remote** → enter the Pi's IP
address printed above → tap **Connect**. Within a couple of seconds the Xbox
accepts it. Open a game. Check that stick and buttons work.

Run with `--verbose` to see the state printed on every Xbox poll:
```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py --verbose
```

### A4 — Install autostart (Oberon mode)

Once you're happy with the mapping, install the service so the bridge starts
on boot without any manual steps:

```bash
cd /opt/hotas-bridge
sudo ./install.sh oberon
```

That's it. From now on, boot the Pi → boot the Xbox → open Oberon Remote →
tap Connect. No SSH required.

**Manage the service:**
```bash
systemctl status hotas-oberon          # is it running?
journalctl -u hotas-oberon -f          # live log
systemctl restart hotas-oberon         # restart after config change
systemctl stop hotas-oberon            # stop it

# To switch game profiles permanently:
nano /etc/systemd/system/hotas-oberon.service
# Edit the --config line, then:
systemctl daemon-reload && systemctl restart hotas-oberon
```

---

---

# MODE B — USB Proxy (two Pis, bulletproof)

**How it works:** OPi B sits between a genuine Xbox controller and the console
using a USB man-in-the-middle (`usb-proxy` on `raw_gadget`). The console talks
to the real controller — auth passes through untouched. Only the GIP input
reports (packet type `0x20`) get rewritten with X52 state arriving over WiFi
from OPi A. Every game works because the console always sees a licensed device.

Complete Step 2 (WiFi link) before continuing here.

---

## OPi B setup — kernel prep

### B1 — Force USB-C into peripheral (gadget) mode

The Xbox port in this setup is USB-C on OPi B, acting as a USB device. By
default it's in OTG/host mode. Force it with a device tree overlay:

```bash
cd /opt/hotas-bridge/overlays
sudo armbian-add-overlay opi-usbc-peripheral.dts
sudo reboot
```

After reboot, verify it worked:
```bash
ls /sys/class/udc
# Must print something like:  musb-hdrc.1.auto
# Empty output = overlay didn't apply. See Troubleshooting.
```

### B2 — Enable raw_gadget kernel module

```bash
sudo modprobe raw_gadget
echo $?   # must be 0
```

If the module is missing (`FATAL: Module raw_gadget not found`), your kernel
was built without it. Switch to the edge kernel:
```bash
sudo armbian-config   # → System → Alternative kernels → edge → reboot
```
Then retry `modprobe raw_gadget`.

### B3 — Power wiring — READ THIS

The USB-C port carries data to the Xbox. Do not use it as the Pi's power
input at the same time. Feed OPi B 5 V through GPIO header pins 2 and 4
(physical pin numbers, counted from the corner nearest the USB ports). A
dedicated 5V/3A supply here is important — brownouts during a session drop
the controller.

The Xbox's USB port may supply a small amount of current through the same
cable; that is fine and expected. Just make sure the GPIO supply is the
primary feed.

---

## OPi B setup — build and run

### B4 — Build usb-proxy

```bash
cd /opt/hotas-bridge/proxy
sudo ./setup_opi_b.sh
```

Read the output. The line `Lua scripting: enabled (lua5.4)` must appear. If
it says `disabled`, the build will still work but the HOTAS merge will not run.
The most common cause: `libluajit-*-dev` is installed and takes priority. Fix:

```bash
sudo apt remove libluajit-*-dev
cd /opt/hotas-bridge/proxy && sudo ./setup_opi_b.sh
```

### B5 — Phase 1: pure passthrough test

**This is the most important step.** Before any HOTAS code, prove that the
proxy can relay a controller for a sustained session.

1. Plug controller #1 into OPi B's USB-A port (no hub).
2. Plug the USB-C cable from OPi B into the Xbox.
3. Temporarily disable HOTAS injection: open
   `proxy/usb-proxy/injection.json` and set both `"enable"` fields to `false`.
4. Run the proxy:
   ```bash
   cd /opt/hotas-bridge/proxy
   sudo ./run_proxy.sh
   ```
5. The Xbox should recognise the controller. **Play a game for 30+ minutes.**
   - No "connect your controller" prompts = auth relay is solid ✓
   - If the controller drops after ~10 min: bad USB cable or the endpoint log
     shows stalled control transfers (see Troubleshooting).
6. Note the endpoint address in the proxy log (`EP81` or `EP82` or both).
   Open `proxy/usb-proxy/injection.json` and confirm the ep_address values
   match (default `81` and `82` cover the common cases).
7. Re-enable injection: set both `"enable"` fields back to `true`.

---

## OPi A setup (proxy mode)

### B6 — Map the X52 on OPi A

Same process as Mode A steps A2 above (the config format is identical).
The sender uses `sender_config.json` and the same `--list` / `--probe` flags:

```bash
sudo python3 /opt/hotas-bridge/sender/hotas_sender.py --list
sudo python3 /opt/hotas-bridge/sender/hotas_sender.py --probe
nano /opt/hotas-bridge/sender/sender_config.json
```

### B7 — Test the full chain (manual)

Open four terminals (or use `tmux`):

**OPi B — terminal 1 (receiver):**
```bash
python3 /opt/hotas-bridge/receiver/hotas_receiver.py --stats
# Should print ~250 pkt/s once the sender is running
```

**OPi B — terminal 2 (proxy, injection enabled):**
```bash
cd /opt/hotas-bridge/proxy
sudo ./run_proxy.sh -v 1
# -v 1 prints before/after bytes for every injected packet
```

**OPi A:**
```bash
sudo python3 /opt/hotas-bridge/sender/hotas_sender.py
```

Checks:
- Receiver prints ~250 pkt/s ✓
- Proxy log shows `Injection[int EP81] before: ... after: ...` when you move the stick ✓
- In game: stick moves aircraft / throttle changes speed ✓
- Hold right stick on the gamepad: it overrides the HOTAS left/right stick ✓
- Kill the sender (Ctrl-C): within ~1 second input reverts to pure gamepad ✓

---

## Install autostart (proxy mode)

Once Phase 1 passes and the full chain works:

**On OPi A:**
```bash
cd /opt/hotas-bridge
sudo ./install.sh sender
```

**On OPi B:**
```bash
cd /opt/hotas-bridge
sudo ./install.sh receiver
# This enables the receiver service and installs (but does not enable) the proxy.
# After confirming Phase 1 is solid, enable the proxy:
sudo systemctl enable --now hotas-proxy
```

From now on: power both Pis → they connect automatically → Xbox plays.

**Manage:**
```bash
# OPi A
systemctl status hotas-sender
journalctl -u hotas-sender -f
systemctl restart hotas-sender   # after config changes

# OPi B
systemctl status hotas-receiver hotas-proxy
journalctl -u hotas-proxy -f
# Restart both after a config change:
systemctl restart hotas-receiver hotas-proxy
```

---

---

# Per-game profiles

Profiles live in `/opt/hotas-bridge/sender/profiles/`. To use one:

**Mode A (Oberon):**
```bash
nano /etc/systemd/system/hotas-oberon.service
# Change --config line to point at the profile, e.g.:
#   --config /opt/hotas-bridge/sender/profiles/sender_config.msfs2024.json
systemctl daemon-reload && systemctl restart hotas-oberon
```

**Mode B (Proxy sender):**
```bash
nano /etc/systemd/system/hotas-sender.service
# Same: change --config line
systemctl daemon-reload && systemctl restart hotas-sender
```

---

## Star Wars: Squadrons

Profile: `sender_config.squadrons.json`

| X52 control | Xbox input | In-game action |
|---|---|---|
| Stick X/Y | Left stick | Yaw / pitch |
| Twist | Right stick X | Roll |
| Throttle | Right stick Y | Throttle (see note) |
| POV hat | D-pad | Power management |
| Trigger | RB | Primary fire |
| Thumb button | A | Boost / ability |

**Throttle note:** Squadrons maps right-stick-Y as a *rate*, not an
absolute position. Pushing the lever gives a continuous increase; centring
it holds throttle where it is. This is different from a traditional HOTAS
but is how the game works with any controller. Test it on your first mission.

Mode 2 layer: same trigger fire + extra camera/menu controls on thumb buttons.

---

## Microsoft Flight Simulator 2024

Profile: `sender_config.msfs2024.json`

| X52 control | Xbox input | In-game action |
|---|---|---|
| Stick X/Y | Left stick | Roll / pitch |
| Twist | Right stick X | Rudder |
| Throttle | Right trigger (RT) | Throttle (absolute) |
| POV hat | D-pad | View / camera |
| Trigger | A | Confirm / interact |

**Critical in-game binding:** Go to Options → Controls → filter for
*Throttle* → bind **THROTTLE AXIS** to **Right Trigger**. Do NOT use the
default incremental *Throttle Up / Throttle Down* actions — those make the
lever feel wrong (rate-based). With THROTTLE AXIS bound to RT, lever position
maps 1:1 to engine power.

Rudder on twist: Options → Controls → filter for *Rudder* →
bind **RUDDER AXIS** to **Right Stick X**.

---

---

# Config reference

Config files are JSON. Both `oberon_server.py` and `hotas_sender.py` read
the same format.

```json
{
  "device_match": "X52",      ← substring of evdev device name
  "host": "10.42.0.1",        ← receiver IP (sender / UDP mode only)
  "port": 5555,               ← UDP port (sender / UDP mode only)
  "hat_to_dpad": true,        ← map POV hat to d-pad automatically

  "axes": {
    "ABS_X":  { "target": "lx", "invert": false, "deadzone": 0.05, "expo": 0.20 },
    "ABS_Y":  { "target": "ly", "invert": true,  "deadzone": 0.05, "expo": 0.20 },
    "ABS_RZ": { "target": "rx", "invert": false, "deadzone": 0.12, "expo": 0.30 },
    "ABS_Z":  { "target": "rt", "invert": true,  "deadzone": 0.00, "expo": 0.0  }
  },

  "buttons": {
    "mode1": { "BTN_TRIGGER": "rb", "BTN_BASE3": "select_mode1", ... },
    "mode2": { "BTN_TRIGGER": "rb", ... },
    "mode3": { ... }
  }
}
```

**Axis targets:** `lx  ly  rx  ry  lt  rt`

**Button targets:** `a  b  x  y  lb  rb  ls  rs  view  menu`
`dpad_up  dpad_down  dpad_left  dpad_right`
`lt_button  rt_button` (drives trigger to full deflection from a digital button)
`select_mode1  select_mode2  select_mode3` (activates mode layer)

**Axis tuning tips:**
- `deadzone`: fraction of axis travel to ignore at centre. 0.05–0.10 for stick,
  0.10–0.15 for twist, 0.0 for throttle.
- `expo`: 0 = linear, 1 = full cubic. 0.20–0.30 gives a soft centre feel.
- `invert`: flip the axis direction. Set true for ABS_Y (evdev 0 = stick up,
  but games expect +1 = up).

---

---

# Troubleshooting

**`ls /sys/class/udc` is empty after the overlay (OPi B).**
Check that `armbian-add-overlay` succeeded — it prints errors if DTS labels
don't resolve. Look in `/boot/armbianEnv.txt` for `user_overlays=opi-usbc-peripheral`.
Some H618 kernels also need `phy0_type` forced; consult the Armbian/DietPi forum
threads for "Orange Pi Zero 2W OTG peripheral" (same SoC).

**Controller drops after ~10 minutes (proxy mode).**
The Xbox re-challenges auth periodically. The proxy forwards this through the
real controller; a bad USB cable adds enough latency to fail. Use a short,
good-quality USB-C cable. Also check the proxy log for `STALL` on control
transfers.

**Receiver shows 0 pkt/s.**
Check: sender is running on OPi A (`systemctl status hotas-sender`), WiFi link
is up (`ping 10.42.0.1`), power save is off on both boards
(`iw dev wlan0 get power_save` → must say `off`).

**Inputs do nothing in-game (proxy mode).**
Run the proxy with `-v 1`. You should see `Injection[int EP81] before: ...
after: ...` lines when you move the stick. If not: wrong `ep_address` in
`injection.json` — read the correct EP from the proxy startup log and add it.
Also confirm the proxy was built with Lua: the build output must contain
`Lua scripting: enabled (lua5.4)`.

**Oberon: Xbox app connects but inputs are ignored.**
The game is blocking synthetic inputs. Try a different game to confirm the
chain works, then check if your target game has a known synthetic-input
restriction (common in competitive games with kernel anti-cheat). Switch to
Mode B for those titles.

**Oberon: "No device matching X52" on the Pi.**
The X52 may expose two evdev nodes (stick + keyboard MFD). Only the one with
ABS axes is the right one. Run `--list` to see all devices, then `--device
/dev/input/eventX` to pick the right node explicitly and add that path to the
service `ExecStart` line.

**Axes drift / twitchy at centre.**
Increase `deadzone` for that axis in the config (0.10–0.15 for twist). The X52
twist bearing is notoriously loose. You can also run `--probe` with the stick
centred and watch the printed values to confirm the resting range.

**Buttons land on wrong Xbox actions.**
Your specific controller revision may have slightly different GIP report
offsets (rare, seen on some Elite pads). In proxy mode: run with `-v 1`,
press a known button, watch which byte changes, and adjust the GIP offsets at
the top of `proxy/scripts/gip_hotas_merge.lua`.

---

## File map

```
hotas-bridge/
├── install.sh                     ← one-shot installer for each board
├── README.md                      ← this file
│
├── oberon/
│   ├── oberon_server.py           ← OPi A (or any single Pi), Oberon mode
│   ├── calibrate.py               ← auto-calibration wizard (detects your axes)
│   └── hotas-oberon.service       ← systemd unit for Oberon mode
│
├── sender/
│   ├── hotas_sender.py            ← OPi A, USB proxy mode
│   ├── hotas-sender.service       ← systemd unit
│   ├── sender_config.json         ← DEFAULT (= Squadrons profile)
│   └── profiles/
│       ├── sender_config.squadrons.json   ← same as the default
│       └── sender_config.msfs2024.json
│
├── receiver/
│   ├── hotas_receiver.py          ← OPi B, USB proxy mode
│   └── hotas-receiver.service     ← systemd unit
│
├── proxy/
│   ├── setup_opi_b.sh             ← builds usb-proxy with Lua on OPi B
│   ├── run_proxy.sh               ← runs usb-proxy with correct UDC args
│   ├── injection.json             ← binds Lua merge script to controller endpoints
│   ├── hotas-proxy.service        ← systemd unit
│   └── scripts/
│       └── gip_hotas_merge.lua    ← GIP input report rewriter
│
├── overlays/
│   └── opi-usbc-peripheral.dts    ← DTB overlay: USB-C → peripheral mode
│
└── tools/
    └── link_setup.sh              ← dedicated WiFi AP/client, power save off
```

---

## Auto-calibration (recommended for X52 Pro or any unit)

Different X52 variants enumerate axes under different codes. Instead of
guessing, run the wizard once. It detects which code each control uses,
learns each axis's real range and resting centre, leaves self-dithering
rotaries unmapped, and writes a ready-to-use config.

```bash
sudo python3 /opt/hotas-bridge/oberon/calibrate.py --game squadrons
```

Follow the prompts: it asks you to move the stick L/R, then fwd/back, then
twist, then the throttle, detecting each in turn. Add `--buttons` to also
walk through button mapping. It writes `sender/sender_config.calibrated.json`.

Then run the server against it:
```bash
sudo python3 /opt/hotas-bridge/oberon/oberon_server.py \
    --config /opt/hotas-bridge/sender/sender_config.calibrated.json
```

If a control feels wrong afterward, re-run the wizard, or open the JSON and
flip that axis's `invert`, or nudge its `deadzone`.

---

## Menu-suspend toggle (fixes the throttle scrolling the Xbox dashboard)

On the **Xbox dashboard**, the right trigger scrolls lists vertically. Because
throttle is mapped to the right trigger, a raised throttle scrolls the
dashboard on its own and you can't navigate. This is dashboard-only — in
Squadrons the trigger-as-throttle is exactly right and scrolls nothing.

Two ways to handle it:

1. **Quickest:** pull the throttle all the way DOWN before navigating the
   dashboard. At zero, the trigger is 0 and nothing scrolls. Launch the game,
   then throttle up in flight.

2. **Menu-suspend button (built in):** the default config sets
   `"suspend_button": "BTN_PINKIE"`. Press the pinkie button to FREEZE all
   axes (throttle and sticks report neutral) so you can navigate the dashboard
   with the hat/buttons; press it again to resume flying. Buttons and the
   d-pad keep working while suspended, so you can select and launch a game.

   Change which button by editing `suspend_button` in the config to any evdev
   button name (from `--probe`). Remove the line to disable the feature.
