# ImpactIQ

Post-impact structural triage: detect a wheel-impact event, reconstruct a
representative loading condition, predict structural response with an ML
surrogate trained on SOLIDWORKS FEA, and tell engineers whether the upright
needs inspection.

```
PHYSICAL IMPACT -> Arduino+IMU -> Ax,Ay,Az -> impact detector -> Fx,Fy,Fz
                 -> ML surrogate -> stress + displacement -> LOW/MODERATE/HIGH
                 -> dashboard highlights affected upright
                 -> SOLIDWORKS provides engineering validation
```

## The contract everyone builds against

**Axis convention (never change this mid-hackathon):**
`Fx` = longitudinal, `Fy` = lateral, `Fz` = vertical (all Newtons).

**Dataset schema** (`data/schema.md`): `Fx, Fy, Fz, von_mises_stress_MPa, max_displacement_mm`.
Person 1's real SOLIDWORKS export drops in as `data/fea_dataset.csv` with
zero code changes elsewhere.

**Backend API** (`backend/app.py`) is the integration hub. Everyone talks to
it, never to each other's code directly:
- `POST /impact {Fx, Fy, Fz, upright_id, source}` -> runs the model, returns
  predicted stress/displacement/priority, an `in_envelope` flag +
  `extrapolation_warning` (see "outside trained envelope" below), and logs the
  event.
- `GET /latest` — most recent event (dashboard polls this).
- `GET /history?limit=N` — recent events.
- `GET /metadata` — thresholds, material, axis convention, training envelope,
  and held-out metrics (R², MAE, MAPE).

## Current status (already built and verified end-to-end)

- [x] Synthetic FEA dataset (`data/generate_synthetic_data.py`) — physically
      motivated placeholder so the pipeline works today, swappable for real
      SOLIDWORKS data with zero code changes.
- [x] ML surrogate (`model/train.py`, `model/predict.py`) — RandomForest,
      R²=0.97 stress / 0.96 displacement on synthetic data. Auto-switches to
      Ridge regression if the real FEA dataset ends up small (<50 rows),
      since a forest overfits tiny hand-picked load cases.
- [x] Backend (`backend/app.py`) — tested live, full round trip works.
- [x] Impact detector (`backend/impact_detector.py`) — has a `--simulate`
      mode that generates fake impact bursts with NO hardware, so it's
      already been run end-to-end against the backend.
- [x] Dashboard (`dashboard/index.html`) — polls the backend, shows a
      color-coded priority badge and highlights the affected upright on a
      simple car diagram.
- [x] Arduino starter sketch (`arduino/impact_imu.ino`) — MPU6050 template
      printing the exact serial format the detector expects.
- [x] Demo fallback — dashboard "Simulate Impact" buttons (Mild/Moderate/
      Severe per corner) that post preset impacts straight to the backend,
      so a live demo works even with no hardware. Verified LOW/MOD/HIGH.
- [x] Pre-flight check (`check_demo.py`) — one command that verifies the
      whole stack is stage-ready and all three priority levels fire.
- [x] Robust dataset loader (`model/dataset.py`) — validates/cleans real
      SOLIDWORKS exports with clear errors (see "Person 1" below).
- [x] "Outside trained envelope" guard — every prediction reports whether the
      input force is within the range the model was trained on; extrapolations
      are flagged in the API and on the dashboard instead of faked confidence.
- [x] Validation plot (`model/validate.py`) — cross-validated AI-vs-FEA parity
      plot (`model/validation.png`) with R²/MAE/MAPE, for judging.

## Quickstart

**Fastest path (fresh clone):**

```bash
git clone <this-repo> && cd impactiq
./setup.sh          # venv + deps + dataset + trained model, one command
./run.sh            # starts the backend on http://localhost:8000 (leave running)
# then open dashboard/index.html in a browser
```

**Manual path (if you prefer to run the steps yourself):**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. generate/refresh the dataset (swap for real FEA export when ready)
python data/generate_synthetic_data.py

# 2. train the model
python model/train.py

# 3. (optional) validation plot for judges: AI vs SOLIDWORKS FEA
python model/validate.py            # writes model/validation.png

# 4. start the backend (leave running)
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

# 5. start the impact detector — plug in Person 3's Arduino first:
python backend/impact_detector.py                    # auto-finds the port, 9600 baud
# no hardware? emit Person 3's exact serial output instead:
python backend/impact_detector.py --simulate-arduino

# 6. open dashboard/index.html in a browser (or `python -m http.server`
#    from inside dashboard/ and visit http://localhost:8080)
```

## Live demo runbook (on stage)

**The night before / morning of:**
```bash
pip install --break-system-packages -r requirements.txt
python data/generate_synthetic_data.py   # or: real FEA export already in place
python model/train.py
```

**Right before you walk on stage — one command, look for all-green:**
```bash
# terminal 1 (leave running):
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# terminal 2:
python check_demo.py        # verifies backend, model, and all 3 priority levels
```
If `check_demo.py` prints `ALL CHECKS PASSED`, you're safe. If not, it tells
you exactly what's broken.

**The demo itself — hero path (Arduino working):**
1. Open `dashboard/index.html`, confirm the status dot is green ("connected").
2. Physically hit the mounted IMU. Graph spikes → corner turns red →
   priority badge + stress/displacement appear. That's the whole story.

**The demo itself — fallback path (Arduino misbehaving on stage):**
- The dashboard has a **Demo Controls** panel: pick a corner, click
  **Mild / Moderate / Severe**. Same pipeline, same output as a real hit —
  it POSTs a preset impact straight to the backend. Nobody in the audience
  can tell the difference, and it's an honest fallback (the spec explicitly
  endorses a "SIMULATE IMPACT" button).
- This works even if the Arduino AND the detector script are both dead —
  dashboard + backend alone is a complete demo.

**Everything runs locally** — no internet needed on stage.

## Person 1 — SOLIDWORKS

Your only deliverable that plugs into the pipeline: `data/fea_dataset.csv`
matching `data/schema.md`, and `data/material.json` with the real material's
yield strength and elastic modulus. 8-20 well-chosen load cases spanning
mild to severe is enough.

**You do NOT need node X/Y/Z coordinates.** The model maps one force vector per
load case to the peak response — it is not a GNN. Skip the manual "List Results
-> Save As CSV" spatial export; we only need each study's peak von Mises stress
and peak displacement, plus the force you applied.

**If your export is already one summary row per study** (5 columns per
schema.md):
```bash
cp your_export.csv data/fea_dataset.csv
```

**If your export is per-node stress** (node index + stress, from the VBA API),
use the adapter to reduce it and attach the applied forces:
```bash
# fill data/load_cases.csv (template): applied Fx,Fy,Fz + peak displacement per study
python data/from_solidworks.py --manifest data/load_cases.csv \
    --nodes-dir data/solidworks_exports   # omit --nodes-dir if stress is in the manifest
```

Either way, then:
```bash
# edit data/material.json with real yield_strength_MPa / elastic_modulus_GPa
python model/dataset.py data/fea_dataset.csv   # (optional) validate it loads cleanly first
python model/train.py    # re-trains, prints new thresholds + metrics automatically
python model/validate.py # regenerate the AI-vs-FEA plot on the real data
python check_demo.py     # confirm the 3 demo buttons still hit LOW/MOD/HIGH
```

The CSV loader (`model/dataset.py`) is forgiving about real-export quirks —
header case/whitespace, a UTF-8 BOM, comma/semicolon/tab delimiters, and extra
columns — but strict about the 5 required columns being present and numeric, so
a bad export fails with a clear message instead of a cryptic crash mid-training.

Everything downstream (model, backend, dashboard) picks up the change with
no code edits.

## Person 3 — Arduino / dashboard

`arduino/impact_imu.ino` is Person 3's MPU-6500 sketch. It detects hits on the
board and prints one line per hit at 9600 baud:
`IMPACT,<FRONT|BACK|LEFT|RIGHT>,ax,ay,az,totalG` (in g).

**Wiring (I2C, Arduino Uno/Nano):**

| MPU-6500 pin | Arduino | Note |
|---|---|---|
| VCC | 3.3V | 5V only if the breakout has a regulator (most GY-6500 boards do) |
| GND | GND | |
| SCL | A5 | Mega: pin 21 · Leonardo/R4: SCL pin |
| SDA | A4 | Mega: pin 20 · Leonardo/R4: SDA pin |
| AD0 | GND | sets I2C address 0x68, which the sketch uses |
| NCS | 3.3V | if your breakout exposes it: high selects I2C mode |

**Mounting:** board flat, chip side up, **Y- toward the car's front** and
**X+ toward the car's left** (Person 3's calibration). The software removes 1 g
of gravity from Z, so the board must stay flat.

**Two ways to get hits into the dashboard (pick one; only one program can own the port):**

1. **Python bridge** (works in any browser):
   `python backend/impact_detector.py` auto-finds the port, converts each hit to
   a force and posts it to the backend.
2. **Straight from the browser** (Chrome or Edge, works on Vercel too): click
   **Connect sensor** on the dashboard and pick the Arduino's port.

Both use the same mapping: board Y- = front, X+ = left, force = a × 9.81 ×
200 kg (a quarter of an ~800 kg F1 car). The struck corner comes from the hit
direction and lights up on the 3D car.

**Troubleshooting:**
- Nothing after `READY`: check SDA/SCL aren't swapped, and AD0 is on GND.
- "Port is busy": close the Arduino IDE serial monitor (and the Python bridge
  if you're using Connect sensor).
- Every tap reads LOW (or every tap reads HIGH): tune `--mass` in the bridge,
  and `SENSOR.massKg` in `dashboard/index.html` for the browser path.

## Deploy to Vercel

The dashboard is a static site, so Vercel needs no build settings.
`vercel.json` serves the repo root and redirects `/` to `/dashboard/`, and
`.vercelignore` keeps CAD files and Python out of the deploy.

1. vercel.com → **Add New → Project** → import `msamin-25/FormulaTech` → **Deploy**.
2. On its own, the deployed page runs the in-browser model.
3. With the backend running on your laptop (`./run.sh`), the Vercel page in
   Chrome switches to the trained model automatically. Chrome may ask to allow
   access to your local network: allow it. **Connect sensor** reads the
   Arduino over USB from the Vercel page too.

## Person 2 (you) — integration owner

Backend, model, and detector are built and tested. If real data lands from
Person 1 or Person 3 before the merge point, your job is just:
1. Drop in their file (`data/fea_dataset.csv` or real serial port).
2. Re-run `model/train.py` if the FEA data changed.
3. Confirm `curl localhost:8000/health` and a manual `POST /impact` still work.

## Known simplifications (say these out loud during judging, don't hide them)

- Boundary conditions and load application in SOLIDWORKS are simplified for
  a prototype — document Person 1's assumption on a methodology slide.
- The IMU-to-force conversion (`backend/impact_detector.py`) uses a single
  effective mass for F=ma (default 200 kg, a quarter of an ~800 kg F1 car),
  not a full multibody dynamics reconstruction —
  it's a stand-in for "how much of the car's inertia loads the upright,"
  not a claim about total vehicle mass.
- Priority thresholds are stress-as-fraction-of-yield cut points
  (40%/75%) — a reasonable engineering judgment call for a hackathon triage
  tool, not a certified safety standard.
