"""
Step 2: Pair actuals (30-min) with forecasts (1-hour) and compute
day-ahead accuracy + skill curve by lead time.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Dict, List

HERE = os.path.dirname(__file__)
DATA_DIR = os.path.join(HERE, "..", "data")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_json(name: str) -> list:
    with open(os.path.join(DATA_DIR, name)) as f:
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
    """
    Latest forecast for each startTime, published on or before target_time.
    Used for the "latest forecast available now" view.
    """
    best: Dict[datetime, dict] = {}
    for st, rows in forecasts_by_start.items():
        candidates = [f for f in rows if parse_ts(f["publishTime"]) <= target_time]
        if not candidates:
            continue
        best[st] = max(candidates, key=lambda f: parse_ts(f["publishTime"]))
    return best


def pairs_at_lead_target(
    forecasts_by_start, actuals_hourly,
    window_start: datetime, window_end: datetime,
    lead_target_hours: float,
) -> List[dict]:
    """
    For each delivery T in [window_start, window_end]:
      use the latest forecast published <= (T - lead_target_hours).
    Simulates "the forecast available `lead_target` hours before delivery".
    """
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


def metrics(pairs: List[dict]) -> dict:
    if not pairs:
        return {"n": 0, "bias_mw": None, "mae_mw": None,
                "rmse_mw": None, "mape_pct": None}
    actuals = [p["actual_mw"] for p in pairs]
    forecasts = [p["forecast_mw"] for p in pairs]
    errors = [f - a for a, f in zip(actuals, forecasts)]
    abs_errors = [abs(e) for e in errors]
    sq_errors = [e * e for e in errors]
    nonzero = [(a, e) for a, e in zip(actuals, errors) if a != 0]
    mape = (
        mean(abs(e) / abs(a) for a, e in nonzero) * 100
        if nonzero else None
    )
    return {
        "n": len(pairs),
        "bias_mw": mean(errors),
        "mae_mw": mean(abs_errors),
        "rmse_mw": (sum(sq_errors) / len(sq_errors)) ** 0.5,
        "mape_pct": mape,
    }


def print_metrics(label: str, m: dict):
    if m["n"] == 0:
        print(f"  {label}: n=0 (no data)")
        return
    mape = f"{m['mape_pct']:.1f}%" if m["mape_pct"] is not None else "n/a"
    print(f"  {label}: n={m['n']}, "
          f"MAE={m['mae_mw']:.1f}, "
          f"RMSE={m['rmse_mw']:.1f}, "
          f"Bias={m['bias_mw']:.1f}, "
          f"MAPE={mape}")


def main():
    print("Loading data...")
    actuals = load_json("actuals_wind_jan2026.json")
    forecasts = load_json("forecasts_wind_jan2026.json")
    print(f"  actuals: {len(actuals)} rows")
    print(f"  forecasts: {len(forecasts)} rows")

    actuals_hourly = actuals_to_hourly(actuals)
    forecasts_by_start = group_forecasts_by_start(forecasts)
    print(f"  actuals_hourly: {len(actuals_hourly)} hours")
    print(f"  forecast startTimes: {len(forecasts_by_start)}")

    window_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_end   = datetime(2026, 1, 31, 23, 0, tzinfo=timezone.utc)

    # ---- Day-ahead accuracy (lead target = 24h) ----
    print("\nDay-ahead forecast accuracy (lead ≈ 24h)")
    pairs_24 = pairs_at_lead_target(
        forecasts_by_start, actuals_hourly,
        window_start, window_end, lead_target_hours=24,
    )
    print_metrics("overall", metrics(pairs_24))

    # ---- Skill curve by lead target ----
    print("\nForecast skill by lead target")
    for lt in [6, 12, 18, 24, 36, 48]:
        pairs = pairs_at_lead_target(
            forecasts_by_start, actuals_hourly,
            window_start, window_end, lead_target_hours=lt,
        )
        print_metrics(f"lead ~{lt:>2}h", metrics(pairs))


if __name__ == "__main__":
    main()