#!/usr/bin/env python3
"""
ImpactIQ — backend / integration hub (Person 2's main deliverable).

This is the one piece everyone else plugs into:
  - Person 3's impact detector POSTs Fx, Fy, Fz here after detecting an impact.
  - Person 3's dashboard polls GET /latest (or /history) to display results.
  - Person 1's SOLIDWORKS work feeds data/fea_dataset.csv, which model/train.py
    consumes — this API doesn't change when that happens.

Run:
    uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

Then POST a test impact:
    curl -X POST localhost:8000/impact -H "Content-Type: application/json" \
         -d '{"Fx": 3200, "Fy": -1800, "Fz": 4100}'
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
from model.predict import predict, _load as load_model_and_metadata  # noqa: E402

app = FastAPI(title="ImpactIQ Backend", version="0.1.0")

# Wide-open CORS for a hackathon demo — dashboard may be served from a
# different port/origin (file://, `python -m http.server`, or Vercel).
# allow_private_network lets an https page (the Vercel deploy) reach this
# backend on the presenter's laptop; Chrome requires the opt-in.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,
)

# In-memory event log. A hackathon demo doesn't need a database; if the
# process restarts, history resets. Good enough for a live demo.
EVENT_LOG: List[dict] = []
MAX_LOG_SIZE = 500


class ImpactInput(BaseModel):
    Fx: float
    Fy: float
    Fz: float
    upright_id: Optional[str] = "front_left"  # which upright this event is for
    source: Optional[str] = "unknown"          # "arduino" | "manual" | "simulated"


@app.on_event("startup")
def check_model_loaded():
    try:
        load_model_and_metadata()
        print("Model + metadata loaded OK.")
    except FileNotFoundError as e:
        print(f"WARNING: {e}")
        print("Run `python model/train.py` before sending impacts, or the "
              "/impact endpoint will 500.")


@app.get("/health")
def health():
    return {"status": "ok", "events_logged": len(EVENT_LOG)}


@app.get("/metadata")
def metadata():
    """Model info, axis convention, material, and priority thresholds —
    useful for the dashboard to show context (yield strength, thresholds)."""
    try:
        _, meta = load_model_and_metadata()
        return meta
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Model not trained yet. Run model/train.py")


@app.post("/impact")
def report_impact(impact: ImpactInput):
    """
    Main integration point. Person 3's impact detector calls this the moment
    it decides a real impact happened, with the representative Fx/Fy/Fz it
    derived from the IMU reading.
    """
    try:
        result = predict(impact.Fx, impact.Fy, impact.Fz)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Model not trained yet. Run model/train.py")

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "upright_id": impact.upright_id,
        "source": impact.source,
        **result,
    }
    EVENT_LOG.append(event)
    if len(EVENT_LOG) > MAX_LOG_SIZE:
        EVENT_LOG.pop(0)

    print(f"[IMPACT] {event['upright_id']}: "
          f"stress={event['predicted_von_mises_stress_MPa']}MPa "
          f"priority={event['priority']}")

    return event


@app.get("/latest")
def latest():
    """Dashboard polls this for the most recent impact event."""
    if not EVENT_LOG:
        return {"message": "no impacts recorded yet"}
    return EVENT_LOG[-1]


@app.get("/history")
def history(limit: int = 20):
    return {"count": len(EVENT_LOG), "events": EVENT_LOG[-limit:]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
