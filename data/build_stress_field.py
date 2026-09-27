#!/usr/bin/env python3
"""
ImpactIQ — build a renderable stress field from Person 1's SOLIDWORKS node export.

Person 1's export is a per-node stress field for ONE study:
    NodeID, X, Y, Z, Stress_MPa      (coordinates in metres)

This can't train the impact->priority surrogate (that needs the *applied force*
per load case, which a stress export never contains). But it IS a real FEA result,
so we render it as a 3D stress heatmap on the actual upright geometry.

This script reduces the raw nodes to a compact JSON the dashboard viewer loads:
  - centres + scales the point cloud to unit size (keeps real bounds in metadata)
  - keeps EVERY high-stress node, random-samples the rest (importance sampling),
    so the hot spots survive the downsample
  - normalises |stress| to 0..1 for colouring (99th-percentile cap so one outlier
    node doesn't wash the whole model to blue)

Usage:
    python data/build_stress_field.py                       # data/upright_nodes.csv -> data/upright_field.json
    python data/build_stress_field.py --in <file> --max 40000
"""

import argparse
import csv
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_IN = HERE / "upright_nodes.csv"
DEFAULT_OUT = HERE / "upright_field.json"

# recognise columns case-insensitively
X_HINTS = ["x"]
Y_HINTS = ["y"]
Z_HINTS = ["z"]
STRESS_HINTS = ["von", "vonmises", "von_mises", "stress_mpa", "stress", "vms", "sigma"]


def _pick(header, hints, exclude=()):
    low = [h.strip().lower() for h in header]
    # exact match first (so "x" doesn't match "index")
    for i, name in enumerate(low):
        if name in hints and i not in exclude:
            return i
    # then contains
    for hint in hints:
        for i, name in enumerate(low):
            if hint in name and i not in exclude:
                return i
    return None


def load(path: Path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            sep = csv.Sniffer().sniff(sample).delimiter
        except Exception:
            sep = ","
        r = csv.reader(f, delimiter=sep)
        header = next(r)
        xi = _pick(header, X_HINTS)
        yi = _pick(header, Y_HINTS)
        zi = _pick(header, Z_HINTS)
        si = _pick(header, STRESS_HINTS, exclude={xi, yi, zi})
        if None in (xi, yi, zi, si):
            raise SystemExit(
                f"Could not find X/Y/Z/stress columns in header {header}.\n"
                f"  Need coordinates + stress, e.g. NodeID,X,Y,Z,Stress_MPa.")
        xs, ys, zs, ss = [], [], [], []
        for row in r:
            if len(row) <= max(xi, yi, zi, si):
                continue
            try:
                x = float(row[xi]); y = float(row[yi]); z = float(row[zi]); s = float(row[si])
            except ValueError:
                continue
            xs.append(x); ys.append(y); zs.append(z); ss.append(s)
    return xs, ys, zs, ss, header[si].strip()


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(math.floor(k)); hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def build(in_path: Path, out_path: Path, max_points: int, seed: int = 7):
    xs, ys, zs, ss = load(in_path)[:4]
    n = len(xs)
    if n == 0:
        raise SystemExit(f"No numeric rows parsed from {in_path}.")

    # von Mises is >=0; these exports are a signed component, so colour by magnitude
    mag = [abs(v) for v in ss]
    peak = max(mag)
    peak_i = mag.index(peak)
    cap = percentile(sorted(mag), 0.99) or peak  # colour scale cap

    # centre + scale to unit box (preserve real bounds for the caption)
    cx = sum(xs) / n; cy = sum(ys) / n; cz = sum(zs) / n
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    minz, maxz = min(zs), max(zs)
    span = max(maxx - minx, maxy - miny, maxz - minz) or 1.0
    scale = 2.0 / span  # fit roughly into [-1, 1]

    # importance sampling: keep the hottest nodes, sample the rest
    random.seed(seed)
    idx = list(range(n))
    if n > max_points:
        hot_keep = min(max_points // 3, n)
        hot = sorted(idx, key=lambda i: mag[i], reverse=True)[:hot_keep]
        hot_set = set(hot)
        rest = [i for i in idx if i not in hot_set]
        rest_keep = max(0, max_points - len(hot))
        rest_sample = random.sample(rest, min(rest_keep, len(rest)))
        keep = hot + rest_sample
    else:
        keep = idx
    if peak_i not in keep:
        keep.append(peak_i)

    positions, stress = [], []
    for i in keep:
        positions.append(round((xs[i] - cx) * scale, 4))
        positions.append(round((ys[i] - cy) * scale, 4))
        positions.append(round((zs[i] - cz) * scale, 4))
        stress.append(round(min(1.0, mag[i] / cap), 4))  # 0..1 for colouring

    peak_pos = [round((xs[peak_i] - cx) * scale, 4),
                round((ys[peak_i] - cy) * scale, 4),
                round((zs[peak_i] - cz) * scale, 4)]

    out = {
        "meta": {
            "source": in_path.name,
            "n_total": n,
            "n_rendered": len(stress),
            "peak_stress_MPa": round(peak, 3),
            "colour_cap_MPa": round(cap, 3),
            "bounds_mm": {
                "x": [round(minx * 1000, 2), round(maxx * 1000, 2)],
                "y": [round(miny * 1000, 2), round(maxy * 1000, 2)],
                "z": [round(minz * 1000, 2), round(maxz * 1000, 2)],
            },
            "note": "Colour = |stress| normalised to the 99th percentile. Values are a "
                    "signed stress component from the SOLIDWORKS export, not von Mises.",
        },
        "peak_pos": peak_pos,
        "positions": positions,   # flat [x,y,z, x,y,z, ...]
        "stress": stress,         # 0..1 per point
    }
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024
    print(f"Read {n:,} nodes from {in_path.name}")
    print(f"  peak |stress| = {peak:.2f} MPa  (colour cap {cap:.2f} MPa @ 99th pct)")
    print(f"  bounds: X {out['meta']['bounds_mm']['x']}  Y {out['meta']['bounds_mm']['y']}  "
          f"Z {out['meta']['bounds_mm']['z']}  (mm)")
    print(f"  rendered {len(stress):,} points -> {out_path.name} ({size_kb:.0f} KB)")
    print("\nOpen dashboard/index.html — the 'Real FEA Stress Field' panel loads this.")


def main():
    ap = argparse.ArgumentParser(description="Build a 3D stress field JSON from a node export.")
    ap.add_argument("--in", dest="inp", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max", type=int, default=40000, help="max points to render (default 40000)")
    args = ap.parse_args()
    if not args.inp.exists():
        raise SystemExit(
            f"Input not found: {args.inp}\n"
            f"  Save Person 1's coordinate export (NodeID,X,Y,Z,Stress_MPa) there.")
    build(args.inp, args.out, args.max)


if __name__ == "__main__":
    main()
