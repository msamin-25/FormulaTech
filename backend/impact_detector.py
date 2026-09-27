#!/usr/bin/env python3
"""
ImpactIQ — impact detector (serial -> backend bridge).

Reads Person 3's Arduino (MPU-6500) over serial, turns each impact into a
representative Fx/Fy/Fz force vector plus the struck corner, and POSTs it to
the backend at /impact. The dashboard picks it up from there.

SERIAL FORMATS UNDERSTOOD
-------------------------
1. Person 3's sketch (arduino/impact_imu.ino) — the Arduino detects the impact
   itself and prints ONE line per hit, at 115200 baud:

       READY                                   (once, at boot — ignored)
       IMPACT,<DIRECTION>,<ax>,<ay>,<az>,<totalG>
       e.g. IMPACT,FRONT,0.42,-3.10,1.25,3.37

   Accelerations are in g (sensor range +/-16 g). DIRECTION is FRONT / BACK /
   LEFT / RIGHT from Person 3's calibration of the board's mounting.

2. Plain streaming "ax,ay,az" samples (~100-200 Hz). Detection then happens
   here: baseline calibration, threshold, peak capture, cooldown.

BOARD -> CAR AXES (Person 3's calibrated mounting)
--------------------------------------------------
    board Y-  = car FRONT       board X+ = car LEFT       board Z+ = up
    Fx (longitudinal, + forward) = -ay
    Fy (lateral,      + left)    =  ax
    Fz (vertical,     + up)      =  az - 1 g   (gravity removed; board mounted flat)

Corner: front if ay < 0 else rear; left if ax >= 0 else right. This follows
Person 3's FRONT/BACK/LEFT/RIGHT labels, so a hit lands on one wheel of the car.

FORCE
-----
Representative force = acceleration (g) * 9.81 * --mass. The default 200 kg is
a quarter of an ~800 kg F1 car: the share of the car's inertia one corner
carries. With it, ~2.5 g reads MODERATE and ~5 g reads HIGH on the current
model. It is the single tuning knob for the rig: state it on the methodology
slide next to Person 1's boundary conditions.

USAGE
-----
    python backend/impact_detector.py                     # auto-detect the Arduino port
    python backend/impact_detector.py --port /dev/cu.usbmodem1101
    python backend/impact_detector.py --simulate-arduino  # fake Person 3 output, no hardware
    python backend/impact_detector.py --simulate          # fake streaming samples, no hardware
    python backend/impact_detector.py --dry-run           # print forces, don't POST
"""

import argparse
import math
import random
import re
import sys
import time

import requests

DEFAULT_BACKEND_URL = "http://localhost:8000/impact"
G_TO_MS2 = 9.81
SENSOR_ERROR = re.compile(r"not found|not responding|could not read|failed", re.I)
PORT_HINTS = ("usbmodem", "usbserial", "wchusbserial", "ttyacm", "ttyusb", "arduino", "ch340", "cp210")


def parse_args():
    p = argparse.ArgumentParser(description="ImpactIQ impact detector")
    p.add_argument("--port", default="auto", help="Serial port, or 'auto' to find the Arduino")
    p.add_argument("--baud", type=int, default=115200, help="Must match Serial.begin() in the sketch (115200)")
    p.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    p.add_argument("--mass", type=float, default=200.0,
                   help="Effective mass (kg) for F = m*a. Default: quarter of an ~800 kg F1 car.")
    p.add_argument("--gravity-g", type=float, default=1.0,
                   help="Static reading on board Z to remove (1.0 when mounted flat; 0 to disable)")
    p.add_argument("--upright-id", default="auto",
                   help="Force every hit onto one corner (e.g. front_left); 'auto' derives it from the hit")
    p.add_argument("--threshold-g", type=float, default=1.5, help="[streaming mode] trigger threshold")
    p.add_argument("--capture-window-s", type=float, default=0.15, help="[streaming mode] peak window")
    p.add_argument("--cooldown-s", type=float, default=1.0, help="[streaming mode] min time between hits")
    p.add_argument("--simulate", action="store_true", help="Fake streaming ax,ay,az samples")
    p.add_argument("--simulate-arduino", action="store_true", help="Fake Person 3's IMPACT lines")
    p.add_argument("--dry-run", action="store_true", help="Print forces without POSTing")
    return p.parse_args()


# ---------------------------------------------------------------- mapping ---

def board_to_force(ax, ay, az_dev, mass):
    """Board-frame acceleration (g, gravity already removed) -> car-frame force (N)."""
    k = G_TO_MS2 * mass
    return -ay * k, ax * k, az_dev * k


def corner_from_board(ax, ay):
    return ("front" if ay < 0 else "rear") + "_" + ("left" if ax >= 0 else "right")


# ---------------------------------------------------------------- sources ---

def find_port():
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        text = f"{p.device} {p.description} {p.manufacturer or ''}".lower()
        if any(h in text for h in PORT_HINTS):
            return p.device
    listed = ", ".join(p.device for p in ports) or "none"
    raise RuntimeError(f"no Arduino-looking serial port found (ports: {listed}). "
                       f"Plug it in, or pass --port explicitly.")


def open_serial(port, baud):
    import serial  # imported here so the simulators work without pyserial
    if port == "auto":
        port = find_port()
    ser = serial.Serial(port, baud, timeout=1)
    print(f"Listening on {port} @ {baud} baud (Arduino resets on connect; wait for READY)...")
    return ser


def lines_from_serial(ser):
    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if line:
            yield line


def lines_simulated_arduino():
    """Emit exactly what Person 3's sketch prints, with a hit every 4-8 s."""
    print("SIMULATED Arduino: emitting Person 3's IMPACT lines (no hardware).")
    yield "READY"
    while True:
        time.sleep(random.uniform(4, 8))
        direction = random.choice(["FRONT", "BACK", "LEFT", "RIGHT"])
        g = random.choice([1.4, 3.2, 6.0]) * random.uniform(0.9, 1.1)
        main, side = g, g * random.uniform(0.1, 0.5)
        ax, ay = {
            "FRONT": (random.choice([-1, 1]) * side, -main),
            "BACK":  (random.choice([-1, 1]) * side, main),
            "LEFT":  (main, random.choice([-1, 1]) * side),
            "RIGHT": (-main, random.choice([-1, 1]) * side),
        }[direction]
        az = 1.0 + random.uniform(-0.6, 0.6)
        total = math.sqrt(ax * ax + ay * ay + az * az)
        yield f"IMPACT,{direction},{ax:.2f},{ay:.2f},{az:.2f},{total:.2f}"


def lines_simulated_stream():
    """Quiet noise with an occasional burst, as 'ax,ay,az' lines."""
    print("SIMULATED stream: fake ax,ay,az samples (no hardware); a hit every 5-10 s.")
    t, next_hit = 0.0, random.uniform(5, 10)
    while True:
        if t >= next_hit:
            peak = random.choice([1.4, 3.2, 6.0])
            for _ in range(20):
                yield f"{random.uniform(-peak, peak):.3f},{random.uniform(-peak, peak):.3f},{1 + random.uniform(-peak, peak):.3f}"
                time.sleep(0.01)
            t, next_hit = 0.0, random.uniform(5, 10)
        else:
            yield f"{random.uniform(-.03, .03):.3f},{random.uniform(-.03, .03):.3f},{1 + random.uniform(-.03, .03):.3f}"
            time.sleep(0.01)
            t += 0.01


# ------------------------------------------------------------------ output ---

def report(args, ax, ay, az_dev, label):
    fx, fy, fz = board_to_force(ax, ay, az_dev, args.mass)
    corner = corner_from_board(ax, ay) if args.upright_id == "auto" else args.upright_id
    mag_g = math.sqrt(ax * ax + ay * ay + az_dev * az_dev)
    print(f"[IMPACT] {label:<14} {mag_g:5.2f} g -> {corner:<11} "
          f"Fx={fx:8.0f} N  Fy={fy:8.0f} N  Fz={fz:8.0f} N")
    if args.dry_run:
        return
    payload = {"Fx": fx, "Fy": fy, "Fz": fz, "upright_id": corner, "source": label}
    try:
        r = requests.post(args.backend_url, json=payload, timeout=3)
        r.raise_for_status()
        res = r.json()
        print(f"         -> {res.get('priority')} ({res.get('predicted_von_mises_stress_MPa')} MPa)"
              + ("  [outside trained envelope]" if res.get("in_envelope") is False else ""))
    except requests.exceptions.RequestException as e:
        print(f"         -> WARNING: backend unreachable at {args.backend_url}: {e}")


def parse_impact_line(line):
    """'IMPACT,FRONT,ax,ay,az,totalG' -> (direction, ax, ay, az) or None."""
    parts = line.split(",")
    if len(parts) < 5 or parts[0].strip().upper() != "IMPACT":
        return None
    try:
        return parts[1].strip().upper(), float(parts[2]), float(parts[3]), float(parts[4])
    except ValueError:
        return None


def parse_sample_line(line):
    try:
        ax, ay, az = (float(v) for v in line.split(","))
        return ax, ay, az
    except ValueError:
        return None


# -------------------------------------------------------------------- main ---

def run(args, lines, source_name):
    baseline, calib = None, []
    last_hit = 0.0
    print(f"Watching for impacts (mass={args.mass:g} kg, gravity={args.gravity_g:g} g, "
          f"corner={'auto' if args.upright_id == 'auto' else args.upright_id}).")
    for line in lines:
        if line.upper() == "READY":
            print("Arduino says READY.")
            continue

        hit = parse_impact_line(line)
        if hit:  # Person 3's format: the Arduino already detected the impact
            direction, ax, ay, az = hit
            report(args, ax, ay, az - args.gravity_g, f"{source_name}:{direction.lower()}")
            continue

        sample = parse_sample_line(line)
        if sample is None:
            if SENSOR_ERROR.search(line):  # the sketch halts after printing these
                print(f"ARDUINO ERROR: {line}  (check the MPU-6500 wiring, then reset the board)")
            continue  # debug prints or partial lines

        # streaming format: detect here
        if baseline is None:
            calib.append(sample)
            if len(calib) == 50:
                baseline = tuple(sum(s[i] for s in calib) / 50 for i in range(3))
                print("Baseline (g): ax={:.3f} ay={:.3f} az={:.3f}".format(*baseline))
            continue
        d = [sample[i] - baseline[i] for i in range(3)]
        mag = math.sqrt(sum(v * v for v in d))
        now = time.time()
        if mag < args.threshold_g or now - last_hit < args.cooldown_s:
            continue
        peak, peak_mag, end = d, mag, now + args.capture_window_s
        for more in lines:
            s = parse_sample_line(more)
            if s:
                d2 = [s[i] - baseline[i] for i in range(3)]
                m2 = math.sqrt(sum(v * v for v in d2))
                if m2 > peak_mag:
                    peak, peak_mag = d2, m2
            if time.time() >= end:
                break
        report(args, peak[0], peak[1], peak[2], source_name)
        last_hit = time.time()


def main():
    args = parse_args()
    if args.simulate_arduino:
        lines, name = lines_simulated_arduino(), "simulated"
    elif args.simulate:
        lines, name = lines_simulated_stream(), "simulated"
    else:
        try:
            lines, name = lines_from_serial(open_serial(args.port, args.baud)), "arduino"
        except Exception as e:
            print(f"ERROR opening serial port: {e}")
            print("Tip: --simulate-arduino runs the whole pipeline without hardware.")
            sys.exit(1)
    try:
        run(args, lines, name)
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:  # e.g. cable pulled mid-demo
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
