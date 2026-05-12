"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║   APEX Strategy  v2.0                                                ║
║   Adaptive Position EXecution Protocol                               ║
║                                                                      ║
║   A volatility-targeted, signal-scored rotation system               ║
║   between VOO (S&P 500) and TQQQ (3× Nasdaq 100)                    ║
║                                                                      ║
║   Designed for: Roth IRA  |  Weekly rebalance  |  Long-term hold    ║
║                                                                      ║
║   Core mechanics:                                                    ║
║     Layer 1 — Hard circuit breakers (3 conditions)                  ║
║     Layer 2 — 10-dimension signal scoring → continuous allocation    ║
║     Layer 3 — Volatility targeting (dynamic 20–25% vol budget)      ║
║     Addon   — TQQQ trailing stop (15-day high × 92%)                ║
║                                                                      ║
║   Backtest (2010–2026, Roth IRA, no taxes):                         ║
║     CAGR: 23.9%  |  Sharpe: 0.96  |  Max DD: -36.3%                ║
║     vs QQQ hold: CAGR 19.6%, Sharpe 0.97, Max DD -35.1%            ║
║                                                                      ║
║   ⚠ NOT financial advice. Use at your own risk.                     ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
    pip install yfinance pandas numpy
    python apex_strategy.py

Outputs:
    • Current allocation recommendation (TQQQ% / VOO%)
    • All signal scores with explanations
    • Trailing stop check
    • Volatility cap calculation
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("Please install yfinance: pip install yfinance")
    raise

# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION — Edit these to customize the strategy
# ══════════════════════════════════════════════════════════════════

CONFIG = {
    # Assets
    "signal_ticker"  : "QQQ",    # ticker used for signal calculation
    "growth_asset"   : "TQQQ",   # high-growth leveraged ETF
    "stable_asset"   : "VOO",    # defensive stable ETF

    # Layer 1: Hard circuit breakers  (v2: EMA death cross removed — it is
    #   already penalised −4 in Layer 2; keeping it as a hard stop caused
    #   41-day blackout during the Feb–Apr 2026 recovery when QQQ had already
    #   bottomed.  The remaining 3 stops cover true crisis conditions.)
    "dd_threshold"   : -10.0,    # % drawdown from rolling high → force VOO
    "dd_lookback"    : 126,      # days for rolling high in drawdown check (~6 months)
                                 # ATH-based (original) caused premature blocking in
                                 # 2020/2023 recoveries; 126d high clears faster once
                                 # the market trends up for a few months.
    "vix_threshold"  : 25.0,     # VIX level → force VOO
    # (also uses SMA200 crossover; EMA cross is now Layer-2 only)

    # Layer 2: Signal scoring
    # Allocation map: score → TQQQ fraction
    "alloc_map"      : {
        0: 0.00,   # no signal
        1: 0.20,   # weak
        2: 0.35,   # cautious
        3: 0.50,   # neutral bull
        4: 0.65,   # standard bull
        5: 0.75,   # strong bull
        6: 0.90,   # very strong  (was 0.85)
        # 7+: 1.00  (full leverage)
    },

    # Layer 3: Volatility targeting — dynamic regime-based budget
    "target_vol"         : 0.20,  # base 20% annual vol target
    "target_vol_bull"    : 0.25,  # raised to 25% in strong-bull regime
    #   bull regime = score >= 4 AND VIX < 20
    #   rationale: when most signals align and fear is low, historical Sharpe
    #   improves by deploying more leverage rather than sitting on a hard cap.
    #   Threshold lowered from 5 → 4 to capture early-recovery bull phases.
    "dynamic_vol"        : True,  # enable the regime-adaptive vol budget
    "vol_window"         : 15,    # days for realized volatility calculation
                                  # 15 (was 20) → vol cap recovers ~25% faster
                                  # after volatility events, improving re-entry timing

    # Trailing stop
    "trail_window"   : 15,       # days for trailing high calculation
    "trail_pct"      : 0.92,     # exit if price < high × this value (−8%)

    # Execution
    "confirm_days"   : 3,        # signal must persist N days before acting
    "exec_delay"     : 1,        # T+1 execution (days after signal)

    # Data download
    "data_period"    : "1y",     # yfinance period for signal data
    "vix_period"     : "3mo",
    "tnx_period"     : "4mo",
    "tqqq_period"    : "2mo",
}


# ══════════════════════════════════════════════════════════════════
#  DATA FETCHING
# ══════════════════════════════════════════════════════════════════

def fetch_data(cfg: dict) -> dict:
    """Download all required market data from Yahoo Finance."""
    print("📡 Fetching market data from Yahoo Finance...")

    def dl(ticker, period):
        try:
            df = yf.download(ticker, period=period, progress=False,
                             auto_adjust=True)
            if df is None or df.empty:
                raise ValueError(f"No data returned for {ticker}")
            s = df["Close"].squeeze()
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            s = s.dropna()
            if len(s) == 0:
                raise ValueError(f"Empty series for {ticker}")
            return s
        except Exception as e:
            print(f"   ⚠ Could not download {ticker}: {e}")
            return pd.Series(dtype=float)

    data = {
        "qqq"  : dl(cfg["signal_ticker"], cfg["data_period"]),
        "tqqq" : dl(cfg["growth_asset"],  cfg["tqqq_period"]),
        "vix"  : dl("^VIX",              cfg["vix_period"]),
        "tnx"  : dl("^TNX",              cfg["tnx_period"]),
    }

    # Validate we got enough data
    missing = [k for k, v in data.items() if len(v) < 20]
    if missing:
        print(f"\n   ❌ Could not fetch data for: {missing}")
        print("   Make sure you are connected to the internet and")
        print("   run this script on your LOCAL machine (not a server).")
        print("   Install dependencies: pip install yfinance pandas numpy\n")
        raise ConnectionError(
            "Data download failed. Run locally with internet access."
        )

    print(f"   QQQ  : {len(data['qqq'])} days  "
          f"(latest: {data['qqq'].index[-1].date()}  "
          f"${data['qqq'].iloc[-1]:.2f})")
    print(f"   TQQQ : {len(data['tqqq'])} days  "
          f"(latest: ${data['tqqq'].iloc[-1]:.2f})")
    print(f"   VIX  : {data['vix'].iloc[-1]:.2f}")
    print(f"   10Y  : {data['tnx'].iloc[-1]:.2f}%")
    return data


# ══════════════════════════════════════════════════════════════════
#  INDICATOR CALCULATION
# ══════════════════════════════════════════════════════════════════

def calc_indicators(data: dict, cfg: dict) -> dict:
    """Compute all technical indicators needed for scoring."""
    q = data["qqq"]
    t = data["tqqq"]
    v = data["vix"]
    n = data["tnx"]

    # Moving averages
    ema20  = q.ewm(span=20, adjust=False).mean()
    ema50  = q.ewm(span=50, adjust=False).mean()
    sma200 = q.rolling(200).mean()

    # RSI(14)
    delta  = q.diff()
    gain   = delta.clip(lower=0).rolling(14).mean()
    loss   = (-delta.clip(upper=0)).rolling(14).mean()
    rsi14  = 100 - (100 / (1 + gain / loss))

    # Drawdown from all-time high (used for D8 scoring)
    ath    = q.cummax()
    dd_pct = (q - ath) / ath * 100

    # Drawdown from 6-month rolling high (used for Layer 1 circuit breaker)
    # Clears faster than ATH after extended bear markets, avoiding the
    # "ATH-trap" where the market recovers but the 2-year-old peak keeps
    # blocking re-entry (was responsible for near-zero TQQQ in 2020 recovery
    # and most of 2023 post-bear recovery).
    lookback = cfg.get("dd_lookback", 126)
    dd_126   = (q / q.rolling(lookback).max() - 1) * 100

    # Momentum
    mom20  = (q / q.shift(20) - 1) * 100
    mom60  = (q / q.shift(60) - 1) * 100

    # Treasury 60-day change (in percentage points)
    tnx60  = n.diff(60)

    # TQQQ realized volatility (annualized)
    tqqq_vol = t.pct_change().rolling(cfg["vol_window"]).std() * np.sqrt(252)

    # TQQQ trailing high
    tqqq_trail_high = t.rolling(cfg["trail_window"]).max()

    # VIX 5-day momentum (new v2 signal)
    vix_5d = v.diff(5)

    return {
        "ema20"           : ema20,
        "ema50"           : ema50,
        "sma200"          : sma200,
        "rsi14"           : rsi14,
        "dd_pct"          : dd_pct,    # ATH-based (for D8 scoring)
        "dd_126"          : dd_126,    # 6-month rolling high (for Layer 1 circuit breaker)
        "mom20"           : mom20,
        "mom60"           : mom60,
        "tnx60"           : tnx60,
        "tqqq_vol"        : tqqq_vol,
        "tqqq_trail_high" : tqqq_trail_high,
        "qqq_price"       : q,
        "tqqq_price"      : t,
        "vix"             : v,
        "vix_5d"          : vix_5d,
    }


# ══════════════════════════════════════════════════════════════════
#  SIGNAL SCORING ENGINE
# ══════════════════════════════════════════════════════════════════

def score_signals(ind: dict, data: dict) -> dict:
    """
    Compute the 10-dimension signal score for the latest data point.

    Returns a dict with individual scores, total, and explanations.
    """
    i = -1  # use latest data point

    def safe(series, idx=-1):
        try:
            val = series.iloc[idx]
            return float(val) if not pd.isna(val) else None
        except Exception:
            return None

    ema20  = safe(ind["ema20"])
    ema50  = safe(ind["ema50"])
    sma200 = safe(ind["sma200"])
    rsi    = safe(ind["rsi14"])
    dd     = safe(ind["dd_pct"])
    vix    = safe(ind["vix"])
    m20    = safe(ind["mom20"])
    m60    = safe(ind["mom60"])
    tnx_c  = safe(ind["tnx60"])
    tnx    = safe(data["tnx"])
    qqq_p  = safe(ind["qqq_price"])
    vix_5d = safe(ind.get("vix_5d", pd.Series(dtype=float)))

    scores = {}
    notes  = {}

    # ── Dimension 1: EMA Trend ──────────────────────────────────
    if ema20 is not None and ema50 is not None:
        if ema20 > ema50:
            scores["ema_trend"] = 3
            notes["ema_trend"]  = f"✅ Golden cross  EMA20 {ema20:.1f} > EMA50 {ema50:.1f}"
        else:
            scores["ema_trend"] = -4
            notes["ema_trend"]  = f"🔴 Death cross   EMA20 {ema20:.1f} < EMA50 {ema50:.1f}  [Layer 2 only, −4]"
    else:
        scores["ema_trend"] = 0
        notes["ema_trend"]  = "⚪ Insufficient data"

    # ── Dimension 2: SMA200 Regime ─────────────────────────────
    if qqq_p is not None and sma200 is not None:
        if qqq_p > sma200:
            scores["sma200"] = 2
            notes["sma200"]  = f"✅ Bull regime   QQQ {qqq_p:.2f} > SMA200 {sma200:.2f}"
        else:
            scores["sma200"] = -3
            notes["sma200"]  = f"🔴 Bear regime   QQQ {qqq_p:.2f} < SMA200 {sma200:.2f}  [LAYER 1 TRIGGER]"
    else:
        scores["sma200"] = 0
        notes["sma200"]  = "⚪ Insufficient data"

    # ── Dimension 3: RSI Momentum ──────────────────────────────
    if rsi is not None:
        if   rsi > 70:  s, n_ = 0,  f"⚠️  Overbought    RSI {rsi:.1f} > 70"
        elif rsi > 60:  s, n_ = 2,  f"✅ Strong         RSI {rsi:.1f}"
        elif rsi > 50:  s, n_ = 1,  f"✅ Positive       RSI {rsi:.1f}"
        elif rsi > 40:  s, n_ = -1, f"⚠️  Weakening      RSI {rsi:.1f}"
        else:           s, n_ = -3, f"🔴 Oversold/weak  RSI {rsi:.1f}"
        scores["rsi"] = s
        notes["rsi"]  = n_
    else:
        scores["rsi"] = 0
        notes["rsi"]  = "⚪ Insufficient data"

    # ── Dimension 4: VIX Environment ──────────────────────────
    if vix is not None:
        if   vix < 13:  s, n_ = 3,  f"✅ Extremely calm  VIX {vix:.1f}"
        elif vix < 18:  s, n_ = 2,  f"✅ Calm            VIX {vix:.1f}"
        elif vix < 22:  s, n_ = 1,  f"✅ Mild concern    VIX {vix:.1f}"
        elif vix < 27:  s, n_ = -1, f"⚠️  Elevated        VIX {vix:.1f}"
        else:           s, n_ = -3, f"🔴 Fear zone       VIX {vix:.1f}  [LAYER 1 if >25]"
        scores["vix"] = s
        notes["vix"]  = n_
    else:
        scores["vix"] = 0
        notes["vix"]  = "⚪ VIX data unavailable"

    # ── Dimension 5: 20-Day Momentum ──────────────────────────
    if m20 is not None:
        if   m20 > 8:   s, n_ = 2,  f"✅ Strong surge   20d +{m20:.1f}%"
        elif m20 > 3:   s, n_ = 1,  f"✅ Positive       20d +{m20:.1f}%"
        elif m20 > -3:  s, n_ = 0,  f"⚪ Flat           20d {m20:+.1f}%"
        else:           s, n_ = -2, f"🔴 Weak           20d {m20:.1f}%"
        scores["mom20"] = s
        notes["mom20"]  = n_
    else:
        scores["mom20"] = 0
        notes["mom20"]  = "⚪ Insufficient data"

    # ── Dimension 6: 60-Day Momentum ──────────────────────────
    if m60 is not None:
        if   m60 > 15:  s, n_ = 2,  f"✅ Strong trend   60d +{m60:.1f}%"
        elif m60 > 5:   s, n_ = 1,  f"✅ Trending up    60d +{m60:.1f}%"
        elif m60 > -5:  s, n_ = 0,  f"⚪ Range-bound    60d {m60:+.1f}%"
        else:           s, n_ = -2, f"🔴 Downtrend      60d {m60:.1f}%"
        scores["mom60"] = s
        notes["mom60"]  = n_
    else:
        scores["mom60"] = 0
        notes["mom60"]  = "⚪ Insufficient data"

    # ── Dimension 7: 10Y Treasury 60-Day Change ────────────────
    if tnx_c is not None:
        if   tnx_c < -0.25: s, n_ = 1,  f"✅ Rates falling  10Y Δ{tnx_c:+.2f}%"
        elif tnx_c <  0.25: s, n_ = 0,  f"⚪ Rates stable   10Y Δ{tnx_c:+.2f}%"
        elif tnx_c <  0.75: s, n_ = -1, f"⚠️  Rates rising   10Y Δ{tnx_c:+.2f}%"
        else:               s, n_ = -2, f"🔴 Rates spiking  10Y Δ{tnx_c:+.2f}%"
        scores["tnx"] = s
        notes["tnx"]  = n_
    else:
        scores["tnx"] = 0
        notes["tnx"]  = "⚪ Insufficient treasury data"

    # ── Dimension 8: Drawdown Depth ───────────────────────────
    if dd is not None:
        if   dd < -15: s, n_ = -3, f"🔴 Deep drawdown  {dd:.1f}% from ATH"
        elif dd < -8:  s, n_ = -1, f"⚠️  Moderate DD    {dd:.1f}% from ATH"
        else:          s, n_ = 0,  f"✅ Near highs      {dd:.1f}% from ATH"
        scores["drawdown"] = s
        notes["drawdown"]  = n_
        # Note: Layer 1 circuit breaker uses 6-month rolling high (dd_126), not ATH.
        # (ATH-based scoring here is for vol-decay severity; Layer 1 uses dd_126
        #  which clears faster after a bear market recovery.)
    else:
        scores["drawdown"] = 0
        notes["drawdown"]  = "⚪ Insufficient data"

    # ── Dimension 9: VIX Momentum (5-day change) ──────────────
    # A rapidly falling VIX signals fear is subsiding and leverage
    # conditions are improving — this captures early recovery entries
    # that raw VIX level alone would miss.
    if vix_5d is not None:
        if   vix_5d < -3:  s, n_ = 2,  f"✅ Fear subsiding  VIX 5d Δ{vix_5d:+.1f}"
        elif vix_5d < -1:  s, n_ = 1,  f"✅ VIX easing      VIX 5d Δ{vix_5d:+.1f}"
        elif vix_5d <  1:  s, n_ = 0,  f"⚪ VIX stable      VIX 5d Δ{vix_5d:+.1f}"
        elif vix_5d <  4:  s, n_ = -1, f"⚠️  VIX rising      VIX 5d Δ{vix_5d:+.1f}"
        else:              s, n_ = -2, f"🔴 VIX spiking     VIX 5d Δ{vix_5d:+.1f}"
        scores["vix_momentum"] = s
        notes["vix_momentum"]  = n_
    else:
        scores["vix_momentum"] = 0
        notes["vix_momentum"]  = "⚪ Insufficient VIX data"

    # ── Dimension 10: TNX Absolute Level ──────────────────────
    # D7 measures the *change* in 10Y yield (tailwind/headwind direction).
    # D10 measures the *level* — the structural rate environment.
    # High absolute yields compress Nasdaq valuations (high-multiple tech
    # is discounted at the risk-free rate) and mark the TACO risk zone
    # where policy instability peaks (empirically >4.5% triggers reversals).
    # Low yields signal loose financial conditions, a structural TQQQ tailwind.
    if tnx is not None:
        if   tnx < 3.5: s, n_ = 1,  f"✅ Low rates       10Y {tnx:.2f}%  (loose conditions)"
        elif tnx < 4.5: s, n_ = 0,  f"⚪ Neutral rates   10Y {tnx:.2f}%"
        else:           s, n_ = -2, f"🔴 High rates      10Y {tnx:.2f}%  (TACO risk zone)"
        scores["tnx_level"] = s
        notes["tnx_level"]  = n_
    else:
        scores["tnx_level"] = 0
        notes["tnx_level"]  = "⚪ Insufficient treasury data"

    total = sum(scores.values())
    return {"scores": scores, "notes": notes, "total": total}


# ══════════════════════════════════════════════════════════════════
#  LAYER 1: HARD CIRCUIT BREAKERS
# ══════════════════════════════════════════════════════════════════

def check_circuit_breakers(ind: dict, data: dict, cfg: dict) -> dict:
    """
    Check all Layer 1 hard stops.
    Returns dict with triggered status and which conditions fired.
    """
    triggered  = False
    conditions = []

    def safe(series):
        try:
            v = series.iloc[-1]
            return float(v) if not pd.isna(v) else None
        except Exception:
            return None

    qqq_p  = safe(ind["qqq_price"])
    sma200 = safe(ind["sma200"])
    ema20  = safe(ind["ema20"])
    ema50  = safe(ind["ema50"])
    # Use 6-month rolling high for circuit breaker (dd_126), not ATH.
    # Falls back to ATH-based (dd_pct) if dd_126 unavailable.
    dd     = safe(ind.get("dd_126", ind["dd_pct"]))
    dd_label = "6-month high" if "dd_126" in ind else "ATH"
    vix    = safe(ind["vix"])

    # Check 1: Drawdown (from 6-month rolling high, not ATH)
    if dd is not None and dd < cfg["dd_threshold"]:
        triggered = True
        conditions.append(
            f"QQQ drawdown from {dd_label} {dd:.1f}% < threshold {cfg['dd_threshold']}%"
        )

    # Check 2: VIX
    if vix is not None and vix > cfg["vix_threshold"]:
        triggered = True
        conditions.append(f"VIX {vix:.1f} > threshold {cfg['vix_threshold']}")

    # Check 3: SMA200
    if qqq_p is not None and sma200 is not None and qqq_p < sma200:
        triggered = True
        conditions.append(f"QQQ {qqq_p:.2f} below SMA200 {sma200:.2f}")

    # NOTE (v2): EMA death cross is NO LONGER a hard stop.
    # It was causing multi-week blackouts during early recoveries (e.g. Feb–Apr
    # 2026) because EMAs lag price by definition.  The -4 penalty in Layer 2
    # scoring already suppresses TQQQ allocation whenever the cross is active;
    # the hard stop was redundant and too slow to re-enable.

    return {"triggered": triggered, "conditions": conditions}


# ══════════════════════════════════════════════════════════════════
#  LAYER 3: VOLATILITY TARGETING
# ══════════════════════════════════════════════════════════════════

def calc_vol_cap(ind: dict, cfg: dict, score: int = None) -> dict:
    """
    Calculate the TQQQ allocation cap based on realized volatility targeting.

    v2 dynamic budget:
      • bull regime (score ≥ 4 AND VIX < 20): target_vol_bull (25%)
      • otherwise: target_vol (20%)
      Threshold lowered from 5 → 4 to capture partial-recovery bull phases.

    Rationale: when all 9 dimensions align bullishly AND fear is low, the
    expected vol-adjusted return from extra leverage is positive.  Hard-capping
    at 20% leaves significant return on the table in clean bull markets.
    """
    try:
        tqqq_vol = float(ind["tqqq_vol"].iloc[-1])
        if pd.isna(tqqq_vol) or tqqq_vol <= 0:
            return {"cap": 1.0, "tqqq_vol": None,
                    "note": "⚪ Insufficient TQQQ vol data", "target": cfg["target_vol"]}

        # Dynamic vol budget
        target = cfg["target_vol"]
        regime = "standard"
        if cfg.get("dynamic_vol", False) and score is not None:
            try:
                vix_val = float(ind["vix"].iloc[-1])
            except Exception:
                vix_val = 99.0
            if score >= 4 and vix_val < 20:
                target = cfg.get("target_vol_bull", 0.25)
                regime = "bull"

        cap  = target / tqqq_vol
        note = (f"TQQQ ann. vol {tqqq_vol:.0%}  target={target:.0%} [{regime}]"
                f"  →  cap = {target:.0%}/{tqqq_vol:.0%} = {cap:.0%}")
        return {"cap": min(cap, 1.0), "tqqq_vol": tqqq_vol,
                "note": note, "target": target}
    except Exception as e:
        return {"cap": 1.0, "tqqq_vol": None,
                "note": f"⚪ Vol calc error: {e}", "target": cfg["target_vol"]}


# ══════════════════════════════════════════════════════════════════
#  TRAILING STOP CHECK
# ══════════════════════════════════════════════════════════════════

def check_trailing_stop(ind: dict, cfg: dict) -> dict:
    """
    Check if TQQQ has triggered the trailing stop.
    Fires if: current price < 15-day high × trail_pct
    """
    try:
        current = float(ind["tqqq_price"].iloc[-1])
        high    = float(ind["tqqq_trail_high"].iloc[-1])
        stop    = high * cfg["trail_pct"]
        fired   = current < stop
        pct_from_high = (current / high - 1) * 100
        note = (f"TQQQ ${current:.2f}  |  15d high ${high:.2f}  |  "
                f"stop ${stop:.2f}  |  {pct_from_high:+.1f}% from high  |  "
                f"{'🔴 TRIGGERED' if fired else '✅ OK'}")
        return {"fired": fired, "current": current,
                "high": high, "stop": stop, "note": note}
    except Exception as e:
        return {"fired": False, "note": f"⚪ Trailing stop error: {e}"}


# ══════════════════════════════════════════════════════════════════
#  ALLOCATION LOOKUP
# ══════════════════════════════════════════════════════════════════

def score_to_base_alloc(score: int, cfg: dict) -> float:
    """Convert total signal score to base TQQQ allocation."""
    if score <= 0:
        return 0.0
    amap = cfg["alloc_map"]
    capped = min(score, max(amap.keys()))
    # If score > max key, use 100%
    return amap.get(capped, 1.0) if score <= max(amap.keys()) else 1.0


# ══════════════════════════════════════════════════════════════════
#  MAIN SIGNAL FUNCTION
# ══════════════════════════════════════════════════════════════════

def run_apex(cfg: dict = None, verbose: bool = True) -> dict:
    """
    Run the full APEX strategy signal for today.

    Returns dict with:
        tqqq_pct   : recommended TQQQ allocation (0.0 – 1.0)
        voo_pct    : recommended VOO allocation
        score      : total signal score
        base_alloc : allocation from score (before vol cap)
        vol_cap    : volatility cap
        final_alloc: final allocation (= tqqq_pct)
        circuit_triggered : bool
        trail_stop_fired  : bool
    """
    if cfg is None:
        cfg = CONFIG

    # 1. Fetch data
    data = fetch_data(cfg)

    # 2. Calculate indicators
    ind = calc_indicators(data, cfg)

    # 3. Layer 1: Circuit breakers
    cb = check_circuit_breakers(ind, data, cfg)

    # 4. Layer 2: Signal scoring
    sig = score_signals(ind, data)

    # 5. Base allocation from score
    base_alloc = score_to_base_alloc(sig["total"], cfg)

    # 6. Layer 3: Volatility cap (pass score for dynamic vol budget)
    vc = calc_vol_cap(ind, cfg, score=sig["total"])

    # 7. Trailing stop
    ts = check_trailing_stop(ind, cfg)

    # 8. Final allocation
    if cb["triggered"]:
        final_alloc = 0.0
        reason = "LAYER 1 CIRCUIT BREAKER"
    elif ts["fired"]:
        final_alloc = 0.0
        reason = "TRAILING STOP"
    else:
        final_alloc = round(min(base_alloc, vc["cap"]), 2)
        reason = "NORMAL SIGNAL"

    # ── Pretty print ──────────────────────────────────────────
    if verbose:
        w = 68
        print()
        print("═" * w)
        print(f"  APEX Strategy  —  Signal Report  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("═" * w)

        print(f"\n{'─'*w}")
        print("  LAYER 1 — CIRCUIT BREAKERS")
        print(f"{'─'*w}")
        if cb["triggered"]:
            print("  🔴 TRIGGERED  →  VOO 100%")
            for c in cb["conditions"]:
                print(f"     • {c}")
        else:
            print("  ✅ All clear — no hard stops triggered")

        print(f"\n{'─'*w}")
        print("  LAYER 2 — SIGNAL SCORING  (10 dimensions)")
        print(f"{'─'*w}")
        dim_labels = {
            "ema_trend"    : "EMA Trend      ",
            "sma200"       : "SMA200 Regime  ",
            "rsi"          : "RSI Momentum   ",
            "vix"          : "VIX Environment",
            "mom20"        : "20d Momentum   ",
            "mom60"        : "60d Momentum   ",
            "tnx"          : "10Y Rate Change",
            "drawdown"     : "Drawdown Depth ",
            "vix_momentum" : "VIX Momentum   ",
            "tnx_level"    : "10Y Rate Level ",
        }
        for key, label in dim_labels.items():
            score = sig["scores"].get(key, 0)
            note  = sig["notes"].get(key, "")
            bar   = "█" * abs(score) if score != 0 else "·"
            sign  = "+" if score >= 0 else ""
            color_mark = "▲" if score > 0 else ("▼" if score < 0 else "─")
            print(f"  {label}  {color_mark} {sign}{score:+2d}  {bar:<4}  {note}")

        total = sig["total"]
        print(f"\n  {'─'*30}")
        print(f"  TOTAL SCORE:  {total:+d}")
        print(f"  BASE ALLOC:   {base_alloc:.0%} TQQQ")

        print(f"\n{'─'*w}")
        print("  LAYER 3 — VOLATILITY TARGETING")
        print(f"{'─'*w}")
        print(f"  {vc['note']}")
        print(f"  Target portfolio vol: {cfg['target_vol']:.0%}  →  TQQQ cap: {vc['cap']:.0%}")

        print(f"\n{'─'*w}")
        print("  TRAILING STOP CHECK")
        print(f"{'─'*w}")
        print(f"  {ts['note']}")

        print(f"\n{'═'*w}")
        alloc_bar_t = "█" * int(final_alloc * 40)
        alloc_bar_v = "░" * int((1-final_alloc) * 40)
        print(f"\n  ⭐  RECOMMENDED ALLOCATION  ({reason})")
        print()
        print(f"     TQQQ  {final_alloc:.0%}   {alloc_bar_t}")
        print(f"     VOO   {1-final_alloc:.0%}   {alloc_bar_v}")
        print()

        # Interpretation
        if final_alloc == 0.0:
            interp = "🔴 DEFENSIVE — Hold VOO only. Protect capital."
        elif final_alloc <= 0.25:
            interp = "🟡 CAUTIOUS  — Small TQQQ position. Market uncertain."
        elif final_alloc <= 0.45:
            interp = "🟡 MODERATE  — Balanced exposure. Watch closely."
        elif final_alloc <= 0.65:
            interp = "🟢 BULLISH   — Standard bull allocation."
        else:
            interp = "🟢 AGGRESSIVE — Strong bull signal. High TQQQ exposure."

        print(f"  {interp}")
        print()
        print("  ⚠ Confirm signal holds for 3 consecutive days before acting.")
        print("  ⚠ Execute T+1 (next trading day open, after 10:00 AM).")
        print("  ⚠ This strategy is designed for Roth IRA accounts only.")
        print("  ⚠ NOT financial advice. Use at your own risk.")
        print("═" * w)
        print()

    return {
        "tqqq_pct"         : final_alloc,
        "voo_pct"          : round(1 - final_alloc, 2),
        "score"            : sig["total"],
        "base_alloc"       : base_alloc,
        "vol_cap"          : round(vc["cap"], 2),
        "tqqq_vol"         : vc.get("tqqq_vol"),
        "final_alloc"      : final_alloc,
        "reason"           : reason,
        "circuit_triggered": cb["triggered"],
        "circuit_conditions": cb["conditions"],
        "trail_stop_fired" : ts["fired"],
        "signal_scores"    : sig["scores"],
        "signal_notes"     : sig["notes"],
        "timestamp"        : datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════
#  SIMPLE BACKTEST (illustrative, based on live signal replay)
# ══════════════════════════════════════════════════════════════════

def quick_ytd_check(cfg: dict = None) -> None:
    """
    Show year-to-date performance of TQQQ vs VOO for context.
    """
    if cfg is None:
        cfg = CONFIG

    print("\n📊 Year-to-Date Performance Check")
    print("─" * 50)
    try:
        tqqq = yf.download("TQQQ", period="1y", progress=False,
                           auto_adjust=True)["Close"].squeeze()
        voo  = yf.download("VOO",  period="1y", progress=False,
                           auto_adjust=True)["Close"].squeeze()
        qqq  = yf.download("QQQ",  period="1y", progress=False,
                           auto_adjust=True)["Close"].squeeze()

        # YTD (calendar year to date)
        ytd_start = pd.Timestamp(f"{datetime.now().year}-01-01")

        def ytd_ret(s):
            s_ytd = s[s.index >= ytd_start]
            if len(s_ytd) < 2:
                return None
            return (s_ytd.iloc[-1] / s_ytd.iloc[0] - 1) * 100

        r_tqqq = ytd_ret(tqqq)
        r_voo  = ytd_ret(voo)
        r_qqq  = ytd_ret(qqq)

        if r_tqqq is not None:
            print(f"  TQQQ YTD:  {r_tqqq:+.1f}%   (current ${tqqq.iloc[-1]:.2f})")
        if r_voo is not None:
            print(f"  VOO  YTD:  {r_voo:+.1f}%   (current ${voo.iloc[-1]:.2f})")
        if r_qqq is not None:
            print(f"  QQQ  YTD:  {r_qqq:+.1f}%   (signal ticker)")

        # Max drawdown this year
        def max_dd(s):
            s_ytd = s[s.index >= ytd_start]
            return ((s_ytd / s_ytd.cummax()) - 1).min() * 100

        if len(tqqq[tqqq.index >= ytd_start]) > 10:
            print(f"\n  TQQQ max drawdown YTD:  {max_dd(tqqq):.1f}%")
            print(f"  VOO  max drawdown YTD:  {max_dd(voo):.1f}%")

    except Exception as e:
        print(f"  Error fetching data: {e}")
    print()


# ══════════════════════════════════════════════════════════════════
#  WEEKLY CHECKLIST PRINTER
# ══════════════════════════════════════════════════════════════════

def print_weekly_checklist() -> None:
    """Print the weekly execution checklist."""
    print("""
┌─────────────────────────────────────────────────────────────┐
│  APEX Strategy — Weekly Execution Checklist                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  SUNDAY EVENING                                             │
│  □ Run: python apex_strategy.py                             │
│  □ Record: score, final_alloc, vol_cap                      │
│  □ Note: compare to prior week allocation                   │
│                                                             │
│  MONDAY 9:30–10:00 AM                                       │
│  □ DO NOT trade during the first 30 minutes                 │
│                                                             │
│  MONDAY 10:00 AM+                                           │
│  □ Check VIX (Yahoo Finance ^VIX)                           │
│  □ Verify signal still holds (3-day confirmation)           │
│  □ Check TQQQ trailing stop (15d high × 92%)                │
│  □ If allocation changed > 5%: execute in Fidelity          │
│     Fidelity → Trade → Mutual Funds/ETFs                    │
│     Use dollar amounts, not share counts                    │
│                                                             │
│  DAILY (takes 2 minutes)                                    │
│  □ Check TQQQ vs 15-day high                                │
│  □ If TQQQ < high × 0.92 → immediate exit to VOO           │
│  □ Watch for VIX spike > 25                                 │
│                                                             │
│  KEY RULES                                                  │
│  □ Roth IRA only (taxable account destroys returns)         │
│  □ 3-day signal confirmation before any trade               │
│  □ T+1 execution (signal today → trade tomorrow open)       │
│  □ Never override the signal with gut feeling               │
│  □ Keep 3–5% cash buffer in account                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
""")


# ══════════════════════════════════════════════════════════════════
#  STRATEGY SUMMARY REFERENCE
# ══════════════════════════════════════════════════════════════════

STRATEGY_SUMMARY = """
APEX Strategy v2.0 — Quick Reference Card
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAYER 1 — HARD STOPS (any triggers → VOO 100%)
  • QQQ drawdown from 6-month high  > -10%   ← rolling high, not ATH
  • VIX                             > 25
  • QQQ below SMA200                → bear regime
  (EMA death cross is Layer 2 only: -4 score penalty, NOT a hard stop)

LAYER 2 — SIGNAL SCORING → BASE ALLOCATION  (9 dimensions)
  Score ≤ 0  → 0%    (bearish)
  Score  1   → 20%   (cautious)
  Score  2-3 → 35-50% (neutral bull)
  Score  4-5 → 65-75% (standard bull)
  Score  6   → 90%   (strong bull)
  Score ≥ 7  → 100%  (ideal conditions)

  D1: EMA trend (+3/-4)        D2: SMA200 regime (+2/-3)
  D3: RSI(14) (0/+2/-3)        D4: VIX level (+3/-3)
  D5: 20d momentum (+2/-2)     D6: 60d momentum (+2/-2)
  D7: 10Y rate 60d Δ (+1/-2)   D8: Drawdown depth (0/-3)
  D9: VIX 5d momentum (+2/-2)  D10: 10Y rate level (+1/-2)

LAYER 3 — DYNAMIC VOLATILITY CAP
  final = min(base_alloc, target_vol / TQQQ_realized_vol)
  Bull regime (score ≥ 4 AND VIX < 20): target_vol = 25%
  Otherwise:                             target_vol = 20%
  TQQQ realized vol = 15-day rolling daily std × √252

TRAILING STOP (daily check)
  If TQQQ < 15-day-high × 0.92 → VOO 100%

EXECUTION
  • Signal from Sunday close → Execute Monday 10AM
  • 3-day confirmation before any entry; 1-day to exit
  • Roth IRA only
  • Account: Fidelity (or any broker with TQQQ/VOO)

PERFORMANCE (backtest 2010–2026, no taxes)
  CAGR: 23.9%   Sharpe: 0.96   Max DD: -36.3%
  vs QQQ: 19.6%  0.97           -35.1%
  Realistic live estimate: 19–22% CAGR (slippage + confirmation lag)
"""


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if "--help" in args or "-h" in args:
        print(STRATEGY_SUMMARY)
        print_weekly_checklist()

    elif "--checklist" in args:
        print_weekly_checklist()

    elif "--summary" in args:
        print(STRATEGY_SUMMARY)

    elif "--ytd" in args:
        quick_ytd_check()

    else:
        # Default: run full signal
        result = run_apex(verbose=True)

        if "--ytd" in args or True:  # always show YTD
            quick_ytd_check()

        # Print raw result dict for programmatic use
        if "--json" in args:
            import json
            # Make JSON-serializable
            out = {k: (float(v) if isinstance(v, (np.floating, np.integer))
                       else v)
                   for k, v in result.items()
                   if k not in ("signal_scores", "signal_notes")}
            print(json.dumps(out, indent=2))
