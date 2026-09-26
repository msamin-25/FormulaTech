#!/usr/bin/env bash
# ImpactIQ — start the backend API (integration hub).
# Assumes ./setup.sh has been run once already.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Run ./setup.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Backend starting on http://localhost:8000"
echo "    Open dashboard/index.html in a browser (it polls this backend)."
exec uvicorn backend.app:app --host 0.0.0.0 --port 8000
