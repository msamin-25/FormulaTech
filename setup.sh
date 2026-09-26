#!/usr/bin/env bash
# ImpactIQ — one-command setup for a fresh clone.
#
#   git clone <repo> && cd impactiq && ./setup.sh
#
# Creates a virtualenv, installs deps, generates the dataset (if none is
# present), and trains the model. After this finishes, run ./run.sh to start
# the backend, then open dashboard/index.html.
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
echo "==> Using interpreter: $($PY --version 2>&1)"

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if [ ! -f data/fea_dataset.csv ]; then
  echo "==> No data/fea_dataset.csv found — generating synthetic placeholder data"
  python data/generate_synthetic_data.py
else
  echo "==> Found data/fea_dataset.csv — using it as-is (drop in real FEA data here)"
fi

echo "==> Training the surrogate model"
python model/train.py

echo ""
echo "==> Setup complete."
echo "    Start the backend:   ./run.sh        (or: source .venv/bin/activate && uvicorn backend.app:app --host 0.0.0.0 --port 8000)"
echo "    Open the dashboard:  dashboard/index.html"
echo "    Pre-flight check:    source .venv/bin/activate && python check_demo.py"
