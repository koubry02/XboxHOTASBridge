"""
X52 Pro MFD (multi-function display) status output.

Uses libx52's `x52cli` (https://github.com/nirenjan/libx52) to write the three
16-character text lines on the throttle's display. Everything here is optional
and best-effort: if x52cli isn't installed, all calls become silent no-ops so
the bridge runs exactly as before.

Install libx52 on the Pi:
    sudo apt-add-repository ppa:nirenjan/libx52
    sudo apt update
    sudo apt install x52pro-linux      # provides the x52cli binary
    # (or build from source per the repo's INSTALL.md)

Display layout (3 lines x 16 chars):
    line 0:  <Pi IP address>          e.g. 192.168.1.69
    line 1:  XBOX:<ON/--> <ping>ms    e.g. XBOX:ON   45ms
    line 2:  MENU:<ON/OFF>            e.g. MENU:ON
"""
import shutil
import subprocess
import threading
import time

# Resolve x52cli once. None means "not installed" -> no-op everywhere.
_X52CLI = shutil.which("x52cli")

# libx52's CLI addresses the three MFD lines as 0, 1, 2.
_LINE_LEN = 16


def available():
    return _X52CLI is not None


def _set_line(line_no, text):
    """Write one MFD line. Best-effort; never raises into the caller."""
    if _X52CLI is None:
        return
    text = (text or "")[:_LINE_LEN]
    try:
        subprocess.run(
            [_X52CLI, "mfd", "text", str(line_no), text],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        )
    except Exception:
        pass  # display is cosmetic; input must never be affected


class MFDStatus:
    """
    Tracks bridge status and pushes it to the MFD. Thread-safe. A background
    thread refreshes the display ~2x/sec so the ping and connection state stay
    current without the caller having to redraw on every packet.
    """

    def __init__(self, ip="", refresh_hz=2.0):
        self._lock = threading.Lock()
        self._ip = ip
        self._connected = False
        self._ping_ms = None
        self._menu = False
        self._last = None  # last rendered tuple, to skip redundant writes
        self._stop = False
        self._enabled = available()
        if self._enabled:
            t = threading.Thread(target=self._loop, args=(refresh_hz,), daemon=True)
            t.start()

    # ---- state setters (called from the server) ----
    def set_ip(self, ip):
        with self._lock:
            self._ip = ip

    def set_connected(self, connected):
        with self._lock:
            self._connected = connected
            if not connected:
                self._ping_ms = None

    def set_ping(self, ping_ms):
        with self._lock:
            self._ping_ms = ping_ms

    def set_menu(self, menu_on):
        with self._lock:
            self._menu = menu_on

    def stop(self):
        self._stop = True

    # ---- rendering ----
    def _render(self):
        with self._lock:
            ip = self._ip or "no IP"
            if self._connected:
                p = f"{int(self._ping_ms)}ms" if self._ping_ms is not None else "--ms"
                l1 = f"XBOX:ON {p:>7}"[:_LINE_LEN]
            else:
                l1 = "XBOX:--  waiting"
            l2 = f"MENU:{'ON' if self._menu else 'OFF'}"
        return (ip[:_LINE_LEN], l1, l2)

    def _loop(self, hz):
        # Give the device a moment to settle, then refresh on a cadence.
        period = 1.0 / max(0.5, hz)
        while not self._stop:
            lines = self._render()
            if lines != self._last:
                for i, txt in enumerate(lines):
                    _set_line(i, txt)
                self._last = lines
            time.sleep(period)
