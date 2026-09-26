#!/usr/bin/env python3
"""
ImpactIQ — train the ML surrogate model.

Reads data/fea_dataset.csv (synthetic today, real SOLIDWORKS export later —
same schema, zero code changes needed) and trains a model that predicts
[von_mises_stress_MPa, max_displacement_mm] from [Fx, Fy, Fz].

MODEL CHOICE — auto-adapts to how much data you actually have:
  - >= 50 rows (synthetic data today): RandomForestRegressor. Enough samples
    to learn nonlinear interactions without overfitting.
  - < 50 rows (a real SOLIDWORKS export is often just 8-20 hand-picked load
    cases): Ridge regression on the same engineered features. A forest with
    12 training rows memorizes noise and can't extrapolate past the load
    cases you happened to simulate; a low-variance linear model on physically
    sensible features (magnitude, squared terms) degrades much more gracefully.

Run this again any time data/fea_dataset.csv is replaced with real FEA data.
"""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor

sys.path.insert(0, str(Path(__file__).parent))
from features import build_feature_matrix, FEATURE_NAMES  # noqa: E402
from dataset import load_fea_dataset, DatasetError  # noqa: E402

HERE = Path(__file__).parent
DATA_CSV = HERE.parent / "data" / "fea_dataset.csv"
MATERIAL_JSON = HERE.parent / "data" / "material.json"
MODEL_PATH = HERE / "surrogate_model.joblib"
METADATA_PATH = HERE / "metadata.json"

SMALL_DATA_THRESHOLD = 50  # below this row count, prefer Ridge over RF


def build_model(n: int):
    """Pick the estimator based on how much data we have. Shared by train.py
    and validate.py so the validation plot reflects the model you actually ship.

    Returns (estimator, model_type_str).
    """
    if n < SMALL_DATA_THRESHOLD:
        return MultiOutputRegressor(Ridge(alpha=1.0)), "ridge"
    return (
        MultiOutputRegressor(
            RandomForestRegressor(n_estimators=150, max_depth=8, random_state=42)
        ),
        "random_forest",
    )


def priority_thresholds(yield_mpa: float):
    """LOW/MODERATE/HIGH cut points as a fraction of material yield strength.
    Standard-ish engineering judgment call for a hackathon triage tool:
    document this on the methodology slide.
    """
    return {
        "moderate_at_fraction_of_yield": 0.40,
        "high_at_fraction_of_yield": 0.75,
        "moderate_stress_MPa": round(0.40 * yield_mpa, 2),
        "high_stress_MPa": round(0.75 * yield_mpa, 2),
    }


def main():
    try:
        df = load_fea_dataset(DATA_CSV)
    except DatasetError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    n = len(df)

    X = build_feature_matrix(df["Fx"], df["Fy"], df["Fz"])
    y = df[["von_mises_stress_MPa", "max_displacement_mm"]].values

    # Record the force envelope the model was actually trained on, so predict.py
    # can flag inputs that fall outside it (a tree/regression extrapolated past
    # its training range is not trustworthy — say so rather than hide it).
    magnitudes = np.sqrt(df["Fx"] ** 2 + df["Fy"] ** 2 + df["Fz"] ** 2)
    training_envelope = {
        "Fx": [float(df["Fx"].min()), float(df["Fx"].max())],
        "Fy": [float(df["Fy"].min()), float(df["Fy"].max())],
        "Fz": [float(df["Fz"].min()), float(df["Fz"].max())],
        "magnitude_max": float(magnitudes.max()),
    }

    model, model_type = build_model(n)
    if model_type == "ridge":
        print(f"n={n} < {SMALL_DATA_THRESHOLD} -> using Ridge regression "
              f"(small real-FEA-dataset regime).")
        test_size = max(0.15, 3 / n) if n > 6 else 0.0
    else:
        print(f"n={n} >= {SMALL_DATA_THRESHOLD} -> using RandomForest "
              f"(synthetic-data regime).")
        test_size = 0.2

    if test_size > 0 and n * test_size >= 2:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42
        )
    else:
        X_train, y_train = X, y
        X_test, y_test = X, y  # too little data to hold out; report train fit only
        print("WARNING: dataset too small for a real train/test split — "
              "metrics below are training-fit only, not generalization.")

    model.fit(X_train, y_train)
    preds = model.predict(X_test)

    stress_mae = mean_absolute_error(y_test[:, 0], preds[:, 0])
    disp_mae = mean_absolute_error(y_test[:, 1], preds[:, 1])
    stress_r2 = r2_score(y_test[:, 0], preds[:, 0]) if len(y_test) >= 2 else float("nan")
    disp_r2 = r2_score(y_test[:, 1], preds[:, 1]) if len(y_test) >= 2 else float("nan")

    # MAPE — mean absolute % error, the metric judges intuitively understand
    # ("predictions are within X% of SOLIDWORKS"). Guard against divide-by-zero
    # on any near-zero FEA target.
    def _mape(true, pred):
        true = np.asarray(true, dtype=float)
        pred = np.asarray(pred, dtype=float)
        nonzero = np.abs(true) > 1e-9
        if not nonzero.any():
            return float("nan")
        return float(np.mean(np.abs((true[nonzero] - pred[nonzero]) / true[nonzero])) * 100)

    stress_mape = _mape(y_test[:, 0], preds[:, 0])
    disp_mape = _mape(y_test[:, 1], preds[:, 1])

    print(f"Stress   MAE: {stress_mae:.2f} MPa   MAPE: {stress_mape:.1f}%   R^2: {stress_r2:.3f}")
    print(f"Displace MAE: {disp_mae:.4f} mm    MAPE: {disp_mape:.1f}%   R^2: {disp_r2:.3f}")

    joblib.dump(model, MODEL_PATH)

    with open(MATERIAL_JSON) as f:
        material = json.load(f)
    yield_mpa = material["yield_strength_MPa"]

    metadata = {
        "model_type": model_type,
        "n_training_rows": n,
        "feature_names": FEATURE_NAMES,
        "targets": ["von_mises_stress_MPa", "max_displacement_mm"],
        "axis_convention": {
            "Fx": "longitudinal", "Fy": "lateral", "Fz": "vertical"
        },
        "material": material,
        "priority_thresholds": priority_thresholds(yield_mpa),
        "training_envelope": training_envelope,
        "metrics": {
            "stress_MAE_MPa": stress_mae,
            "stress_MAPE_pct": None if np.isnan(stress_mape) else stress_mape,
            "stress_R2": None if np.isnan(stress_r2) else stress_r2,
            "displacement_MAE_mm": disp_mae,
            "displacement_MAPE_pct": None if np.isnan(disp_mape) else disp_mape,
            "displacement_R2": None if np.isnan(disp_r2) else disp_r2,
        },
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nSaved model -> {MODEL_PATH}")
    print(f"Saved metadata -> {METADATA_PATH}")
    print(f"Priority thresholds (stress-based, vs yield={yield_mpa} MPa): "
          f"{metadata['priority_thresholds']}")


if __name__ == "__main__":
    main()
