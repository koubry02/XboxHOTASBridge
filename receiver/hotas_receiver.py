#!/usr/bin/env python3
"""
hotas_receiver.py — runs on OPi B (the board plugged into the Xbox).

Listens for the sender's UDP stream, validates each 40-byte record and
publishes the latest one to /dev/shm/hotas_state, where the usb-proxy Lua
transform (gip_hotas_merge.lua) picks it up on every controller input report.

The record carries its sequence number at both head (offset 4) and tail
(offset 24); the Lua side treats a mismatch as a torn read and keeps its
previous state, so no locking is needed across the two processes.

Usage:
    python3 hotas_receiver.py               # listen on 0.0.0.0:5555
    python3 hotas_receiver.py --port 5555 --state /dev/shm/hotas_state
"""

import argparse
import os
import socket
import struct
import time

MAGIC = b"HB01"
RECORD_SIZE = 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--state", default="/dev/shm/hotas_state")
    ap.add_argument("--stats", action="store_true", help="print packet rate once per second")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    sock.bind((args.bind, args.port))

    # Pre-create the state file so the Lua side never sees a partial file.
    fd = os.open(args.state, os.O_RDWR | os.O_CREAT, 0o644)
    os.pwrite(fd, b"\x00" * RECORD_SIZE, 0)

    last_seq = 0
    count = 0
    dropped = 0
    t_mark = time.monotonic()
    print(f"Listening on {args.bind}:{args.port} -> {args.state}")

    while True:
        pkt, addr = sock.recvfrom(256)
        if len(pkt) != RECORD_SIZE or pkt[:4] != MAGIC:
            continue
        (seq,) = struct.unpack_from("<I", pkt, 4)
        (seq2,) = struct.unpack_from("<I", pkt, 24)
        if seq != seq2:
            continue
        # UDP reordering guard: accept only forward progress (with wrap slack)
        if last_seq and 0 < (last_seq - seq) & 0xFFFFFFFF < 1000:
            dropped += 1
            continue
        last_seq = seq
        os.pwrite(fd, pkt, 0)
        count += 1

        if args.stats:
            now = time.monotonic()
            if now - t_mark >= 1.0:
                print(f"{count} pkt/s   out-of-order {dropped}   from {addr[0]}")
                count = 0
                dropped = 0
                t_mark = now


if __name__ == "__main__":
    main()
