"""
S&P 500 Profit Margin Tracker  (APEX Layer 0 data source)
==========================================================
Fetches corporate profit data from FRED (Federal Reserve Economic Data)
and computes margin direction signals used by APEX Layer 0.

Proxy metric: CP / GDP × 100
  CP  = Corporate Profits After Tax w/ IVA & CCAdj  (FRED series CP)
  GDP = Gross Domestic Product                       (FRED series GDP)
Both are quarterly SAAR (seasonally adjusted annual rate, billions USD).

Run this script each quarter after BEA data updates (typically end of
January, April, July, October for Q4/Q1/Q2/Q3 respectively).

Usage:
    python sp500_margin_tracker.py           # show current snapshot
    python sp500_margin_tracker.py --save    # write sp500_output/sp500_margin_latest.json
    python sp500_margin_tracker.py --history # print full historical table
    python sp500_margin_tracker.py --history --tail 30  # last 30 quarters
"""

import warnings
warnings.filterwarnings("ignore")

import json
import os
import sys
from datetime import datetime
from io import StringIO

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:
    print("Please install requests: pip install requests")
    raise

# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════

FRED_CP  = "CP"    # Corporate Profits After Tax (Billions, SAAR, quarterly)
FRED_GDP = "GDP"   # Gross Domestic Product      (Billions, SAAR, quarterly)

_REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR  = os.path.join(_REPO_ROOT, "sp500_output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "sp500_margin_latest.json")

# YoY classification thresholds (percentage-point change in CP/GDP ratio)
#   MAJOR_EXPANSION : +2.0 pp or more
#   EXPANSION       : +0.5 pp to +2.0 pp
#   FLAT            : −0.5 pp to +0.5 pp
#   CONTRACTION     : −2.0 pp to −0.5 pp
#   MAJOR_CONTRACTION: below −2.0 pp
YOY_THRESHOLDS = [
    ( 2.0, "MAJOR_EXPANSION"),
    ( 0.5, "EXPANSION"),
    (-0.5, "FLAT"),
    (-2.0, "CONTRACTION"),
    (None, "MAJOR_CONTRACTION"),
]

# QoQ classification thresholds (same scale, tighter bands)
QOQ_THRESHOLDS = [
    ( 1.0, "MAJOR_EXPANSION"),
    ( 0.3, "EXPANSION"),
    (-0.3, "FLAT"),
    (-1.0, "CONTRACTION"),
    (None, "MAJOR_CONTRACTION"),
]


# ══════════════════════════════════════════════════════════════════
#  DATA FETCHING
# ══════════════════════════════════════════════════════════════════

def fetch_fred_series(series_id: str) -> pd.Series:
    """
    Download a FRED series as a pandas Series via the public CSV endpoint.
    No API key required.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    print(f"    Fetching FRED:{series_id} …", end=" ", flush=True)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), parse_dates=[0], index_col=0)
        df.columns = [series_id]
        s = pd.to_numeric(df[series_id], errors="coerce").dropna()
        print(f"{len(s)} rows  ({s.index[0].date()} → {s.index[-1].date()})")
        return s
    except Exception as e:
        print(f"❌ {e}")
        return pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════
#  MARGIN CALCULATION
# ══════════════════════════════════════════════════════════════════

def classify_direction(change_pp: float, thresholds: list) -> str:
    """Map a percentage-point change to a direction label."""
    for thresh, label in thresholds:
        if thresh is None:
            return label
        if change_pp >= thresh:
            return label
    return "N/A"


def build_margin_series() -> pd.DataFrame:
    """
    Compute quarterly S&P 500 margin proxy (CP / GDP × 100) from FRED.
    Returns DataFrame with margin, YoY/QoQ changes, and direction labels.
    """
    print("📡  Fetching FRED data …")
    cp  = fetch_fred_series(FRED_CP)
    gdp = fetch_fred_series(FRED_GDP)

    if cp.empty or gdp.empty:
        raise ConnectionError("Could not fetch FRED data. Check internet connection.")

    df = pd.DataFrame({"cp": cp, "gdp": gdp}).dropna()
    df["margin"] = df["cp"] / df["gdp"] * 100   # percentage of GDP

    # YoY: same quarter one year ago (4 quarters back)
    df["margin_yoy_prev"] = df["margin"].shift(4)
    df["yoy_change"]      = df["margin"] - df["margin_yoy_prev"]

    # QoQ: previous quarter
    df["margin_qoq_prev"] = df["margin"].shift(1)
    df["qoq_change"]      = df["margin"] - df["margin_qoq_prev"]

    df["direction_yoy"] = df["yoy_change"].apply(
        lambda x: classify_direction(float(x), YOY_THRESHOLDS) if not pd.isna(x) else "N/A"
    )
    df["direction_qoq"] = df["qoq_change"].apply(
        lambda x: classify_direction(float(x), QOQ_THRESHOLDS) if not pd.isna(x) else "N/A"
    )

    return df


def get_latest_snapshot(df: pd.DataFrame) -> dict:
    """Extract the most recent complete quarter's margin snapshot."""
    valid = df.dropna(subset=["direction_yoy", "direction_qoq"])
    if valid.empty:
        raise ValueError("No valid margin data available.")
    latest = valid.iloc[-1]
    return {
        "direction_yoy"  : latest["direction_yoy"],
        "direction_qoq"  : latest["direction_qoq"],
        "margin_latest"  : round(float(latest["margin"]), 4),
        "margin_yoy_prev": round(float(latest["margin_yoy_prev"]), 4),
        "margin_qoq_prev": round(float(latest["margin_qoq_prev"]), 4),
        "yoy_change_pp"  : round(float(latest["yoy_change"]), 4),
        "qoq_change_pp"  : round(float(latest["qoq_change"]), 4),
        "as_of_quarter"  : latest.name.strftime("%Y-%m-%d"),
        "data_source"    : "FRED CP/GDP (Corporate Profits After Tax / GDP, SAAR)",
        "updated_at"     : datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
#  OUTPUT
# ══════════════════════════════════════════════════════════════════

def save_snapshot(snap: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(snap, f, indent=2)
    print(f"💾  Saved → {OUTPUT_FILE}")


def print_status(snap: dict) -> None:
    print("\n" + "═" * 62)
    print("  S&P 500 Profit Margin Tracker  —  Latest Snapshot")
    print("═" * 62)
    print(f"  Quarter data:   {snap['as_of_quarter']}")
    print(f"  Margin (latest): {snap['margin_latest']:.3f}%  of GDP")
    print(f"  YoY change:     {snap['yoy_change_pp']:+.3f} pp  →  {snap['direction_yoy']}")
    print(f"  QoQ change:     {snap['qoq_change_pp']:+.3f} pp  →  {snap['direction_qoq']}")
    print("═" * 62)
    print()
    print("  Used by APEX Layer 0 regime scoring:")
    print(f"    YoY direction score: {_MARGIN_SCORES.get(snap['direction_yoy'], 0):+d}")
    print(f"    QoQ direction score: {_MARGIN_SCORES.get(snap['direction_qoq'], 0) // 2:+d}  "
          f"(halved: {_MARGIN_SCORES.get(snap['direction_qoq'], 0)} // 2)")
    print("═" * 62)


# Score mapping (same as in apex_strategy.py)
_MARGIN_SCORES = {
    "MAJOR_EXPANSION": 4, "EXPANSION": 2, "FLAT": 0,
    "CONTRACTION": -2, "MAJOR_CONTRACTION": -4, "N/A": 0
}


def print_history(df: pd.DataFrame, tail: int = 20) -> None:
    print(f"\n{'─' * 82}")
    print(f"  {'Quarter':<12} {'Margin%':>8}  {'YoY Δpp':>8}  "
          f"{'YoY Direction':<20}  {'QoQ Δpp':>8}  {'QoQ Direction'}")
    print(f"  {'─' * 80}")
    valid = df.dropna(subset=["direction_yoy", "direction_qoq"])
    for date, row in valid.tail(tail).iterrows():
        print(
            f"  {date.strftime('%Y-%m-%d'):<12} "
            f"{row['margin']:>7.3f}%  "
            f"{row['yoy_change']:>+8.3f}  "
            f"{row['direction_yoy']:<20}  "
            f"{row['qoq_change']:>+8.3f}  "
            f"{row['direction_qoq']}"
        )
    print(f"  {'─' * 80}")
    print(f"  Showing last {min(tail, len(valid))} quarters  "
          f"({valid.index[0].date()} → {valid.index[-1].date()})")


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    tail = 20
    if "--tail" in args:
        idx  = args.index("--tail")
        tail = int(args[idx + 1])

    df   = build_margin_series()
    snap = get_latest_snapshot(df)
    print_status(snap)

    if "--history" in args:
        print_history(df, tail=tail)

    if "--save" in args:
        save_snapshot(snap)
        print("\n  ✅  Layer 0 input updated.")
        print("      Run apex_strategy.py to see the current regime.")
    else:
        print("\n  Tip: run with --save to update the Layer 0 regime snapshot.")
        print("       run with --history to view the full quarterly table.")
