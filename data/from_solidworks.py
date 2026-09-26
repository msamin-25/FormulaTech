#!/usr/bin/env python3
"""
ImpactIQ — SOLIDWORKS export adapter.

Turns Person 1's SOLIDWORKS output into `data/fea_dataset.csv` (the contract in
data/schema.md), so nothing downstream (train.py / predict.py / backend) changes.

WHY THIS EXISTS
---------------
Our surrogate model is NOT a Graph Neural Network. It maps ONE force vector per
load case to the peak structural response:

    [Fx, Fy, Fz]  ->  [max von_mises_stress_MPa, max_displacement_mm]

So we do NOT need per-node X/Y/Z coordinates. Person 1's VBA export lists stress
per mesh node; all we take from it is the *maximum* stress in each study. The
applied force is the study INPUT and is not in the stress export, so it comes
from a small manifest Person 1 fills in (data/load_cases.csv).

TWO WAYS TO USE IT
------------------
MODE A — you already have one summary row per load case (simplest):
    Fill data/load_cases.csv with Fx,Fy,Fz,von_mises_stress_MPa,max_displacement_mm
    per study, then:
        python data/from_solidworks.py --manifest data/load_cases.csv
    (This just validates and installs it as fea_dataset.csv.)

MODE B — you have per-node stress files from the VBA export (one CSV per study):
    Fill data/load_cases.csv with, per study: a `study` name that matches the
    node file (by name), Fx,Fy,Fz, and — because a stress plot has no
    displacement — max_displacement_mm read off the study's result summary.
    Then point at the folder of node files:
        python data/from_solidworks.py --manifest data/load_cases.csv \
            --nodes-dir data/solidworks_exports
    Each node file is reduced to its max von Mises stress and joined to the
    matching manifest row by `study`.

Stress units: node exports are often in Pa (values ~1e8). Pass --stress-units pa
to convert to MPa, or leave the default (auto) which warns if it looks like Pa.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DEFAULT_MANIFEST = HERE / "load_cases.csv"
DEFAULT_OUT = HERE / "fea_dataset.csv"

# Column name fragments we recognize in a per-node export, case-insensitive.
STRESS_HINTS = ["von", "vonmises", "von_mises", "von mises", "stress", "vms", "sigma"]
NODE_HINTS = ["node", "index", "id"]

SCHEMA_COLUMNS = ["Fx", "Fy", "Fz", "von_mises_stress_MPa", "max_displacement_mm"]


def _find_column(columns, hints):
    """Return the first column whose lowercased name contains any hint."""
    low = {c: c.strip().lower() for c in columns}
    for hint in hints:
        for orig, name in low.items():
            if hint in name:
                return orig
    return None


def _to_mpa(series: pd.Series, units: str, label: str) -> pd.Series:
    """Normalize a stress column to MPa. `units` is 'mpa', 'pa', or 'auto'."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if units == "pa":
        return s / 1e6
    if units == "mpa":
        return s
    # auto: SOLIDWORKS default is Pa; 250 MPa -> 2.5e8. If the max looks like Pa,
    # convert and warn; otherwise assume it's already MPa.
    if len(s) and s.abs().max() > 1e5:
        print(f"  [{label}] values look like Pa (max={s.abs().max():.3g}); "
              f"converting to MPa. Pass --stress-units mpa to override.")
        return s / 1e6
    return s


def max_stress_from_node_file(path: Path, units: str) -> float:
    """Read one per-node stress export and return the peak von Mises stress in MPa."""
    df = pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python",
                     skip_blank_lines=True)
    stress_col = _find_column(df.columns, STRESS_HINTS)
    if stress_col is None:
        # Fall back: the last numeric column that isn't the node index.
        node_col = _find_column(df.columns, NODE_HINTS)
        numeric = [c for c in df.columns
                   if c != node_col and pd.to_numeric(df[c], errors="coerce").notna().any()]
        if not numeric:
            raise ValueError(
                f"{path.name}: couldn't find a stress column. "
                f"Columns present: {list(df.columns)}")
        stress_col = numeric[-1]
        print(f"  [{path.name}] no obvious stress header; using column "
              f"'{stress_col}'.")
    mpa = _to_mpa(df[stress_col], units, path.name)
    if mpa.empty:
        raise ValueError(f"{path.name}: no numeric stress values in '{stress_col}'.")
    return float(mpa.max())


def load_manifest(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(
            f"Manifest not found: {path}\n"
            f"  Copy data/load_cases.csv (a template is committed) and fill it in.")
    df = pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python",
                     skip_blank_lines=True)
    df.columns = [c.strip() for c in df.columns]
    # Drop template comment/example rows (any row where Fx isn't numeric).
    if "Fx" in df.columns:
        df = df[pd.to_numeric(df["Fx"], errors="coerce").notna()].reset_index(drop=True)
    for req in ["Fx", "Fy", "Fz"]:
        if req not in df.columns:
            raise SystemExit(f"Manifest {path} is missing required column '{req}'. "
                             f"Found: {list(df.columns)}")
    return df


def build(manifest_path: Path, nodes_dir: Path | None, units: str,
          out_path: Path) -> pd.DataFrame:
    man = load_manifest(manifest_path)
    if man.empty:
        raise SystemExit(f"Manifest {manifest_path} has no data rows filled in yet.")

    rows = []
    for i, r in man.iterrows():
        fx, fy, fz = float(r["Fx"]), float(r["Fy"]), float(r["Fz"])

        # Stress: from a node file if we have one for this study, else from the
        # manifest's own von_mises_stress_MPa column (MODE A).
        stress = None
        if nodes_dir is not None and "study" in man.columns and pd.notna(r.get("study")):
            study = str(r["study"]).strip()
            matches = sorted(nodes_dir.glob(f"{study}*"))
            if not matches:
                matches = sorted(nodes_dir.glob(f"*{study}*"))
            if matches:
                stress = max_stress_from_node_file(matches[0], units)
                print(f"  study '{study}': peak stress {stress:.2f} MPa "
                      f"from {matches[0].name}")
            else:
                print(f"  study '{study}': WARNING no node file matched; "
                      f"falling back to manifest stress column.")
        if stress is None:
            if "von_mises_stress_MPa" not in man.columns or pd.isna(
                    r.get("von_mises_stress_MPa")):
                raise SystemExit(
                    f"Row {i}: no node file and no von_mises_stress_MPa in the "
                    f"manifest. Provide one or the other.")
            stress = float(r["von_mises_stress_MPa"])

        if "max_displacement_mm" not in man.columns or pd.isna(
                r.get("max_displacement_mm")):
            raise SystemExit(
                f"Row {i}: max_displacement_mm is required in the manifest "
                f"(a stress export doesn't contain displacement — read the peak "
                f"URES displacement off the study's result summary).")
        disp = float(r["max_displacement_mm"])

        rows.append([fx, fy, fz, round(stress, 4), round(disp, 6)])

    out = pd.DataFrame(rows, columns=SCHEMA_COLUMNS)
    out.to_csv(out_path, index=False)
    print(f"\nWrote {len(out)} load case(s) -> {out_path}")
    print(out.to_string(index=False))
    print("\nNext:  python model/train.py && python model/validate.py && "
          "python check_demo.py")
    return out


def main():
    ap = argparse.ArgumentParser(description="Convert SOLIDWORKS output to "
                                             "data/fea_dataset.csv")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                    help="load-case manifest (Fx,Fy,Fz + stress/displacement or study)")
    ap.add_argument("--nodes-dir", type=Path, default=None,
                    help="folder of per-node stress exports (MODE B)")
    ap.add_argument("--stress-units", choices=["auto", "mpa", "pa"], default="auto",
                    help="units of stress in node files (default: auto-detect)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="output dataset path (default: data/fea_dataset.csv)")
    args = ap.parse_args()

    if args.nodes_dir is not None and not args.nodes_dir.exists():
        raise SystemExit(f"--nodes-dir not found: {args.nodes_dir}")

    build(args.manifest, args.nodes_dir, args.stress_units, args.out)


if __name__ == "__main__":
    main()
