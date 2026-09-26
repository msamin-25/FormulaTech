#!/usr/bin/env python3
"""
ImpactIQ — pre-flight demo check.

Run this RIGHT BEFORE you go on stage. It verifies the whole software stack
is healthy and that the three demo buttons will produce three different
priority levels. One command, all green = safe to present.

    python check_demo.py

Exit code 0 = everything's good. Non-zero = something's wrong, with a
message telling you what to fix.
"""

import sys

BACKEND_URL = "http://localhost:8000"

# Must match the presets wired into dashboard/index.html
DEMO_PRESETS = {
    "mild":     {"Fx": 900,  "Fy": 500,   "Fz": -700},
    "moderate": {"Fx": 3400, "Fy": -2000, "Fz": 4300},
    "severe":   {"Fx": 7000, "Fy": 5000,  "Fz": -6500},
}
EXPECTED_PRIORITY = {"mild": "LOW", "moderate": "MODERATE", "severe": "HIGH"}

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def ok(msg):   print(f"{GREEN}  [OK]{RESET} {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def warn(msg): print(f"{YELLOW}[WARN]{RESET} {msg}")


def main():
    try:
        import requests
    except ImportError:
        fail("`requests` not installed. Run: pip install -r requirements.txt")
        return 1

    problems = 0

    # 1. Backend reachable?
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=3)
        r.raise_for_status()
        ok(f"Backend is up ({BACKEND_URL})")
    except Exception as e:
        fail(f"Backend not reachable at {BACKEND_URL} — start it with: "
             f"uvicorn backend.app:app --host 0.0.0.0 --port 8000")
        fail(f"     ({e})")
        return 1  # nothing else will work; bail early

    # 2. Model trained / metadata present?
    try:
        meta = requests.get(f"{BACKEND_URL}/metadata", timeout=3).json()
        mtype = meta.get("model_type", "?")
        nrows = meta.get("n_training_rows", "?")
        mat = meta.get("material", {}).get("material_name", "?")
        ok(f"Model loaded: {mtype}, trained on {nrows} rows, material: {mat}")
        if "PLACEHOLDER" in str(mat):
            warn("Still using PLACEHOLDER material/data — swap in Person 1's real "
                 "FEA export before the real demo, then re-run model/train.py.")
    except Exception as e:
        fail(f"Could not read model metadata — has model/train.py been run? ({e})")
        problems += 1

    # 3. Do the three demo buttons produce three distinct priorities?
    print("\n  Testing the three demo-button presets:")
    got = {}
    for severity, preset in DEMO_PRESETS.items():
        try:
            resp = requests.post(f"{BACKEND_URL}/impact",
                                 json={**preset, "upright_id": "front_right",
                                       "source": "preflight_check"},
                                 timeout=3).json()
            priority = resp.get("priority")
            stress = resp.get("predicted_von_mises_stress_MPa")
            got[severity] = priority
            expected = EXPECTED_PRIORITY[severity]
            mark = ok if priority == expected else warn
            mark(f"{severity:9s} -> {stress} MPa -> {priority} "
                 f"(expected {expected})")
        except Exception as e:
            fail(f"{severity} preset failed to POST: {e}")
            problems += 1

    distinct = len(set(got.values()))
    if distinct == 3:
        ok("All three buttons give three DIFFERENT priorities — good for the demo.")
    else:
        warn(f"Only {distinct} distinct priority level(s) across the three buttons. "
             f"After retraining on real data you may need to adjust the preset "
             f"force values in dashboard/index.html (DEMO_PRESETS) and here.")
        problems += 1

    print()
    if problems == 0:
        print(f"{GREEN}=== ALL CHECKS PASSED — safe to demo. ==={RESET}")
        return 0
    else:
        print(f"{YELLOW}=== {problems} issue(s) above — review before going on stage. ==={RESET}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
