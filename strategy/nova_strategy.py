"""
NOVA Bear Monitor — Semiconductor Sector Risk Alert
====================================================
Daily severity alert for the semiconductor sector bear risk.
Designed as a companion to APEX — you decide the action.

5-level severity scale (0 All Clear → 4 Extreme) derived from
7 bear-focused signals with 3-day confirmation (5-day for Level 4).

Layer 0 — Semiconductor cycle regime (NVDA+MU quarterly revenue).
          Auto-updated by GitHub Action via soxx_cycle_tracker.py --save.

Usage:
    python nova_strategy.py              → today's bear monitor report
    python nova_strategy.py --backtest   → + 5-year episode table
    python nova_strategy.py --json       → JSON output
    python nova_strategy.py --ytd        → YTD performance table

⚠ NOT financial advice. Use at your own risk.
"""

import warnings
warnings.filterwarnings("ignore")

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance pandas numpy")
    raise

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_SNAP = os.path.join(_REPO_ROOT, "soxx_output", "soxx_cycle_latest.json")

# ── Configuration ──────────────────────────────────────────────────────────

_CONFIG = {
    "signal_ticker": "SOXX",
    "spy_ticker":    "SPY",
    "lead1_ticker":  "NVDA",
    "lead2_ticker":  "MU",
    "data_period":   "1y",
    "vix_period":    "3mo",
    "lead_period":   "2mo",
    "dd_lookback":   126,     # ~6 months for rolling drawdown
}


# ══════════════════════════════════════════════════════════════════
#  LAYER 0 — SEMICONDUCTOR CYCLE REGIME  (quarterly)
# ══════════════════════════════════════════════════════════════════

from soxx_cycle_tracker import REGIME_DEFINITIONS


def get_regime_params(cycle_file: str = CYCLE_SNAP) -> tuple:
    """
    Load the quarterly semiconductor cycle snapshot.
    Returns (regime_params, regime_label, sub_label, cycle_data).
    Falls back to NEUTRAL if the file is missing.
    """
    try:
        snap = json.load(open(cycle_file))
        regime = snap.get("regime", "NEUTRAL")
        sub    = snap.get("sub_label", "")
        return REGIME_DEFINITIONS[regime], regime, sub, snap
    except Exception:
        return REGIME_DEFINITIONS["NEUTRAL"], "NEUTRAL", "", {}


REGIME_PARAMS, CURRENT_REGIME, CURRENT_SUB, CYCLE_DATA = get_regime_params()


# ══════════════════════════════════════════════════════════════════
#  DATA & INDICATORS
# ══════════════════════════════════════════════════════════════════

def fetch_data(cfg: dict = None) -> dict:
    """Download price data required for the bear monitor."""
    if cfg is None:
        cfg = _CONFIG
    print("📡  Fetching market data…")

    def dl(ticker, period):
        try:
            df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
            if df is None or df.empty:
                raise ValueError(f"No data for {ticker}")
            s = df["Close"].squeeze()
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            s = s.dropna()
            if len(s) == 0:
                raise ValueError(f"Empty series for {ticker}")
            return s
        except Exception as e:
            print(f"   ⚠  {ticker}: {e}")
            return pd.Series(dtype=float)

    data = {
        "soxx": dl(cfg["signal_ticker"], cfg["data_period"]),
        "spy":  dl(cfg["spy_ticker"],    cfg["data_period"]),
        "nvda": dl(cfg["lead1_ticker"],  cfg["lead_period"]),
        "mu":   dl(cfg["lead2_ticker"],  cfg["lead_period"]),
        "vix":  dl("^VIX",              cfg["vix_period"]),
    }

    missing = [k for k, v in data.items() if len(v) < 20]
    if missing:
        raise ConnectionError(f"Data missing for: {missing}. Check internet access.")

    print(f"   SOXX : {len(data['soxx'])} days  "
          f"latest {data['soxx'].index[-1].date()}  ${data['soxx'].iloc[-1]:.2f}")
    print(f"   NVDA : ${data['nvda'].iloc[-1]:.2f}   "
          f"MU: ${data['mu'].iloc[-1]:.2f}   "
          f"VIX: {data['vix'].iloc[-1]:.2f}")
    return data


def calc_indicators(data: dict, cfg: dict = None) -> dict:
    """Compute technical indicators used by the bear monitor."""
    if cfg is None:
        cfg = _CONFIG
    soxx = data["soxx"]
    spy  = data["spy"]
    nvda = data["nvda"]
    vix  = data["vix"]

    ema50  = soxx.ewm(span=50,  adjust=False).mean()
    ema200 = soxx.ewm(span=200, adjust=False).mean()

    lookback = cfg.get("dd_lookback", 126)
    dd_126   = (soxx / soxx.rolling(lookback).max() - 1) * 100
    mom60    = (soxx / soxx.shift(60) - 1) * 100

    soxx_60  = soxx / soxx.shift(60)
    spy_60   = spy  / spy.shift(60)
    rel_str  = (soxx_60 / spy_60 - 1) * 100

    nvda_aligned = nvda.reindex(soxx.index, method="ffill")
    nvda_mom60   = (nvda_aligned / nvda_aligned.shift(60) - 1) * 100

    vix_aligned = vix.reindex(soxx.index, method="ffill")
    vix_5d      = vix_aligned.diff(5)

    return {
        "ema50":       ema50,
        "ema200":      ema200,
        "dd_126":      dd_126,
        "mom60":       mom60,
        "rel_str":     rel_str,
        "nvda_mom60":  nvda_mom60,
        "vix":         vix_aligned,
        "vix_5d":      vix_5d,
        "soxx_price":  soxx,
    }


# ══════════════════════════════════════════════════════════════════
#  BEAR MONITOR — SEVERITY SCORING
# ══════════════════════════════════════════════════════════════════

_BEAR_LEVEL_LABELS = {
    0: ("All Clear",  "🟢"),
    1: ("Watch",      "🟡"),
    2: ("Caution",    "🟠"),
    3: ("Bear Alert", "🔴"),
    4: ("Extreme",    "⚫"),
}


def _bear_risk_score(ind: dict) -> tuple:
    """
    Compute 7-signal bear risk score (positive = more bearish).
    Returns (total_score, signal_scores_dict, signal_notes_dict).
    """
    def safe(s, idx=-1):
        try:
            v = s.iloc[idx]
            return float(v) if not pd.isna(v) else None
        except Exception:
            return None

    m60     = safe(ind["mom60"])
    ema50   = safe(ind["ema50"])
    ema200  = safe(ind["ema200"])
    rel_str = safe(ind["rel_str"])
    vix     = safe(ind["vix"])
    vix_5d  = safe(ind.get("vix_5d", pd.Series(dtype=float)))
    dd126   = safe(ind["dd_126"])
    nvda60  = safe(ind.get("nvda_mom60", pd.Series(dtype=float)))

    scores = {}; notes = {}; total = 0

    # S1: SOXX 60d momentum
    if m60 is not None:
        if   m60 < -20: s, n = 3, f"🔴 Steep decline     SOXX 60d {m60:+.1f}%"
        elif m60 < -10: s, n = 2, f"🔴 Downtrend         SOXX 60d {m60:+.1f}%"
        elif m60 <  -5: s, n = 1, f"⚠️  Weakening         SOXX 60d {m60:+.1f}%"
        elif m60 <   5: s, n = 0, f"⚪ Flat               SOXX 60d {m60:+.1f}%"
        else:           s, n = -1, f"✅ Uptrend            SOXX 60d {m60:+.1f}%"
    else: s, n = 0, "⚪ Insufficient data"
    scores["mom60"] = s; notes["mom60"] = n; total += s

    # S2: EMA50/200 major trend
    if ema50 and ema200:
        if ema50 < ema200:
            s, n = 2, f"🔴 Death cross        EMA50 {ema50:.0f} < EMA200 {ema200:.0f}"
        else:
            s, n = -2, f"✅ Golden cross       EMA50 {ema50:.0f} > EMA200 {ema200:.0f}"
    else: s, n = 0, "⚪ Insufficient data"
    scores["ema_major"] = s; notes["ema_major"] = n; total += s

    # S3: Sector rotation (SOXX vs SPY)
    if rel_str is not None:
        if   rel_str < -15: s, n = 2, f"🔴 Sector exodus     SOXX vs SPY 60d {rel_str:+.1f}%"
        elif rel_str <  -5: s, n = 1, f"⚠️  Underperforming   SOXX vs SPY 60d {rel_str:+.1f}%"
        elif rel_str <   5: s, n = 0, f"⚪ Neutral            SOXX vs SPY 60d {rel_str:+.1f}%"
        else:               s, n = -1, f"✅ Sector leader      SOXX vs SPY 60d {rel_str:+.1f}%"
    else: s, n = 0, "⚪ Insufficient data"
    scores["rel_str"] = s; notes["rel_str"] = n; total += s

    # S4: VIX level
    if vix is not None:
        if   vix > 35: s, n = 3, f"🔴 Extreme fear       VIX {vix:.1f}"
        elif vix > 28: s, n = 2, f"🔴 High fear          VIX {vix:.1f}"
        elif vix > 22: s, n = 1, f"⚠️  Elevated           VIX {vix:.1f}"
        elif vix > 18: s, n = 0, f"⚪ Neutral             VIX {vix:.1f}"
        else:          s, n = -1, f"✅ Calm               VIX {vix:.1f}"
    else: s, n = 0, "⚪ Unavailable"
    scores["vix"] = s; notes["vix"] = n; total += s

    # S5: VIX 5-day change (fear acceleration)
    if vix_5d is not None:
        if   vix_5d >  5: s, n = 2, f"🔴 VIX spiking        5d Δ{vix_5d:+.1f}"
        elif vix_5d >  2: s, n = 1, f"⚠️  VIX rising         5d Δ{vix_5d:+.1f}"
        elif vix_5d > -2: s, n = 0, f"⚪ VIX stable          5d Δ{vix_5d:+.1f}"
        else:             s, n = -1, f"✅ VIX falling        5d Δ{vix_5d:+.1f}"
    else: s, n = 0, "⚪ Insufficient data"
    scores["vix_5d"] = s; notes["vix_5d"] = n; total += s

    # S6: SOXX 6-month drawdown
    if dd126 is not None:
        if   dd126 < -25: s, n = 3, f"🔴 Deep crash         SOXX 6m DD {dd126:.1f}%"
        elif dd126 < -15: s, n = 2, f"🔴 Bear territory     SOXX 6m DD {dd126:.1f}%"
        elif dd126 <  -8: s, n = 1, f"⚠️  Correction         SOXX 6m DD {dd126:.1f}%"
        else:             s, n = 0, f"✅ Near highs          SOXX 6m DD {dd126:.1f}%"
    else: s, n = 0, "⚪ Insufficient data"
    scores["dd126"] = s; notes["dd126"] = n; total += s

    # S7: NVDA 60d momentum (AI/compute demand proxy)
    if nvda60 is not None:
        if   nvda60 < -25: s, n = 2, f"🔴 AI demand crashing NVDA 60d {nvda60:+.1f}%"
        elif nvda60 < -10: s, n = 1, f"⚠️  AI demand softening NVDA 60d {nvda60:+.1f}%"
        elif nvda60 <   5: s, n = 0, f"⚪ NVDA neutral        NVDA 60d {nvda60:+.1f}%"
        else:              s, n = -1, f"✅ AI demand strong   NVDA 60d {nvda60:+.1f}%"
    else: s, n = 0, "⚪ Insufficient NVDA data"
    scores["nvda60"] = s; notes["nvda60"] = n; total += s

    return total, scores, notes


def _bear_severity(score: int) -> tuple:
    """Bear risk score → (level 0-4, label, icon)."""
    if   score <= 1:  lv = 0
    elif score <= 3:  lv = 1
    elif score <= 6:  lv = 2
    elif score <= 10: lv = 3
    else:             lv = 4
    lbl, icon = _BEAR_LEVEL_LABELS[lv]
    return lv, lbl, icon


def _confirmed_level_from_scores(recent_scores: list) -> int:
    """
    Level 4 confirmed after 5 consecutive days (reduces false alarms at bounces).
    Levels 1–3 confirmed after 3 consecutive days.
    """
    if not recent_scores:
        return 0
    raw_levels = [_bear_severity(s)[0] for s in recent_scores]
    n = len(raw_levels)

    if n >= 5 and all(l >= 4 for l in raw_levels[-5:]):
        return 4
    for target in [3, 2, 1]:
        window = raw_levels[-min(3, n):]
        if all(l >= target for l in window):
            return target
    return 0


def _recovery_conditions(level: int, ind: dict) -> list:
    """Return list of conditions that must clear to de-escalate from `level`."""
    def safe(s):
        try:
            v = s.iloc[-1]; return float(v) if not pd.isna(v) else None
        except Exception: return None

    dd126 = safe(ind["dd_126"]); vix   = safe(ind["vix"])
    ema50 = safe(ind["ema50"]);  ema200 = safe(ind["ema200"])
    m60   = safe(ind["mom60"])

    conditions = []
    if level >= 3:
        if dd126 is not None and dd126 < -8:
            conditions.append(
                f"✗ SOXX must reclaim 6-month high  (currently {dd126:.1f}% below)")
        if vix is not None and vix > 22:
            conditions.append(f"✗ VIX must fall below 22  (currently {vix:.1f})")
        if ema50 and ema200 and ema50 < ema200:
            conditions.append("✗ EMA golden cross required  (currently death cross)")
    if level >= 2:
        if m60 is not None and m60 < 0:
            conditions.append(
                f"✗ SOXX 60d momentum must turn positive  (currently {m60:+.1f}%)")
    conditions.append("→ All of the above must hold for 5 consecutive trading days")
    return conditions


# ══════════════════════════════════════════════════════════════════
#  MAIN ENTRY — RUN TODAY'S BEAR MONITOR
# ══════════════════════════════════════════════════════════════════

def run_nova_bear_monitor(verbose: bool = True) -> dict:
    """
    Run the NOVA Bear Monitor for today.
    Downloads ~1 year of data, computes last 7 days of bear risk scores,
    applies confirmation rules, and prints the severity report.
    """
    data = fetch_data(_CONFIG)
    ind  = calc_indicators(data, _CONFIG)

    regime_params, regime_label, regime_sub, cycle_data = get_regime_params()

    # Compute bear risk score for last 7 trading days (confirmation window)
    recent_scores = []
    n_ind = len(ind["soxx_price"])
    for offset in range(min(7, n_ind), 0, -1):
        sub_ind = {k: v.iloc[:-offset] if offset > 0 else v
                   for k, v in ind.items() if hasattr(v, "iloc")}
        s, _, _ = _bear_risk_score(sub_ind)
        recent_scores.append(s)

    today_score, signals, notes = _bear_risk_score(ind)
    raw_level, raw_label, _      = _bear_severity(today_score)
    conf_level                   = _confirmed_level_from_scores(recent_scores)
    conf_label, conf_icon        = _BEAR_LEVEL_LABELS[conf_level]
    recovery                     = _recovery_conditions(conf_level, ind)

    conf_levels_recent = [_bear_severity(s)[0] for s in recent_scores]
    days_at_level = sum(1 for l in reversed(conf_levels_recent) if l == conf_level)

    try:
        soxx_p      = float(ind["soxx_price"].iloc[-1])
        soxx_ath    = float(ind["soxx_price"].cummax().iloc[-1])
        soxx_dd_ath = (soxx_p / soxx_ath - 1) * 100
    except Exception:
        soxx_p = soxx_ath = soxx_dd_ath = float("nan")

    if verbose:
        w = 70
        print()
        print("═" * w)
        print(f"  NOVA Bear Monitor  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  Semiconductor Sector Risk Assessment")
        print("═" * w)

        print(f"\n  {conf_icon}  STATUS:  Level {conf_level} — {conf_label.upper()}")
        if days_at_level <= 7:
            print(f"       (at this level for ~{days_at_level} of last 7 days)")
        if conf_level != raw_level:
            print(f"       (raw today: Level {raw_level} — {raw_label}; "
                  f"confirmation {'pending' if raw_level > conf_level else 'clearing'})")

        print(f"\n{'─'*w}")
        print(f"  BEAR RISK SCORE:  {today_score:+d}  "
              f"({'very low' if today_score <= 0 else 'low' if today_score <= 3 else 'moderate' if today_score <= 6 else 'high' if today_score <= 10 else 'extreme'})")
        print(f"  CONFIRMED LEVEL:  {conf_level}  "
              f"({'3-day' if conf_level < 4 else '5-day'} confirmation applied)")

        print(f"\n{'─'*w}")
        print("  SIGNAL BREAKDOWN  (7 bear-focused indicators):")
        print(f"{'─'*w}")
        sig_labels = {
            "mom60":     "SOXX 60d Momentum    ",
            "ema_major": "EMA50 / EMA200 Trend ",
            "rel_str":   "SOXX vs SPY 60d      ",
            "vix":       "VIX Level            ",
            "vix_5d":    "VIX 5-day Change     ",
            "dd126":     "SOXX 6m Drawdown     ",
            "nvda60":    "NVDA 60d Momentum    ",
        }
        for key, lbl in sig_labels.items():
            sc   = signals.get(key, 0)
            note = notes.get(key, "")
            bar  = "█" * abs(sc) if sc != 0 else "·"
            sign = "▲" if sc < 0 else ("▼" if sc > 0 else "─")
            print(f"  {lbl}  {sign} risk {sc:+2d}  {bar:<3}  {note}")

        print(f"\n{'─'*w}")
        if conf_level == 0:
            print("  MARKET CONTEXT:")
            try:
                dd126_v = float(ind["dd_126"].iloc[-1])
                vix_v   = float(ind["vix"].iloc[-1])
                print(f"     SOXX current: ${soxx_p:.2f}   "
                      f"6m DD: {dd126_v:+.1f}%   ATH DD: {soxx_dd_ath:+.1f}%")
                print(f"     VIX: {vix_v:.1f}   Regime: {regime_label}  [{regime_sub}]")
            except Exception:
                pass
            print(f"\n  WHAT WOULD TRIGGER AN ALERT:")
            print(f"     Level 1 (Watch):       Score ≥ 2  "
                  f"(needs {max(0, 2 - today_score)} more risk points)")
            print(f"     Level 2 (Caution):     Score ≥ 4  — 3-day confirmation")
            print(f"     Level 3 (Bear Alert):  Score ≥ 7  — 3-day confirmation  [primary signal]")
            print(f"     Level 4 (Extreme):     Score ≥ 11 — 5-day confirmation")
        else:
            print("  ACTIVE RISK FACTORS:")
            for key, sc in signals.items():
                if sc >= 2:
                    print(f"     ✦ {notes[key]}")
            print(f"\n  CONTEXT:")
            print(f"     SOXX current: ${soxx_p:.2f}   ATH drawdown: {soxx_dd_ath:+.1f}%")
            print(f"     Regime: {regime_label}  [{regime_sub}]")
            if conf_level >= 2:
                print(f"\n  RECOVERY CONDITIONS  (to clear to Level {max(0, conf_level-1)}):")
                for cond in recovery:
                    print(f"     {cond}")
            print(f"\n  YOUR DECISION — suggested considerations:")
            if conf_level == 1:
                print("     Monitor positions more closely.  No urgent action.")
            elif conf_level == 2:
                print("     → Consider reducing SOXL/USD exposure partially")
                print("     → Consider moving from SOXL → USD or USD → SOXX")
            elif conf_level == 3:
                print("     → Strong case for moving to SOXX (1×) or SGOV")
                print("     → If holding SOXL: est. further risk −30% to −60%")
                print("     → If holding USD:  est. further risk −20% to −40%")
            else:
                print("     → Maximum risk conditions. SGOV strongly indicated.")
                print("     → Historical Extreme bears: avg −47% from SOXX peak")

        print()
        print("  ⚠  This is an alert signal only. You decide the action.")
        print("  ⚠  NOT financial advice.")
        print("═" * w)
        print()

    return {
        "today_score":     today_score,
        "raw_level":       raw_level,
        "raw_label":       raw_label,
        "confirmed_level": conf_level,
        "confirmed_label": conf_label,
        "days_at_level":   days_at_level,
        "regime":          regime_label,
        "signal_scores":   signals,
        "signal_notes":    notes,
        "recovery":        recovery,
        "soxx_price":      soxx_p,
        "soxx_dd_ath":     soxx_dd_ath,
        "timestamp":       datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
#  5-YEAR BACKTEST — EPISODE TABLE
# ══════════════════════════════════════════════════════════════════

def backtest_bear_monitor() -> None:
    """
    5-year backtest: downloads history, computes daily bear risk scores with
    3/5-day confirmation, and prints an episode table.
    """
    print("\n📡  Downloading 5-year history for bear monitor backtest…")

    def dl(ticker):
        df = yf.download(ticker, start="2018-01-01",
                         end=datetime.now().strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        s = df["Close"].squeeze()
        if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
        return s.dropna()

    soxx_h = dl("SOXX"); usd_h  = dl("USD")
    spy_h  = dl("SPY");  nvda_h = dl("NVDA")
    vix_h  = dl("^VIX")

    idx    = soxx_h.index
    ema50  = soxx_h.ewm(span=50,  adjust=False).mean()
    ema200 = soxx_h.ewm(span=200, adjust=False).mean()
    m60    = (soxx_h / soxx_h.shift(60) - 1) * 100
    rs_60  = ((soxx_h / soxx_h.shift(60)) /
              (spy_h.reindex(idx, method="ffill") /
               spy_h.reindex(idx, method="ffill").shift(60)) - 1) * 100
    dd126  = (soxx_h / soxx_h.rolling(126).max() - 1) * 100
    vix_a  = vix_h.reindex(idx, method="ffill")
    vix_5d = vix_a.diff(5)
    nvda_a = nvda_h.reindex(idx, method="ffill")
    nvda60 = (nvda_a / nvda_a.shift(60) - 1) * 100
    usd_a  = usd_h.reindex(idx, method="ffill")

    bt_ind_keys = ["mom60", "ema50", "ema200", "rel_str", "vix",
                   "vix_5d", "dd_126", "nvda_mom60"]

    raw_scores = []
    for i in range(len(idx)):
        sub = {
            "mom60":      m60.iloc[:i+1],
            "ema50":      ema50.iloc[:i+1],
            "ema200":     ema200.iloc[:i+1],
            "rel_str":    rs_60.iloc[:i+1],
            "vix":        vix_a.iloc[:i+1],
            "vix_5d":     vix_5d.iloc[:i+1],
            "dd_126":     dd126.iloc[:i+1],
            "nvda_mom60": nvda60.iloc[:i+1],
        }
        s, _, _ = _bear_risk_score(sub)
        raw_scores.append(s)

    raw_scores = pd.Series(raw_scores, index=idx)

    conf_levels = []
    for i in range(len(idx)):
        window = raw_scores.iloc[max(0, i-6):i+1].tolist()
        conf_levels.append(_confirmed_level_from_scores(window))
    conf_levels = pd.Series(conf_levels, index=idx)

    # Build episode table (start/end dates, max level, USD return during episode)
    start_5y = pd.Timestamp("2021-01-01")
    mask     = idx >= start_5y

    print(f"\n{'─'*76}")
    print(f"  5-Year Bear Alert Episodes  (2021–{datetime.now().year})")
    print(f"{'─'*76}")
    print(f"  {'Start':<12} {'End':<12} {'MaxLv':>5}  {'Days':>4}  "
          f"{'USD during':>10}  {'Signal'}")
    print(f"{'─'*76}")

    in_alert = False
    ep_start = None; ep_max = 0

    for date, lv in conf_levels[mask].items():
        if lv >= 1 and not in_alert:
            in_alert = True; ep_start = date; ep_max = lv
        elif in_alert:
            ep_max = max(ep_max, lv)
            if lv == 0:
                # episode ended
                ep_end = date
                ep_days = (ep_end - ep_start).days
                lbl, icon = _BEAR_LEVEL_LABELS[ep_max]
                usd_slice = usd_a[ep_start:ep_end]
                if len(usd_slice) >= 2:
                    usd_ret = (usd_slice.iloc[-1] / usd_slice.iloc[0] - 1) * 100
                    ret_str = f"{usd_ret:+.1f}%"
                    correct = "✓ correct" if usd_ret < 0 else "✗ false alarm"
                else:
                    ret_str = "n/a"; correct = ""
                print(f"  {ep_start.strftime('%Y-%m-%d'):<12} "
                      f"{ep_end.strftime('%Y-%m-%d'):<12} "
                      f"{icon} L{ep_max:1d}  {ep_days:>4}d  "
                      f"{ret_str:>10}  {correct}")
                in_alert = False; ep_start = None; ep_max = 0

    if in_alert:
        lbl, icon = _BEAR_LEVEL_LABELS[ep_max]
        print(f"  {ep_start.strftime('%Y-%m-%d'):<12} {'(ongoing)':<12} "
              f"{icon} L{ep_max:1d}  {'?':>4}d  {'?':>10}")

    print(f"{'─'*76}")
    print()


# ══════════════════════════════════════════════════════════════════
#  YTD PERFORMANCE TABLE
# ══════════════════════════════════════════════════════════════════

def quick_ytd_check() -> None:
    print("\n📊  Year-to-Date Performance  (semiconductor universe)")
    print("─" * 56)
    tickers = {
        "SOXL": "SOXL  (3×)",
        "USD":  "USD   (2×)",
        "SOXX": "SOXX  (1×)",
        "MU":   "MU        ",
        "NVDA": "NVDA      ",
    }
    ytd_start = pd.Timestamp(f"{datetime.now().year}-01-01")
    for tk, label in tickers.items():
        try:
            s = yf.download(tk, period="1y", progress=False,
                            auto_adjust=True)["Close"].squeeze()
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            s = s.dropna()
            s_ytd = s[s.index >= ytd_start]
            if len(s_ytd) < 2:
                continue
            ret = (s_ytd.iloc[-1] / s_ytd.iloc[0] - 1) * 100
            dd  = ((s_ytd / s_ytd.cummax()) - 1).min() * 100
            print(f"  {label}   YTD: {ret:+6.1f}%   MaxDD: {dd:+6.1f}%   "
                  f"${s.iloc[-1]:.2f}")
        except Exception:
            pass
    print()


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "--ytd" in args:
        quick_ytd_check()
    elif "--json" in args:
        import json as _json, contextlib
        with contextlib.redirect_stdout(sys.stderr):
            result = run_nova_bear_monitor(verbose=False)
        out = {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
               for k, v in result.items()
               if k not in ("signal_scores", "signal_notes", "recovery")}
        print(_json.dumps(out, indent=2))
    else:
        result = run_nova_bear_monitor(verbose=True)
        if "--backtest" in args:
            backtest_bear_monitor()
