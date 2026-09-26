#!/usr/bin/env python3
"""
ImpactIQ — synthetic FEA dataset generator.

PURPOSE
-------
Person 1 (SOLIDWORKS) needs time to build the upright, apply fixtures/forces,
and run studies. Person 2 (this pipeline) can't sit idle waiting for that.

This script generates a physically-motivated *placeholder* dataset with the
exact schema described in data/schema.md, using simplified cantilever-beam
mechanics as a stand-in for the real FEA. It is NOT a substitute for real
simulation results — it exists so the ML model, backend, and dashboard can be
built, trained, and demoed *today*. The moment Person 1 exports a real
fea_dataset.csv with the same 5 columns, re-run model/train.py and the whole
pipeline switches to real data with zero code changes.

MODEL (simplified upright as cantilever from wheel center to bearing)
-----------------------------------------------------------------------
- Treat the upright as a cantilevered beam of length L from the wheel-force
  application point to the bearing/hub constraint.
- Bending stress from each force component: sigma_i = (F_i * L) * c / I
- Combine components with a von-Mises-like combination (not exact FEA, but
  captures that off-axis loading interacts nonlinearly with stress).
- Deflection per component via cantilever beam formula: delta_i = F_i*L^3/(3*E*I)
- Combine deflections as vector magnitude.
- Add multiplicative noise (~8%) to emulate real-world/FEA scatter so the
  regression model doesn't overfit to a perfectly clean analytical surface.

Geometry/material constants below are reasonable placeholders for a small
formula-style upright. Person 1: once you have real numbers (arm length,
section modulus, material), update the CONFIG block — even without a full
FEA export, better constants here immediately improve the placeholder.
"""

import csv
import json
import math
import random
from pathlib import Path

random.seed(42)

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# CONFIG — placeholder upright geometry. Update when Person 1 has real specs.
# ---------------------------------------------------------------------------
L_ARM_M = 0.09          # effective moment arm, wheel-force to bearing (m)
SECTION_C_M = 0.012     # distance from neutral axis to outer fiber (m)
SECTION_I_M4 = 2.6e-8   # second moment of area of the critical section (m^4)
                        # (tuned, post magnitude-combination fix, so the
                        # combined stress spans roughly 10-100% of yield
                        # across the sampled force range -> real LOW/
                        # MODERATE/HIGH spread instead of everything one
                        # bucket)
NOISE_FRAC = 0.08       # +/- fractional noise to emulate FEA/real-world scatter

MATERIAL_PATH = HERE / "material.json"
with open(MATERIAL_PATH) as f:
    material = json.load(f)
E_PA = material["elastic_modulus_GPa"] * 1e9

# ---------------------------------------------------------------------------
# Force sampling ranges (N). Covers "nothing happened" through "hard impact".
# Representative wheel-impact loads for a small formula-style car upright.
# ---------------------------------------------------------------------------
F_RANGE = (-6000, 6000)
N_SAMPLES = 400


def noisy(x, frac=NOISE_FRAC):
    return x * (1 + random.uniform(-frac, frac))


def stress_and_disp(fx, fy, fz):
    # Bending moment contribution per axis -> stress (Pa) -> MPa
    sigma_x = abs(fx) * L_ARM_M * SECTION_C_M / SECTION_I_M4
    sigma_y = abs(fy) * L_ARM_M * SECTION_C_M / SECTION_I_M4
    sigma_z = abs(fz) * L_ARM_M * SECTION_C_M / SECTION_I_M4

    # Combine the three bending stress contributions by magnitude.
    # NOTE: a true von-Mises pairwise-subtraction formula (sqrt(a^2+b^2+c^2
    # - ab-bc-ca)) is for combining *principal* stresses, not independent
    # abs()'d bending contributions from three axes — using it here made
    # stress cancel toward zero for balanced loads (harder impact ->
    # LOWER predicted stress, which is physically backwards). Plain
    # magnitude combination is monotonic in load, which is what a triage
    # surrogate needs.
    von_mises_pa = math.sqrt(sigma_x ** 2 + sigma_y ** 2 + sigma_z ** 2)
    von_mises_mpa = von_mises_pa / 1e6

    # Cantilever tip deflection per axis (m) -> combine as vector magnitude -> mm
    delta_x = abs(fx) * L_ARM_M ** 3 / (3 * E_PA * SECTION_I_M4)
    delta_y = abs(fy) * L_ARM_M ** 3 / (3 * E_PA * SECTION_I_M4)
    delta_z = abs(fz) * L_ARM_M ** 3 / (3 * E_PA * SECTION_I_M4)
    disp_mm = math.sqrt(delta_x ** 2 + delta_y ** 2 + delta_z ** 2) * 1000

    return noisy(von_mises_mpa), noisy(disp_mm)


def main():
    rows = []
    for _ in range(N_SAMPLES):
        fx = random.uniform(*F_RANGE)
        fy = random.uniform(*F_RANGE)
        fz = random.uniform(*F_RANGE)
        stress_mpa, disp_mm = stress_and_disp(fx, fy, fz)
        rows.append([round(fx, 2), round(fy, 2), round(fz, 2),
                     round(stress_mpa, 3), round(disp_mm, 4)])

    out_path = HERE / "fea_dataset.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Fx", "Fy", "Fz", "von_mises_stress_MPa", "max_displacement_mm"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} synthetic rows -> {out_path}")
    print(f"Material: {material['material_name']} "
          f"(yield {material['yield_strength_MPa']} MPa)")
    print("This is PLACEHOLDER data. Replace data/fea_dataset.csv with a real")
    print("SOLIDWORKS export (same 5 columns) and re-run model/train.py.")


if __name__ == "__main__":
    main()
