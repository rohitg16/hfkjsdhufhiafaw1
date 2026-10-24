"""
Step 1: Fetch Elexon wind actuals (FUELHH) and forecasts (WINDFOR)
for January 2026 purchase decisions. Saves to local JSON files.
"""
import json
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("ELEXON_BASE_URL", "https://data.elexon.co.uk/bmrs/api/v1")

ACTUALS_FROM_DATE = os.getenv("ACTUALS_FROM_DATE", "2026-01-01")
ACTUALS_TO_DATE   = os.getenv("ACTUALS_TO_DATE", "2026-01-31")

FORECASTS_FROM = os.getenv("FORECASTS_FROM", "2025-12-25T00:00:00Z")
FORECASTS_TO   = os.getenv("FORECASTS_TO", "2026-02-01T23:59:59Z")

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(OUT_DIR, exist_ok=True)


def fetch(url: str, params: dict) -> list:
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        body = r.json()
        # /stream endpoints return a plain list;
        # non-stream endpoints return {"data": [...]}
        if isinstance(body, list):
            return body
        return body.get("data", [])


def main():
    # ---------- Actuals (FUELHH) ----------
    print("Fetching FUELHH (actuals)...")
    fuelhh = fetch(
        f"{BASE}/datasets/FUELHH/stream",
        {
            "settlementDateFrom": ACTUALS_FROM_DATE,
            "settlementDateTo":   ACTUALS_TO_DATE,
        },
    )
    wind_actuals = [row for row in fuelhh if row.get("fuelType") == "WIND"]
    actuals_path = os.path.join(OUT_DIR, "actuals_wind_jan2026.json")
    with open(actuals_path, "w") as f:
        json.dump(wind_actuals, f, indent=2)
    print(f"  saved {len(wind_actuals)} WIND actuals -> {actuals_path}")

    # ---------- Forecasts (WINDFOR) ----------
    print("Fetching WINDFOR (forecasts)...")
    forecasts = fetch(
        f"{BASE}/datasets/WINDFOR/stream",
        {
            "publishDateTimeFrom": FORECASTS_FROM,
            "publishDateTimeTo":   FORECASTS_TO,
        },
    )
    forecasts_path = os.path.join(OUT_DIR, "forecasts_wind_jan2026.json")
    with open(forecasts_path, "w") as f:
        json.dump(forecasts, f, indent=2)
    print(f"  saved {len(forecasts)} forecast rows -> {forecasts_path}")

    # ---------- Sanity checks ----------
    if wind_actuals:
        starts = sorted({r["startTime"] for r in wind_actuals})
        print("\nActuals summary")
        print(f"  first startTime: {starts[0]}")
        print(f"  last  startTime: {starts[-1]}")
        print(f"  unique periods : {len(starts)}")
        t0 = datetime.fromisoformat(starts[0].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(starts[1].replace("Z", "+00:00"))
        print(f"  resolution     : {(t1 - t0).total_seconds() / 60:.0f} min")

    if forecasts:
        pubs = sorted({r["publishTime"] for r in forecasts})
        starts = sorted({r["startTime"] for r in forecasts})
        print("\nForecast summary")
        print(f"  total rows         : {len(forecasts)}")
        print(f"  unique publishTimes: {len(pubs)}")
        print(f"  first publishTime  : {pubs[0]}")
        print(f"  last  publishTime  : {pubs[-1]}")
        print(f"  first startTime    : {starts[0]}")
        print(f"  last  startTime    : {starts[-1]}")


if __name__ == "__main__":
    main()