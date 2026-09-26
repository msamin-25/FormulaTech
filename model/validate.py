#!/usr/bin/env python3
"""
ImpactIQ — validation plot for judges: AI surrogate vs SOLIDWORKS FEA.

Produces a parity plot (predicted stress on the y-axis vs the FEA "ground truth"
on the x-axis). Points on the diagonal = the ML model agrees with SOLIDWORKS.
This is the single most convincing artifact for judging: it shows, visually,
that the surrogate reproduces the FEA it was trained to approximate.

HONESTY NOTE
------------
Predictions here are OUT-OF-FOLD (k-fold cross-validation): every point is
predicted by a model that did NOT see that point during training. So the plot
reflects genuine generalization, not memorization — the numbers are the ones you
can defend to a judge.

Run:
    python model/validate.py
Outputs:
    model/validation_stress.png
    model/validation_displacement.png
    model/validation.png            (both panels side by side)
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: works over SSH / in CI / on the demo laptop
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import mean_absolute_error, r2_score  # noqa: E402
from sklearn.model_selection import KFold, cross_val_predict  # noqa: E402

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from features import build_feature_matrix  # noqa: E402
from dataset import load_fea_dataset, DatasetError  # noqa: E402
from train import build_model  # noqa: E402

DATA_CSV = HERE.parent / "data" / "fea_dataset.csv"

TARGETS = [
    ("von_mises_stress_MPa", "Von Mises Stress", "MPa", "validation_stress.png"),
    ("max_displacement_mm", "Max Displacement", "mm", "validation_displacement.png"),
]


def _mape(true, pred):
    true, pred = np.asarray(true, float), np.asarray(pred, float)
    nz = np.abs(true) > 1e-9
    return float(np.mean(np.abs((true[nz] - pred[nz]) / true[nz])) * 100) if nz.any() else float("nan")


def _panel(ax, true, pred, title, unit):
    lo = float(min(true.min(), pred.min()))
    hi = float(max(true.max(), pred.max()))
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad

    ax.plot([lo, hi], [lo, hi], "--", color="#888", linewidth=1.2,
            label="perfect agreement (y = x)", zorder=1)
    ax.scatter(true, pred, s=28, alpha=0.6, edgecolor="white", linewidth=0.4,
               color="#2f6fed", zorder=2, label="impact cases")

    r2 = r2_score(true, pred) if len(true) >= 2 else float("nan")
    mae = mean_absolute_error(true, pred)
    mape = _mape(true, pred)
    txt = f"$R^2$ = {r2:.3f}\nMAE = {mae:.3g} {unit}\nMAPE = {mape:.1f}%\nn = {len(true)}"
    ax.text(0.04, 0.96, txt, transform=ax.transAxes, va="top", ha="left",
            fontsize=10, bbox=dict(boxstyle="round,pad=0.4", fc="#f4f7ff", ec="#c6d4f5"))

    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(f"SOLIDWORKS FEA {title} ({unit})")
    ax.set_ylabel(f"ImpactIQ predicted {title} ({unit})")
    ax.set_title(f"{title}: AI surrogate vs FEA")
    ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.25)
    return {"r2": r2, "mae": mae, "mape": mape}


def main():
    try:
        df = load_fea_dataset(DATA_CSV)
    except DatasetError as e:
        print(f"ERROR: {e}")
        return 1

    n = len(df)
    X = build_feature_matrix(df["Fx"], df["Fy"], df["Fz"])
    model, model_type = build_model(n)

    n_splits = min(5, n)
    if n_splits < 2:
        print("ERROR: need at least 2 rows to cross-validate.")
        return 1
    cv = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    print(f"Cross-validating {model_type} with {n_splits}-fold CV over {n} rows...")

    # One CV pass on BOTH targets together — exactly the multi-output model we
    # ship — then split the predicted columns per target.
    y_all = df[[col for col, *_ in TARGETS]].values
    pred_all = cross_val_predict(model, X, y_all, cv=cv)

    combined_fig, combined_axes = plt.subplots(1, 2, figsize=(13, 6))

    for i, (col, title, unit, fname) in enumerate(TARGETS):
        y = df[col].values
        pred = pred_all[:, i]

        # standalone single-panel figure
        fig, ax = plt.subplots(figsize=(6.5, 6.5))
        stats = _panel(ax, y, pred, title, unit)
        fig.tight_layout()
        out = HERE / fname
        fig.savefig(out, dpi=140)
        plt.close(fig)
        print(f"  {title:18s} R^2={stats['r2']:.3f}  MAE={stats['mae']:.3g} {unit}  "
              f"MAPE={stats['mape']:.1f}%  -> {out.name}")

        _panel(combined_axes[i], y, pred, title, unit)

    combined_fig.suptitle(
        f"ImpactIQ surrogate validation vs SOLIDWORKS FEA  "
        f"({model_type}, {n_splits}-fold CV, n={n})", fontsize=13)
    combined_fig.tight_layout(rect=[0, 0, 1, 0.96])
    combined_out = HERE / "validation.png"
    combined_fig.savefig(combined_out, dpi=140)
    plt.close(combined_fig)
    print(f"\nSaved combined plot -> {combined_out}")
    print("Show validation.png to the judges: points on the dashed line mean the "
          "AI reproduces SOLIDWORKS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
