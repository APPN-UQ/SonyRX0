"""Fire both RX0 IIs through the Arduino trigger box (see rx0_trigger/rx0_trigger.ino).

Examples:
    python trigger/trigger.py --list                 # find the COM port
    python trigger/trigger.py --port COM5            # one shot
    python trigger/trigger.py --port COM5 -n 30 --interval 3   # 30 shots, 3 s apart
"""
import argparse
import time

import serial
from serial.tools import list_ports


class Trigger:
    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud, timeout=3)
        time.sleep(2)  # most Arduinos reset when the port opens
        self.ser.reset_input_buffer()
        self.ser.write(b"P\n")
        if self.ser.readline().strip() != b"RX0TRIG":
            raise RuntimeError(f"No trigger box answering on {port}")

    def fire(self):
        self.ser.write(b"S\n")
        reply = self.ser.readline().decode().strip()
        if not reply.startswith("OK"):
            raise RuntimeError(f"Trigger box did not confirm: {reply!r}")
        return reply

    def close(self):
        self.ser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("-n", type=int, default=1, help="number of shots")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="seconds between shots (leave time for both cameras to write to card)")
    args = ap.parse_args()

    if args.list or not args.port:
        for p in list_ports.comports():
            print(f"{p.device}: {p.description}")
        return

    trig = Trigger(args.port)
    try:
        for i in range(args.n):
            if i:
                time.sleep(args.interval)
            print(f"shot {i + 1}/{args.n}: {trig.fire()}")
    finally:
        trig.close()


if __name__ == "__main__":
    main()
