"""
Shared feature engineering — used by BOTH train.py and predict.py so the
model always sees features built the exact same way it was trained on.

Axis convention (must match Person 1's SOLIDWORKS setup and Person 3's
Arduino conversion): Fx = longitudinal, Fy = lateral, Fz = vertical, all in N.
"""

import numpy as np


FEATURE_NAMES = [
    "Fx", "Fy", "Fz",
    "abs_Fx", "abs_Fy", "abs_Fz",
    "Fx2", "Fy2", "Fz2",
    "magnitude",
]


def build_features(fx: float, fy: float, fz: float) -> list:
    """Turn raw (Fx, Fy, Fz) into the engineered feature vector.

    Physical loading -> stress/displacement relationships are dominated by
    magnitude and quadratic (energy-like) terms, so we hand the model those
    directly rather than hoping a small dataset teaches it that structure.
    """
    magnitude = float(np.sqrt(fx ** 2 + fy ** 2 + fz ** 2))
    return [
        fx, fy, fz,
        abs(fx), abs(fy), abs(fz),
        fx ** 2, fy ** 2, fz ** 2,
        magnitude,
    ]


def build_feature_matrix(fx_arr, fy_arr, fz_arr):
    return np.array([
        build_features(fx, fy, fz)
        for fx, fy, fz in zip(fx_arr, fy_arr, fz_arr)
    ])
