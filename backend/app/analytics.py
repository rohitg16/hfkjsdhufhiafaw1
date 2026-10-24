"""
Data loading + forecast-vintage pairing + error metrics.
Reusable by the API (app/main.py) and by scripts.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ACTUALS_FILE = os.getenv("ACTUALS_FILE", "actuals_wind_jan2026.json")
FORECASTS_FILE = os.getenv("FORECASTS_FILE", "forecasts_wind_jan2026.json")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_json(name: str) -> list:
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing data file {path}. "
            f"Run `python scripts/fetch_data.py` first."
        )
    with open(path) as f:
        return json.load(f)


def actuals_to_hourly(actuals: List[dict]) -> Dict[datetime, float]:
    buckets: Dict[datetime, List[float]] = defaultdict(list)
    for row in actuals:
        t = parse_ts(row["startTime"])
        hour = t.replace(minute=0, second=0, microsecond=0)
        buckets[hour].append(float(row["generation"]))
    return {h: mean(vals) for h, vals in buckets.items()}


def group_forecasts_by_start(forecasts: List[dict]) -> Dict[datetime, List[dict]]:
    out: Dict[datetime, List[dict]] = defaultdict(list)
    for f in forecasts:
        out[parse_ts(f["startTime"])].append(f)
    return out


def latest_forecast_at(forecasts_by_start, target_time: datetime) -> Dict[datetime, dict]:
    """Latest forecast per startTime, published on or before target_time."""
    best: Dict[datetime, dict] = {}
    for st, rows in forecasts_by_start.items():
        candidates = [f for f in rows if parse_ts(f["publishTime"]) <= target_time]
        if candidates:
            best[st] = max(candidates, key=lambda f: parse_ts(f["publishTime"]))
    return best


def pairs_at_lead_target(forecasts_by_start, actuals_hourly,
                         window_start: datetime, window_end: datetime,
                         lead_target_hours: float) -> List[dict]:
    """For each delivery T, use the latest forecast published ≤ T - lead_target."""
    pairs = []
    for T, actual_mw in actuals_hourly.items():
        if not (window_start <= T <= window_end):
            continue
        cutoff = T - timedelta(hours=lead_target_hours)
        candidates = [
            f for f in forecasts_by_start.get(T, [])
            if parse_ts(f["publishTime"]) <= cutoff
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda f: parse_ts(f["publishTime"]))
        pub = parse_ts(best["publishTime"])
        pairs.append({
            "startTime": T,
            "publishTime": pub,
            "actual_mw": actual_mw,
            "forecast_mw": float(best["generation"]),
            "lead_hours": (T - pub).total_seconds() / 3600.0,
        })
    pairs.sort(key=lambda r: r["startTime"])
    return pairs


def _basic_metrics(actuals, forecasts):
    errors = [f - a for a, f in zip(actuals, forecasts)]
    abs_errors = [abs(e) for e in errors]
    sq_errors = [e * e for e in errors]
    nonzero = [(a, e) for a, e in zip(actuals, errors) if a != 0]
    mape = (mean(abs(e) / abs(a) for a, e in nonzero) * 100) if nonzero else None
    return {
        "bias_mw": mean(errors),
        "mae_mw": mean(abs_errors),
        "rmse_mw": (sum(sq_errors) / len(sq_errors)) ** 0.5,
        "mape_pct": mape,
    }


def metrics(pairs: List[dict]) -> dict:
    """Return raw metrics plus bias-corrected metrics."""
    if not pairs:
        return {"n": 0, "raw": None, "bias_corrected": None}

    actuals = [p["actual_mw"] for p in pairs]
    forecasts = [p["forecast_mw"] for p in pairs]

    raw = _basic_metrics(actuals, forecasts)

    bias = raw["bias_mw"]
    corrected_forecasts = [f - bias for f in forecasts]
    corrected = _basic_metrics(actuals, corrected_forecasts)
    corrected.pop("bias_mw", None)  # bias is 0 by construction

    return {
        "n": len(pairs),
        "raw": raw,
        "bias_corrected": corrected,
    }


class DataStore:
    """Loads JSON once and exposes analytics methods."""

    def __init__(self):
        self.actuals = _load_json(ACTUALS_FILE)
        self.forecasts = _load_json(FORECASTS_FILE)
        self.actuals_hourly = actuals_to_hourly(self.actuals)
        self.forecasts_by_start = group_forecasts_by_start(self.forecasts)

    def data_range(self) -> dict:
        actuals_ts = list(self.actuals_hourly.keys())
        fc_pubs = [parse_ts(f["publishTime"]) for f in self.forecasts]
        return {
            "actuals_start": min(actuals_ts).isoformat(),
            "actuals_end": max(actuals_ts).isoformat(),
            "forecasts_publish_start": min(fc_pubs).isoformat(),
            "forecasts_publish_end": max(fc_pubs).isoformat(),
            "actuals_hours": len(self.actuals_hourly),
            "forecast_rows": len(self.forecasts),
        }

    def latest_forecast(self, decision_time: datetime, hours_ahead: int = 48) -> list:
        latest = latest_forecast_at(self.forecasts_by_start, decision_time)
        end = decision_time + timedelta(hours=hours_ahead)
        rows = []
        for st in sorted(latest.keys()):
            if st < decision_time or st > end:
                continue
            f = latest[st]
            rows.append({
                "startTime": st.isoformat(),
                "publishTime": parse_ts(f["publishTime"]).isoformat(),
                "forecast_mw": float(f["generation"]),
                "lead_hours": (st - parse_ts(f["publishTime"])).total_seconds() / 3600.0,
            })
        return rows

    def accuracy(self, decision_time: datetime, lookback_days: int,
                 lead_target_hours: float) -> dict:
        window_start = decision_time - timedelta(days=lookback_days)
        window_end = decision_time
        pairs = pairs_at_lead_target(
            self.forecasts_by_start, self.actuals_hourly,
            window_start, window_end, lead_target_hours,
        )
        return {
            "decision_time": decision_time.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "lead_target_hours": lead_target_hours,
            "metrics": metrics(pairs),
        }

    def accuracy_by_lead_time(self, decision_time: datetime,
                              lookback_days: int) -> dict:
        window_start = decision_time - timedelta(days=lookback_days)
        window_end = decision_time
        out = []
        for lt in [6, 12, 18, 24, 36, 48]:
            pairs = pairs_at_lead_target(
                self.forecasts_by_start, self.actuals_hourly,
                window_start, window_end, lt,
            )
            m = metrics(pairs)
            out.append({"lead_target_hours": lt, **m})
        return {
            "decision_time": decision_time.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "buckets": out,
        }