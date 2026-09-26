"""
Shared FEA-dataset loader + validator.

This is the single choke point where a real SOLIDWORKS export (or the synthetic
placeholder) enters the pipeline. Both model/train.py and model/validate.py go
through here, so a messy real-world CSV fails with a clear, actionable message
instead of a cryptic pandas/sklearn traceback halfway through training.

Data contract (see data/schema.md — do NOT change mid-hackathon):
    Fx, Fy, Fz, von_mises_stress_MPa, max_displacement_mm   (all numeric)

Forgiving about (things real exports actually do):
  - header case / surrounding whitespace / a UTF-8 BOM
  - extra columns (ignored, but reported)
  - comma OR semicolon OR tab delimiter (auto-detected)
  - blank lines and fully-empty rows

Strict about (things that would silently corrupt the model):
  - a required column being absent (won't guess which column meant what)
  - non-numeric / NaN cells in required columns (row dropped + reported)
  - too few usable rows to train
"""

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["Fx", "Fy", "Fz", "von_mises_stress_MPa", "max_displacement_mm"]
FEATURE_COLUMNS = ["Fx", "Fy", "Fz"]
TARGET_COLUMNS = ["von_mises_stress_MPa", "max_displacement_mm"]
MIN_USABLE_ROWS = 5  # below this, a regression is meaningless


class DatasetError(ValueError):
    """Raised when the dataset can't be used. Message is meant to be shown
    directly to the user (Person 1's export is probably the thing to fix)."""


def _read_csv_any_delimiter(path: Path) -> pd.DataFrame:
    """Read a CSV, tolerating comma / semicolon / tab delimiters and a BOM."""
    # utf-8-sig transparently strips a BOM if present (Excel/SOLIDWORKS exports).
    df = pd.read_csv(path, encoding="utf-8-sig", skip_blank_lines=True)
    if df.shape[1] == 1:
        # Everything landed in one column -> wrong delimiter guess. Let pandas
        # sniff the real one.
        df = pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python",
                         skip_blank_lines=True)
    return df


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Match columns to the canonical schema names ignoring case/whitespace.
    Does NOT do fuzzy/semantic remapping — that would risk silently mislabeling
    stress vs displacement. It only forgives formatting differences."""
    cleaned = {}
    lookup = {c.strip().lower(): c for c in df.columns}
    for canonical in REQUIRED_COLUMNS:
        key = canonical.strip().lower()
        if key in lookup:
            cleaned[lookup[key]] = canonical
    df = df.rename(columns=cleaned)
    return df


def load_fea_dataset(path, verbose: bool = True) -> pd.DataFrame:
    """Load, validate and clean the FEA dataset. Returns a DataFrame with
    exactly the REQUIRED_COLUMNS, all numeric, no NaNs. Raises DatasetError
    with a human-readable message on any unrecoverable problem."""
    path = Path(path)
    if not path.exists():
        raise DatasetError(
            f"Dataset not found: {path}\n"
            f"  -> Generate placeholder data: python data/generate_synthetic_data.py\n"
            f"  -> Or drop Person 1's real SOLIDWORKS export in as {path}"
        )

    try:
        df = _read_csv_any_delimiter(path)
    except Exception as e:  # noqa: BLE001 - surface anything as a clean message
        raise DatasetError(f"Could not parse {path} as CSV: {e}")

    if df.empty:
        raise DatasetError(f"{path} has a header but no data rows.")

    df = _canonicalize_columns(df)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        found = list(df.columns)
        raise DatasetError(
            f"{path} is missing required column(s): {missing}\n"
            f"  Found columns: {found}\n"
            f"  Expected (case/whitespace-insensitive): {REQUIRED_COLUMNS}\n"
            f"  See data/schema.md — this is the contract with Person 1's export."
        )

    extra = [c for c in df.columns if c not in REQUIRED_COLUMNS]
    df = df[REQUIRED_COLUMNS].copy()

    n_raw = len(df)

    # Coerce to numeric; anything non-numeric (stray text, units like "12 MPa")
    # becomes NaN so we can drop and report it rather than crash in sklearn.
    for col in REQUIRED_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    bad_mask = df[REQUIRED_COLUMNS].isna().any(axis=1)
    n_bad = int(bad_mask.sum())
    df = df[~bad_mask].reset_index(drop=True)

    if verbose:
        print(f"[dataset] loaded {n_raw} row(s) from {path}")
        if extra:
            print(f"[dataset] ignoring {len(extra)} extra column(s): {extra}")
        if n_bad:
            print(f"[dataset] dropped {n_bad} row(s) with non-numeric/empty cells")

    if len(df) < MIN_USABLE_ROWS:
        raise DatasetError(
            f"Only {len(df)} usable row(s) after cleaning (need >= {MIN_USABLE_ROWS}).\n"
            f"  Check {path} for empty/garbled rows or a wrong delimiter."
        )

    # Sanity flags — not fatal, but worth shouting about before a demo.
    if verbose:
        if (df[TARGET_COLUMNS] < 0).any().any():
            print("[dataset] WARNING: negative stress/displacement value(s) present "
                  "— FEA magnitudes should be >= 0. Check the export.")
        for col in TARGET_COLUMNS:
            if df[col].nunique() == 1:
                print(f"[dataset] WARNING: '{col}' is constant across all rows "
                      f"— the model can't learn anything for it.")
        if (df[FEATURE_COLUMNS].abs().sum(axis=1) == 0).all():
            print("[dataset] WARNING: all force inputs are zero.")

    return df


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "data/fea_dataset.csv"
    try:
        d = load_fea_dataset(p)
        print(f"\nOK: {len(d)} usable rows.")
        print(d.describe().round(2).to_string())
    except DatasetError as err:
        print(f"\nDATASET ERROR:\n{err}")
        sys.exit(1)
