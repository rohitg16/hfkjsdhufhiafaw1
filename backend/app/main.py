"""
FastAPI entry point. Exposes the analytics as HTTP endpoints.
Run with:
    uvicorn app.main:app --reload --port 8000
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.analytics import DataStore, parse_ts, pairs_at_lead_target

load_dotenv()

DEFAULT_DECISION_TIME = os.getenv("DEFAULT_DECISION_TIME", "2026-01-15T10:00:00Z")
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]

app = FastAPI(
    title="Wind Decision Portal API",
    description="Latest wind forecasts and historical forecast accuracy for day-ahead decisions.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,  # tighten later for production
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    store = DataStore()
except Exception as e:
    raise RuntimeError(f"Failed to load data store: {e}") from e


def _parse_dt(s: str) -> datetime:
    try:
        return parse_ts(s)
    except Exception:
        raise HTTPException(400, f"Invalid ISO datetime: {s}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {
        "data_range": store.data_range(),
        "default_decision_time": DEFAULT_DECISION_TIME,
    }


@app.get("/api/forecast/latest")
def forecast_latest(
    decision_time: str = Query(..., description="ISO datetime, e.g. 2026-01-15T10:00:00Z"),
    hours_ahead: int = Query(48, ge=1, le=168),
):
    dt = _parse_dt(decision_time)
    rows = store.latest_forecast(dt, hours_ahead=hours_ahead)
    return {
        "decision_time": dt.isoformat(),
        "hours_ahead": hours_ahead,
        "count": len(rows),
        "forecasts": rows,
    }


@app.get("/api/accuracy")
def accuracy(
    decision_time: str = Query(...),
    lookback_days: int = Query(30, ge=1, le=90),
    lead_target_hours: float = Query(24, ge=1, le=72),
):
    dt = _parse_dt(decision_time)
    return store.accuracy(dt, lookback_days=lookback_days,
                          lead_target_hours=lead_target_hours)


@app.get("/api/accuracy/by-lead-time")
def accuracy_by_lead_time(
    decision_time: str = Query(...),
    lookback_days: int = Query(30, ge=1, le=90),
):
    dt = _parse_dt(decision_time)
    return store.accuracy_by_lead_time(dt, lookback_days=lookback_days)


@app.get("/api/pairs")
def pairs(
    decision_time: str = Query(...),
    lookback_days: int = Query(30, ge=1, le=90),
    lead_target_hours: float = Query(24, ge=1, le=72),
):
    dt = _parse_dt(decision_time)
    window_start = dt - timedelta(days=lookback_days)
    result = pairs_at_lead_target(
        store.forecasts_by_start, store.actuals_hourly,
        window_start, dt, lead_target_hours,
    )
    return {
        "decision_time": dt.isoformat(),
        "count": len(result),
        "pairs": [
            {
                "startTime": p["startTime"].isoformat(),
                "publishTime": p["publishTime"].isoformat(),
                "actual_mw": p["actual_mw"],
                "forecast_mw": p["forecast_mw"],
                "lead_hours": p["lead_hours"],
            }
            for p in result
        ],
    }