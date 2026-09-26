#!/usr/bin/env python3
"""
ImpactIQ — impact detector.

Reads IMU accelerometer samples (from Arduino over serial, or simulated with
--simulate for testing before hardware is ready), detects an impact event,
converts it into a representative Fx/Fy/Fz force vector, and POSTs it to the
backend at /impact.

EXPECTED SERIAL FORMAT FROM ARDUINO (give this to Person 3 verbatim)
---------------------------------------------------------------------
One line per sample, comma-separated acceleration in g's, in the FIXED axis
convention (mount the IMU so its axes line up with these, or remap in
firmware before printing):

    ax,ay,az

Example lines:
    0.01,-0.02,0.99
    0.02,0.01,1.01
    ...
    -2.31,4.02,3.87      <- impact event: big jump above baseline

Send at roughly 100-200 Hz (a plain `Serial.println(...)` in loop() at that
rate is fine). Nothing fancy needed — this script does the impact detection,
not the Arduino.

AXIS CONVENTION (must match Person 1's SOLIDWORKS setup exactly)
------------------------------------------------------------------
    ax -> Fx  (longitudinal)
    ay -> Fy  (lateral)
    az -> Fz  (vertical)

HOW DETECTION WORKS
--------------------
1. First N samples at startup are averaged as a baseline (mostly gravity,
   ~1g on whichever axis is "up" if the IMU sits still before the run).
2. Every new sample's deviation from baseline is computed. When the
   magnitude of that deviation exceeds --threshold-g, an impact is declared.
3. The peak deviation during a short capture window right after the trigger
   is used as the representative acceleration for that impact (impacts are
   brief; the peak, not the trigger-instant sample, is the representative
   loading condition).
4. Peak acceleration (g) * 9.81 * --mass (kg) = representative force (N) per
   axis. --mass is NOT the whole car's mass — it's an effective mass
   standing in for how much of the car's inertia loads the upright during a
   wheel impact. This is a simplification; document it as such on the
   methodology slide, same as Person 1's boundary conditions.
5. A cooldown period after each detected impact prevents one physical hit
   from re-triggering many events as it rings down.

USAGE
-----
    # simulate without any hardware connected (for testing/demo prep):
    python backend/impact_detector.py --simulate

    # real hardware:
    python backend/impact_detector.py --port /dev/ttyUSB0 --baud 115200
"""

import argparse
import random
import sys
import time
from collections import deque

import requests

DEFAULT_BACKEND_URL = "http://localhost:8000/impact"
G_TO_MS2 = 9.81


def parse_args():
    p = argparse.ArgumentParser(description="ImpactIQ impact detector")
    p.add_argument("--port", default="/dev/ttyUSB0", help="Serial port for the Arduino")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p.add_argument("--mass", type=float, default=8.0,
                    help="Effective mass (kg) for F=ma conversion. Tune this "
                         "with Person 1 to match the assumed loading scenario.")
    p.add_argument("--threshold-g", type=float, default=1.5,
                    help="Deviation-from-baseline magnitude (in g) that counts as an impact.")
    p.add_argument("--capture-window-s", type=float, default=0.15,
                    help="How long to watch for the peak after a trigger.")
    p.add_argument("--cooldown-s", type=float, default=1.0,
                    help="Minimum time between two separate impact events.")
    p.add_argument("--upright-id", default="front_left")
    p.add_argument("--simulate", action="store_true",
                    help="Generate fake IMU samples instead of reading serial "
                         "(no hardware needed — for building/demoing before "
                         "the Arduino side is wired up).")
    return p.parse_args()


def sample_source_serial(port, baud):
    import serial  # imported here so --simulate works without pyserial installed
    ser = serial.Serial(port, baud, timeout=1)
    print(f"Reading IMU samples from {port} @ {baud} baud...")
    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        try:
            ax, ay, az = (float(v) for v in line.split(","))
        except ValueError:
            continue  # skip malformed lines
        yield ax, ay, az


def sample_source_simulated():
    """Emits quiet baseline noise most of the time, with an occasional
    impact burst, so the rest of the pipeline can be built/demoed without
    waiting on hardware."""
    print("SIMULATED mode: generating fake IMU samples (no hardware). "
          "Impacts will fire roughly every 5-10 seconds.")
    t = 0
    next_impact_at = random.uniform(5, 10)
    while True:
        if t >= next_impact_at:
            # short burst of large acceleration in a random-ish direction
            burst_len = int(0.2 / 0.01)
            severity = random.choice(["mild", "moderate", "severe"])
            peak_g = {"mild": 2.0, "moderate": 4.5, "severe": 7.5}[severity]
            for _ in range(burst_len):
                yield (
                    random.uniform(-peak_g, peak_g),
                    random.uniform(-peak_g, peak_g),
                    1.0 + random.uniform(-peak_g, peak_g),
                )
                time.sleep(0.01)
            t = 0
            next_impact_at = random.uniform(5, 10)
        else:
            yield (random.uniform(-0.03, 0.03), random.uniform(-0.03, 0.03),
                   1.0 + random.uniform(-0.03, 0.03))
            time.sleep(0.01)
            t += 0.01


def calibrate_baseline(source, n=50):
    print(f"Calibrating baseline from {n} samples — keep the IMU still...")
    samples = []
    for _ in range(n):
        samples.append(next(source))
    bx = sum(s[0] for s in samples) / n
    by = sum(s[1] for s in samples) / n
    bz = sum(s[2] for s in samples) / n
    print(f"Baseline (g): ax={bx:.3f} ay={by:.3f} az={bz:.3f}")
    return bx, by, bz


def post_impact(backend_url, fx, fy, fz, upright_id, source_label):
    payload = {"Fx": fx, "Fy": fy, "Fz": fz, "upright_id": upright_id, "source": source_label}
    try:
        resp = requests.post(backend_url, json=payload, timeout=3)
        resp.raise_for_status()
        result = resp.json()
        print(f"  -> POSTed. Backend says: stress={result.get('predicted_von_mises_stress_MPa')}"
              f"MPa priority={result.get('priority')}")
    except requests.exceptions.RequestException as e:
        print(f"  -> WARNING: could not reach backend at {backend_url}: {e}")


def main():
    args = parse_args()

    if args.simulate:
        source = sample_source_simulated()
        source_label = "simulated"
    else:
        try:
            source = sample_source_serial(args.port, args.baud)
        except Exception as e:
            print(f"ERROR opening serial port {args.port}: {e}")
            print("Tip: use --simulate to test the rest of the pipeline "
                  "without hardware connected.")
            sys.exit(1)
        source_label = "arduino"

    bx, by, bz = calibrate_baseline(source)

    last_impact_time = 0
    print(f"Watching for impacts (threshold={args.threshold_g}g, "
          f"mass={args.mass}kg)...")

    while True:
        ax, ay, az = next(source)
        dx, dy, dz = ax - bx, ay - by, az - bz
        deviation_mag = (dx ** 2 + dy ** 2 + dz ** 2) ** 0.5

        now = time.time()
        if deviation_mag >= args.threshold_g and (now - last_impact_time) >= args.cooldown_s:
            print(f"[TRIGGER] deviation={deviation_mag:.2f}g — capturing peak...")
            window_end = now + args.capture_window_s
            peak = (dx, dy, dz)
            peak_mag = deviation_mag
            while time.time() < window_end:
                ax2, ay2, az2 = next(source)
                d2 = (ax2 - bx, ay2 - by, az2 - bz)
                mag2 = (d2[0] ** 2 + d2[1] ** 2 + d2[2] ** 2) ** 0.5
                if mag2 > peak_mag:
                    peak, peak_mag = d2, mag2

            fx = peak[0] * G_TO_MS2 * args.mass
            fy = peak[1] * G_TO_MS2 * args.mass
            fz = peak[2] * G_TO_MS2 * args.mass

            print(f"[IMPACT] peak={peak_mag:.2f}g -> Fx={fx:.1f}N Fy={fy:.1f}N Fz={fz:.1f}N")
            post_impact(args.backend_url, fx, fy, fz, args.upright_id, source_label)

            last_impact_time = time.time()


if __name__ == "__main__":
    main()
