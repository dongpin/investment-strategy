"""
NOVA Strategy — Semiconductor Cycle Tracker
============================================
Quarterly Layer 0 regime tool.
Fetches NVDA (compute/AI demand) and MU (memory/DRAM demand) quarterly
revenue from Yahoo Finance and classifies the semiconductor cycle:
  BULL / NEUTRAL / BEAR

Output: soxx_output/soxx_cycle_latest.json  (read by nova_strategy.py)

Usage:
    python soxx_cycle_tracker.py           # print current cycle state
    python soxx_cycle_tracker.py --save    # save JSON snapshot for Layer 0

Update quarterly after NVDA or MU earnings release.
"""

import os
import json
import sys
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance pandas")
    raise

_REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR  = os.path.join(_REPO_ROOT, "soxx_output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "soxx_cycle_latest.json")


# ══════════════════════════════════════════════════════════════════
#  REGIME DEFINITIONS  (read by nova_strategy.py)
# ══════════════════════════════════════════════════════════════════

REGIME_DEFINITIONS = {
    "BULL": {
        "target_vol":            0.40,   # aggressive vol budget in bull cycle
        "target_vol_bull":       0.50,   # score ≥ 6 AND VIX < 20
        "vix_threshold":         30.0,   # higher tolerance in bull
        "dd_threshold":         -18.0,   # % drawdown from 6-month high → no leverage
        "extreme_dd_threshold": -35.0,   # → force cash
        "max_tier":              "soxl",
    },
    "NEUTRAL": {
        "target_vol":            0.35,
        "target_vol_bull":       0.45,
        "vix_threshold":         28.0,
        "dd_threshold":         -15.0,
        "extreme_dd_threshold": -30.0,
        "max_tier":              "usd_soxl",
    },
    "BEAR": {
        "target_vol":            0.28,
        "target_vol_bull":       0.35,
        "vix_threshold":         22.0,
        "dd_threshold":         -10.0,
        "extreme_dd_threshold": -22.0,
        "max_tier":              "soxx",
    },
}


# ══════════════════════════════════════════════════════════════════
#  REVENUE FETCHING
# ══════════════════════════════════════════════════════════════════

def fetch_quarterly_revenue(ticker: str) -> pd.Series:
    """Fetch last 8+ quarters of Total Revenue from Yahoo Finance."""
    t = yf.Ticker(ticker)
    try:
        fin = t.quarterly_financials
        if fin is None or fin.empty:
            return pd.Series(dtype=float)
        for label in ["Total Revenue", "Revenue", "TotalRevenue"]:
            if label in fin.index:
                rev = fin.loc[label].dropna()
                rev = rev.sort_index()          # ascending date order
                return rev
        print(f"  ⚠  No revenue row for {ticker}.  Rows: {list(fin.index[:8])}")
        return pd.Series(dtype=float)
    except Exception as e:
        print(f"  ⚠  {ticker} financials error: {e}")
        return pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════
#  GROWTH CLASSIFICATION
# ══════════════════════════════════════════════════════════════════

# (label, lower_bound_inclusive, upper_bound_exclusive, score)
GROWTH_BRACKETS = [
    ("MAJOR_EXPANSION",   30,  float("inf"),  +3),
    ("EXPANSION",         10,  30,            +2),
    ("FLAT",              -5,  10,             0),
    ("CONTRACTION",      -20,  -5,            -1),
    ("MAJOR_CONTRACTION", float("-inf"), -20, -2),
]

def classify_growth(growth_pct: float) -> tuple:
    """Return (direction_label, score) for a % revenue growth figure."""
    for label, lo, hi, score in GROWTH_BRACKETS:
        if lo <= growth_pct < hi:
            return label, score
    return "FLAT", 0


# ══════════════════════════════════════════════════════════════════
#  REGIME COMPUTATION
# ══════════════════════════════════════════════════════════════════

def compute_cycle_regime(nvda_rev: pd.Series, mu_rev: pd.Series) -> dict:
    """
    Compute semiconductor cycle regime from NVDA and MU revenue.

    NVDA YoY growth: weighted 1.5× (AI/compute demand, higher sensitivity)
    MU   YoY growth: weighted 1.0× (memory/DRAM, cyclical leading indicator)

    Combined score:
        ≥  4  → BULL
        ≤ -2  → BEAR
        else  → NEUTRAL
    """
    result = {
        "nvda_yoy_pct":    None, "nvda_yoy_dir": "N/A", "nvda_yoy_score": 0,
        "nvda_qoq_pct":    None, "nvda_latest_q": "N/A",
        "mu_yoy_pct":      None, "mu_yoy_dir":   "N/A", "mu_yoy_score": 0,
        "mu_qoq_pct":      None, "mu_latest_q":  "N/A",
        "total_score":     0,
        "sub_label":       "",
        "regime":          "NEUTRAL",
        "saved_at":        datetime.now().isoformat(),
    }

    # NVDA
    if len(nvda_rev) >= 5:
        yoy = (nvda_rev.iloc[-1] / nvda_rev.iloc[-5] - 1) * 100
        qoq = (nvda_rev.iloc[-1] / nvda_rev.iloc[-2] - 1) * 100
        d, s = classify_growth(yoy)
        result.update({
            "nvda_yoy_pct":  round(yoy, 1),
            "nvda_yoy_dir":  d,
            "nvda_yoy_score": s,
            "nvda_qoq_pct":  round(qoq, 1),
            "nvda_latest_q": str(nvda_rev.index[-1].date()),
        })
    elif len(nvda_rev) >= 2:
        qoq = (nvda_rev.iloc[-1] / nvda_rev.iloc[-2] - 1) * 100
        result["nvda_qoq_pct"]  = round(qoq, 1)
        result["nvda_latest_q"] = str(nvda_rev.index[-1].date())

    # MU
    if len(mu_rev) >= 5:
        yoy = (mu_rev.iloc[-1] / mu_rev.iloc[-5] - 1) * 100
        qoq = (mu_rev.iloc[-1] / mu_rev.iloc[-2] - 1) * 100
        d, s = classify_growth(yoy)
        result.update({
            "mu_yoy_pct":   round(yoy, 1),
            "mu_yoy_dir":   d,
            "mu_yoy_score": s,
            "mu_qoq_pct":   round(qoq, 1),
            "mu_latest_q":  str(mu_rev.index[-1].date()),
        })
    elif len(mu_rev) >= 2:
        qoq = (mu_rev.iloc[-1] / mu_rev.iloc[-2] - 1) * 100
        result["mu_qoq_pct"]  = round(qoq, 1)
        result["mu_latest_q"] = str(mu_rev.index[-1].date())

    # Combined score: NVDA weighted 1.5×, MU weighted 1.0×
    ns = result["nvda_yoy_score"]
    ms = result["mu_yoy_score"]
    total = int(round(ns * 1.5 + ms * 1.0))
    result["total_score"] = total

    # Sub-label
    if ns > 0 and ms > 0:
        sub = "BULL-BOTH"
    elif ns > 0:
        sub = "BULL-COMPUTE"
    elif ms > 0:
        sub = "BULL-MEMORY"
    elif ns < 0 and ms < 0:
        sub = "BEAR-BOTH"
    elif ns < 0:
        sub = "BEAR-COMPUTE"
    elif ms < 0:
        sub = "BEAR-MEMORY"
    else:
        sub = ""
    result["sub_label"] = sub

    # Classify
    if total >= 4:
        result["regime"] = "BULL"
    elif total <= -2:
        result["regime"] = "BEAR"
    else:
        result["regime"] = "NEUTRAL"

    return result


# ══════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════

def print_cycle_report(r: dict) -> None:
    w = 66
    icons = {"BULL": "🟢", "NEUTRAL": "⚪", "BEAR": "🔴"}
    regime = r["regime"]
    params = REGIME_DEFINITIONS[regime]
    icon   = icons.get(regime, "⚪")
    sub    = f"  [{r['sub_label']}]" if r.get("sub_label") else ""

    print()
    print("═" * w)
    print(f"  NOVA Strategy — Semiconductor Cycle Tracker")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * w)

    print(f"\n  ── NVDA  (Compute / AI demand)  ──────────────────────")
    if r["nvda_yoy_pct"] is not None:
        s = r["nvda_yoy_score"]
        print(f"     YoY revenue: {r['nvda_yoy_pct']:+.1f}%  →  {r['nvda_yoy_dir']}  "
              f"(score {s:+d}, weight 1.5×  →  {s*1.5:+.1f})")
        if r["nvda_qoq_pct"] is not None:
            print(f"     QoQ revenue: {r['nvda_qoq_pct']:+.1f}%")
        print(f"     Latest quarter: {r['nvda_latest_q']}")
    else:
        print("     ⚠  No NVDA revenue data — falling back to NEUTRAL")

    print(f"\n  ── MU / Micron  (Memory / DRAM demand)  ──────────────")
    if r["mu_yoy_pct"] is not None:
        s = r["mu_yoy_score"]
        print(f"     YoY revenue: {r['mu_yoy_pct']:+.1f}%  →  {r['mu_yoy_dir']}  "
              f"(score {s:+d}, weight 1.0×  →  {s*1.0:+.1f})")
        if r["mu_qoq_pct"] is not None:
            print(f"     QoQ revenue: {r['mu_qoq_pct']:+.1f}%")
        print(f"     Latest quarter: {r['mu_latest_q']}")
    else:
        print("     ⚠  No MU revenue data — falling back to NEUTRAL")

    print(f"\n  ── Combined Score  ───────────────────────────────────")
    ns = r["nvda_yoy_score"]; ms = r["mu_yoy_score"]
    print(f"     NVDA: {ns:+d} × 1.5 = {ns*1.5:+.1f}   "
          f"MU: {ms:+d} × 1.0 = {ms*1.0:+.1f}   "
          f"Total: {r['total_score']:+d}")
    print(f"     (threshold: ≥+4 → BULL  |  ≤-2 → BEAR  |  else → NEUTRAL)")

    print(f"\n  {icon}  REGIME: {regime}{sub}")
    print(f"     target_vol={params['target_vol']:.0%}  "
          f"target_vol_bull={params['target_vol_bull']:.0%}")
    print(f"     vix_threshold={params['vix_threshold']}  "
          f"dd_threshold={params['dd_threshold']}%  "
          f"extreme_dd={params['extreme_dd_threshold']}%")
    print(f"     max_tier={params['max_tier']}")
    print()
    print("═" * w)
    print()


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    do_save = "--save" in sys.argv

    print("📡  Fetching quarterly revenue data…")
    nvda_rev = fetch_quarterly_revenue("NVDA")
    mu_rev   = fetch_quarterly_revenue("MU")

    if not nvda_rev.empty:
        print(f"  NVDA: {len(nvda_rev)} quarters  "
              f"(latest: {nvda_rev.index[-1].date()}  "
              f"${nvda_rev.iloc[-1]/1e9:.1f}B revenue)")
    else:
        print("  ⚠  NVDA: no data")

    if not mu_rev.empty:
        print(f"  MU:   {len(mu_rev)} quarters  "
              f"(latest: {mu_rev.index[-1].date()}  "
              f"${mu_rev.iloc[-1]/1e9:.1f}B revenue)")
    else:
        print("  ⚠  MU:   no data")

    r = compute_cycle_regime(nvda_rev, mu_rev)
    print_cycle_report(r)

    if do_save:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(OUTPUT_FILE, "w") as f:
            json.dump(r, f, indent=2)
        print(f"  ✅  Saved → {OUTPUT_FILE}")
        print("  Next update: after NVDA or MU next earnings release.\n")


if __name__ == "__main__":
    main()
