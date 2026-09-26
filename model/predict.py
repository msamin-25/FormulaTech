"""
ImpactIQ — load the trained surrogate model and serve predictions.

Imported by backend/app.py. Not meant to be run standalone, though the
__main__ block at the bottom does a quick sanity check.
"""

import json
import sys
from pathlib import Path

import joblib

sys.path.insert(0, str(Path(__file__).parent))
from features import build_features  # noqa: E402

HERE = Path(__file__).parent
MODEL_PATH = HERE / "surrogate_model.joblib"
METADATA_PATH = HERE / "metadata.json"

_model = None
_metadata = None


def _load():
    global _model, _metadata
    if _model is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"{MODEL_PATH} not found. Run `python model/train.py` first."
            )
        _model = joblib.load(MODEL_PATH)
        with open(METADATA_PATH) as f:
            _metadata = json.load(f)
    return _model, _metadata


def classify_priority(stress_mpa: float, thresholds: dict) -> str:
    if stress_mpa >= thresholds["high_stress_MPa"]:
        return "HIGH"
    elif stress_mpa >= thresholds["moderate_stress_MPa"]:
        return "MODERATE"
    else:
        return "LOW"


def check_envelope(fx: float, fy: float, fz: float, metadata: dict) -> dict:
    """Is this force vector inside the range the model was trained on?

    A RandomForest can't extrapolate past its training range at all (it clamps),
    and any regressor is untrustworthy well outside it. Rather than silently
    return a confident-looking number, we flag it so the backend/dashboard/judge
    knows the prediction is an extrapolation. This is the model saying
    "I haven't seen an impact this big."
    """
    env = metadata.get("training_envelope")
    if not env:  # older metadata without an envelope — don't block, just pass.
        return {"in_envelope": True, "reasons": []}

    reasons = []
    for axis, val in (("Fx", fx), ("Fy", fy), ("Fz", fz)):
        lo, hi = env[axis]
        if val < lo or val > hi:
            reasons.append(
                f"{axis}={val:.0f}N is outside trained range [{lo:.0f}, {hi:.0f}]N"
            )
    magnitude = (fx ** 2 + fy ** 2 + fz ** 2) ** 0.5
    mag_max = env.get("magnitude_max")
    if mag_max and magnitude > mag_max:
        reasons.append(
            f"|F|={magnitude:.0f}N exceeds largest trained impact {mag_max:.0f}N"
        )
    return {"in_envelope": len(reasons) == 0, "reasons": reasons}


def predict(fx: float, fy: float, fz: float) -> dict:
    """Given a representative impact force vector, return the full
    engineering triage result: predicted stress, displacement, and
    inspection priority.
    """
    model, metadata = _load()
    features = [build_features(fx, fy, fz)]
    stress_mpa, disp_mm = model.predict(features)[0]

    thresholds = metadata["priority_thresholds"]
    priority = classify_priority(stress_mpa, thresholds)

    yield_mpa = metadata["material"]["yield_strength_MPa"]

    envelope = check_envelope(fx, fy, fz, metadata)

    return {
        "input": {"Fx": fx, "Fy": fy, "Fz": fz},
        "predicted_von_mises_stress_MPa": round(float(stress_mpa), 2),
        "predicted_max_displacement_mm": round(float(disp_mm), 4),
        "yield_strength_MPa": yield_mpa,
        "fraction_of_yield": round(float(stress_mpa) / yield_mpa, 3),
        "priority": priority,
        "in_envelope": envelope["in_envelope"],
        "extrapolation_warning": None if envelope["in_envelope"]
            else "; ".join(envelope["reasons"]),
        "model_type": metadata["model_type"],
    }


if __name__ == "__main__":
    for fx, fy, fz in [(500, 200, -300), (3000, 1500, -2000), (6000, 4000, -5500)]:
        result = predict(fx, fy, fz)
        print(result)
