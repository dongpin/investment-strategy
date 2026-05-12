"""
APEX Strategy — YTD Backtest Engine
=====================================
Replays APEX signals day-by-day from Jan 1 of the current year using
actual historical prices. Compares against buy-and-hold VOO, QQQ, TQQQ.

Usage:
    python backtest_ytd.py
    python backtest_ytd.py --year 2025
    python backtest_ytd.py --plot        # requires matplotlib
"""

import warnings
warnings.filterwarnings("ignore")

import sys
import numpy as np
import pandas as pd
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance pandas numpy matplotlib")
    raise

from apex_strategy import CONFIG


# ══════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════

def load_data(start: str, end: str) -> dict:
    """Download OHLCV + VIX + TNX data for the full backtest window."""
    print(f"📡  Downloading data  {start} → {end} …")

    def dl(ticker, start, end):
        df = yf.download(ticker, start=start, end=end,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"No data for {ticker}")
        s = df["Close"].squeeze()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.dropna().rename(ticker)

    data = {
        "QQQ"  : dl("QQQ",  start, end),
        "TQQQ" : dl("TQQQ", start, end),
        "VOO"  : dl("VOO",  start, end),
        "QLD"  : dl("QLD",  start, end),
        "VIX"  : dl("^VIX", start, end),
        "TNX"  : dl("^TNX", start, end),
    }
    print(f"    QQQ rows: {len(data['QQQ'])}  TQQQ rows: {len(data['TQQQ'])}")
    return data


# ══════════════════════════════════════════════════════════════════
#  INDICATOR CALCULATION  (vectorised over full history)
# ══════════════════════════════════════════════════════════════════

def build_indicators(data: dict, cfg: dict) -> pd.DataFrame:
    """Return a DataFrame of all indicators aligned on QQQ's trading dates."""
    q = data["QQQ"]
    t = data["TQQQ"]
    v = data["VIX"].reindex(q.index, method="ffill")
    n = data["TNX"].reindex(q.index, method="ffill")

    df = pd.DataFrame(index=q.index)

    # Price series
    df["qqq"]  = q
    df["tqqq"] = t.reindex(q.index, method="ffill")
    df["voo"]  = data["VOO"].reindex(q.index, method="ffill")
    df["qld"]  = data["QLD"].reindex(q.index, method="ffill")
    df["vix"]  = v
    df["tnx"]  = n

    # Moving averages
    df["ema20"]  = q.ewm(span=20, adjust=False).mean()
    df["ema50"]  = q.ewm(span=50, adjust=False).mean()
    df["sma200"] = q.rolling(200).mean()

    # RSI(14)
    delta = q.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi14"] = 100 - (100 / (1 + gain / loss))

    # Drawdown from ATH (used for D8 scoring — measures accumulated vol-decay hole)
    df["dd_pct"] = (q / q.cummax() - 1) * 100

    # Drawdown from 6-month rolling high (used for Layer 1 circuit breaker)
    # Clears faster than ATH after extended bear markets, avoiding the
    # "ATH-trap" (e.g. QQQ 25% below 2021 ATH kept strategy at 0% through 2023).
    dd_lookback = cfg.get("dd_lookback", 126)
    df["dd_126"] = (q / q.rolling(dd_lookback).max() - 1) * 100

    # Momentum
    df["mom20"] = (q / q.shift(20) - 1) * 100
    df["mom60"] = (q / q.shift(60) - 1) * 100

    # Treasury 60-day change
    df["tnx60"] = n.diff(60)

    # TQQQ realized vol (annualised)
    df["tqqq_vol"] = (
        t.reindex(q.index, method="ffill")
         .pct_change()
         .rolling(cfg["vol_window"])
         .std()
        * np.sqrt(252)
    )

    # TQQQ trailing high
    df["tqqq_trail_high"] = (
        t.reindex(q.index, method="ffill")
         .rolling(cfg["trail_window"])
         .max()
    )

    # VIX 5-day momentum (new v2 signal)
    df["vix_5d"] = v.diff(5)

    return df


# ══════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE  (single-row version of apex_strategy logic)
# ══════════════════════════════════════════════════════════════════

def compute_signal(row: pd.Series, cfg: dict) -> float:
    """
    Given one row of the indicators DataFrame, return final TQQQ allocation.
    Returns 0.0–1.0.

    v2 changes vs v1:
      1. EMA death cross removed from Layer 1 (now Layer 2 −4 penalty only)
      2. VIX momentum (9th dimension) added to scoring
      3. Dynamic vol target: 25% when score>=4 and VIX<20, else 20%
      4. Layer 1 drawdown uses 6-month rolling high (not ATH) → faster re-entry
      5. vol_window 20→15 days → vol cap recovers faster post-spike
      6. TNX absolute level (10th dimension) added to scoring
    """
    def g(col):
        v = row.get(col)
        return None if (v is None or pd.isna(v)) else float(v)

    ema20  = g("ema20");  ema50  = g("ema50")
    sma200 = g("sma200"); qqq_p  = g("qqq")
    rsi    = g("rsi14");  dd     = g("dd_pct")   # ATH-based (for D8 scoring)
    dd_126 = g("dd_126")                          # 6-month rolling high (for Layer 1)
    vix    = g("vix");    m20    = g("mom20");  m60 = g("mom60")
    tnx_c  = g("tnx60");  tnx    = g("tnx")
    tqqq_p = g("tqqq");   tqqq_vol = g("tqqq_vol")
    tqqq_h = g("tqqq_trail_high"); vix_5d = g("vix_5d")

    # ── Layer 1: Hard circuit breakers (v2: 3 stops, not 4) ──────
    # EMA death cross removed — its -4 score penalty in Layer 2 is sufficient.
    # Drawdown check uses 6-month rolling high (dd_126) instead of ATH:
    #   ATH-based kept the strategy at 0% TQQQ through most of 2023 (QQQ was
    #   25% below its 2021 ATH even as the market trended strongly upward).
    #   Rolling high clears once the market recovers from its *recent* trough.
    dd_cb = dd_126 if dd_126 is not None else dd   # prefer 6-month high
    if dd_cb is not None and dd_cb < cfg["dd_threshold"]:
        return 0.0
    if vix is not None and vix > cfg["vix_threshold"]:
        return 0.0
    if qqq_p is not None and sma200 is not None and qqq_p < sma200:
        return 0.0

    # ── Layer 2: Signal scoring (9 dimensions) ───────────────────
    score = 0

    # D1: EMA trend
    if ema20 is not None and ema50 is not None:
        score += 3 if ema20 > ema50 else -4

    # D2: SMA200 regime
    if qqq_p is not None and sma200 is not None:
        score += 2 if qqq_p > sma200 else -3

    # D3: RSI
    if rsi is not None:
        if   rsi > 70: score += 0
        elif rsi > 60: score += 2
        elif rsi > 50: score += 1
        elif rsi > 40: score += -1
        else:          score += -3

    # D4: VIX level
    if vix is not None:
        if   vix < 13: score += 3
        elif vix < 18: score += 2
        elif vix < 22: score += 1
        elif vix < 27: score += -1
        else:          score += -3

    # D5: 20d price momentum
    if m20 is not None:
        if   m20 > 8:  score += 2
        elif m20 > 3:  score += 1
        elif m20 > -3: score += 0
        else:          score += -2

    # D6: 60d price momentum
    if m60 is not None:
        if   m60 > 15: score += 2
        elif m60 > 5:  score += 1
        elif m60 > -5: score += 0
        else:          score += -2

    # D7: 10Y Treasury 60d change
    if tnx_c is not None:
        if   tnx_c < -0.25: score += 1
        elif tnx_c <  0.25: score += 0
        elif tnx_c <  0.75: score += -1
        else:                score += -2

    # D8: Drawdown depth
    if dd is not None:
        if   dd < -15: score += -3
        elif dd < -8:  score += -1
        else:          score += 0

    # D9: VIX momentum (5-day change) — new in v2
    # A falling VIX = fear receding = leverage conditions improving.
    # Captures early recovery entries that raw VIX level misses.
    if vix_5d is not None:
        if   vix_5d < -3: score += 2
        elif vix_5d < -1: score += 1
        elif vix_5d <  1: score += 0
        elif vix_5d <  4: score += -1
        else:             score += -2

    # D10: TNX absolute level (10-year Treasury yield)
    # D7 captures the *change* in rates; D10 captures the *level*.
    # High absolute yields compress growth/tech valuations and mark the
    # "TACO risk zone" where policy instability peaks (empirically >4.5%).
    # Low yields signal loose financial conditions — a structural tailwind
    # for leveraged Nasdaq exposure.
    if tnx is not None:
        if   tnx < 3.5: score += 1   # loose financial conditions
        elif tnx < 4.5: score += 0   # neutral / historically normal
        else:           score += -2  # tight + TACO risk zone (>4.5%)

    # Score → base allocation
    amap = cfg["alloc_map"]
    if score <= 0:
        base_alloc = 0.0
    elif score >= max(amap.keys()):
        base_alloc = 1.0
    else:
        base_alloc = amap.get(min(score, max(amap.keys())), 1.0)

    # ── Layer 3: Dynamic volatility cap ──────────────────────────
    # v2: raise target_vol to 25% when signals are bullish (score>=4)
    # AND fear is low (VIX<20). Threshold lowered from 5 → 4 to capture
    # partial-recovery bull phases where most but not all signals align.
    if cfg.get("dynamic_vol", False) and score >= 4 and vix is not None and vix < 20:
        target = cfg.get("target_vol_bull", 0.25)
    else:
        target = cfg["target_vol"]

    if tqqq_vol is not None and tqqq_vol > 0:
        vol_cap = target / tqqq_vol
    else:
        vol_cap = 1.0

    alloc = min(base_alloc, vol_cap)

    # ── Trailing stop ────────────────────────────────────────────
    if tqqq_p is not None and tqqq_h is not None:
        if tqqq_p < tqqq_h * cfg["trail_pct"]:
            return 0.0

    return round(min(alloc, 1.0), 4)


# ══════════════════════════════════════════════════════════════════
#  BACKTEST LOOP
# ══════════════════════════════════════════════════════════════════

def run_backtest_v1(ind: pd.DataFrame, ytd_start: pd.Timestamp) -> pd.DataFrame:
    """Run original v1 backtest (4 hard stops, fixed 20% vol target)."""
    cfg_v1 = CONFIG.copy()
    cfg_v1["dynamic_vol"]   = False
    cfg_v1["target_vol"]    = 0.20
    cfg_v1["alloc_map"]     = {0:0.00, 1:0.20, 2:0.35, 3:0.50,
                                4:0.65, 5:0.75, 6:0.85}

    def signal_v1(row):
        """Original v1 signal with EMA death cross as hard stop."""
        def g(col):
            v = row.get(col)
            return None if (v is None or pd.isna(v)) else float(v)

        ema20  = g("ema20");  ema50  = g("ema50")
        sma200 = g("sma200"); qqq_p  = g("qqq")
        rsi    = g("rsi14");  dd     = g("dd_pct")
        vix    = g("vix");    m20    = g("mom20");  m60 = g("mom60")
        tnx_c  = g("tnx60");  tqqq_p = g("tqqq");  tqqq_vol = g("tqqq_vol")
        tqqq_h = g("tqqq_trail_high")

        # v1 Layer 1: 4 hard stops (includes EMA death cross)
        if dd  is not None and dd  < cfg_v1["dd_threshold"]:   return 0.0
        if vix is not None and vix > cfg_v1["vix_threshold"]:  return 0.0
        if qqq_p is not None and sma200 is not None and qqq_p < sma200: return 0.0
        if ema20 is not None and ema50  is not None and ema20  < ema50:  return 0.0

        score = 0
        if ema20 and ema50:   score += 3 if ema20>ema50 else -4
        if qqq_p and sma200:  score += 2 if qqq_p>sma200 else -3
        if rsi:
            if rsi>70: score+=0
            elif rsi>60: score+=2
            elif rsi>50: score+=1
            elif rsi>40: score+=-1
            else: score+=-3
        if vix:
            if vix<13: score+=3
            elif vix<18: score+=2
            elif vix<22: score+=1
            elif vix<27: score+=-1
            else: score+=-3
        if m20:
            if m20>8: score+=2
            elif m20>3: score+=1
            elif m20>-3: score+=0
            else: score+=-2
        if m60:
            if m60>15: score+=2
            elif m60>5: score+=1
            elif m60>-5: score+=0
            else: score+=-2
        if tnx_c:
            if tnx_c<-0.25: score+=1
            elif tnx_c<0.25: score+=0
            elif tnx_c<0.75: score+=-1
            else: score+=-2
        if dd:
            if dd<-15: score+=-3
            elif dd<-8: score+=-1

        amap = cfg_v1["alloc_map"]
        if score<=0: base=0.0
        elif score>=max(amap.keys()): base=1.0
        else: base=amap.get(min(score, max(amap.keys())), 1.0)

        vol_cap = (0.20/tqqq_vol) if (tqqq_vol and tqqq_vol>0) else 1.0
        alloc   = min(base, vol_cap)

        if tqqq_p and tqqq_h and tqqq_p < tqqq_h * cfg_v1["trail_pct"]:
            return 0.0
        return round(min(alloc, 1.0), 4)

    return run_backtest(ind, cfg_v1, ytd_start, signal_fn=signal_v1)


def run_backtest(ind: pd.DataFrame, cfg: dict, ytd_start: pd.Timestamp,
                 signal_fn=None) -> pd.DataFrame:
    """
    Simulate APEX strategy over the YTD window.
    Weekly rebalance on Mondays; daily trailing stop check.

    Returns a DataFrame with columns:
        date, apex_nav, tqqq_alloc, signal,
        voo_nav, tqqq_nav, qqq_nav, qld_nav
    """
    if signal_fn is None:
        signal_fn = lambda row: compute_signal(row, cfg)

    ytd = ind[ind.index >= ytd_start].copy()
    if len(ytd) < 2:
        raise ValueError("Not enough data in YTD window.")

    nav   = 1.0
    alloc = 0.0
    results = []

    # Use first valid prices as initial prev values
    tqqq_series = ytd["tqqq"].dropna(); voo_series = ytd["voo"].dropna()
    prev_tqqq = tqqq_series.iloc[0] if len(tqqq_series) else 1.0
    prev_voo  = voo_series.iloc[0]  if len(voo_series)  else 1.0

    for i, (date, row) in enumerate(ytd.iterrows()):
        if i == 0:
            alloc = signal_fn(row)
            results.append({"date": date, "apex_nav": nav,
                            "tqqq_alloc": alloc, "voo_alloc": 1 - alloc})
            if not pd.isna(row.get("tqqq", float("nan"))): prev_tqqq = row["tqqq"]
            if not pd.isna(row.get("voo",  float("nan"))): prev_voo  = row["voo"]
            continue

        # ── Daily portfolio return ───────────────────────────────
        tqqq_p_now = row["tqqq"] if not pd.isna(row.get("tqqq", float("nan"))) else prev_tqqq
        voo_p_now  = row["voo"]  if not pd.isna(row.get("voo",  float("nan"))) else prev_voo
        tqqq_ret = (tqqq_p_now / prev_tqqq - 1) if (prev_tqqq and prev_tqqq > 0) else 0.0
        voo_ret  = (voo_p_now  / prev_voo  - 1) if (prev_voo  and prev_voo  > 0) else 0.0
        nav      = nav * (1 + alloc * tqqq_ret + (1 - alloc) * voo_ret)

        # ── Weekly rebalance on Mondays ──────────────────────────
        new_alloc = signal_fn(row) if date.weekday() == 0 else alloc

        # ── Daily trailing stop overrides everything ─────────────
        tqqq_p = row.get("tqqq"); tqqq_h = row.get("tqqq_trail_high")
        if (tqqq_p is not None and tqqq_h is not None and
                not pd.isna(tqqq_p) and not pd.isna(tqqq_h)):
            if float(tqqq_p) < float(tqqq_h) * cfg["trail_pct"]:
                new_alloc = 0.0

        alloc = new_alloc
        results.append({"date": date, "apex_nav": nav,
                        "tqqq_alloc": alloc, "voo_alloc": 1 - alloc})
        prev_tqqq = tqqq_p_now
        prev_voo  = voo_p_now

    bt = pd.DataFrame(results).set_index("date")

    # ── Buy-and-hold benchmarks ──────────────────────────────────
    # Use first_valid_index so assets with late IPOs (e.g. VOO Sept 2010)
    # don't produce all-NaN series when the backtest starts earlier.
    for col, asset in [("voo_nav", "voo"), ("tqqq_nav", "tqqq"),
                       ("qqq_nav", "qqq"), ("qld_nav", "qld")]:
        prices = ytd[asset]
        base_idx = prices.first_valid_index()
        if base_idx is not None:
            bt[col] = prices / prices.loc[base_idx]
        else:
            bt[col] = float("nan")

    return bt


# ══════════════════════════════════════════════════════════════════
#  PERFORMANCE METRICS
# ══════════════════════════════════════════════════════════════════

def metrics(nav_series: pd.Series, label: str) -> dict:
    """Compute CAGR, annualised vol, Sharpe, max drawdown.
    Uses first valid value to handle assets with late IPO (e.g. VOO in 2010).
    """
    nav   = nav_series.dropna()
    if len(nav) < 5:
        return {"label": label, "ytd_return": float("nan"), "cagr": float("nan"),
                "ann_vol": float("nan"), "sharpe": float("nan"), "max_dd": float("nan")}

    rets  = nav.pct_change().dropna()
    n     = len(rets)
    years = n / 252

    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    cagr      = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0.0
    ann_vol   = rets.std() * np.sqrt(252)
    sharpe    = (rets.mean() * 252) / ann_vol if ann_vol > 0 else 0.0
    max_dd    = ((nav / nav.cummax()) - 1).min()

    return {
        "label"     : label,
        "ytd_return": total_ret,
        "cagr"      : cagr,
        "ann_vol"   : ann_vol,
        "sharpe"    : sharpe,
        "max_dd"    : max_dd,
    }


def print_metrics_table(results: list[dict]) -> None:
    hdr = f"{'Strategy':<25} {'YTD':>8} {'Ann. CAGR':>10} {'Vol':>8} {'Sharpe':>8} {'Max DD':>9}"
    print(hdr)
    print("─" * len(hdr))
    for r in results:
        star = " ◀" if "APEX" in r["label"] else ""
        print(
            f"  {r['label']:<23} "
            f"{r['ytd_return']:>+7.1%} "
            f"{r['cagr']:>+9.1%} "
            f"{r['ann_vol']:>7.1%} "
            f"{r['sharpe']:>8.2f} "
            f"{r['max_dd']:>8.1%}"
            f"{star}"
        )


# ══════════════════════════════════════════════════════════════════
#  ALLOCATION TIMELINE SUMMARY
# ══════════════════════════════════════════════════════════════════

def print_monthly_allocations(bt: pd.DataFrame) -> None:
    """Print average TQQQ allocation by month."""
    print("\n📅  Average Monthly TQQQ Allocation")
    print("─" * 35)
    monthly = bt["tqqq_alloc"].resample("ME").mean()
    for period, avg in monthly.items():
        bar  = "█" * int(avg * 20)
        print(f"  {period.strftime('%Y-%m')}  {avg:5.0%}  {bar}")


def print_regime_changes(bt: pd.DataFrame, top_n: int = 15) -> None:
    """Print the largest allocation changes (regime switches)."""
    changes = bt["tqqq_alloc"].diff().abs().nlargest(top_n)
    print(f"\n🔄  Top {top_n} Allocation Shifts")
    print("─" * 50)
    for date, chg in changes.items():
        if chg < 0.05:
            continue
        prev = bt["tqqq_alloc"].loc[:date].iloc[-2] if len(bt[:date]) > 1 else 0
        curr = bt["tqqq_alloc"].loc[date]
        arrow = "▲" if curr > prev else "▼"
        print(f"  {date.strftime('%Y-%m-%d')}  {arrow}  "
              f"{prev:.0%} → {curr:.0%}  (Δ {curr-prev:+.0%})")


# ══════════════════════════════════════════════════════════════════
#  OPTIONAL PLOT
# ══════════════════════════════════════════════════════════════════

def plot_results(bt: pd.DataFrame, year: int) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("matplotlib not available — skip plot")
        return

    fig, axes = plt.subplots(3, 1, figsize=(13, 10),
                             gridspec_kw={"height_ratios": [3, 1.2, 1]})
    fig.suptitle(f"APEX Strategy — YTD {year} Backtest", fontsize=14, fontweight="bold")

    # Panel 1: NAV curves
    ax = axes[0]
    ax.plot(bt.index, bt["apex_nav"],  label="APEX v2.0", color="#2196F3", lw=2.5, zorder=5)
    if "apex_v1_nav" in bt.columns:
        ax.plot(bt.index, bt["apex_v1_nav"], label="APEX v1.0",
                color="#90CAF9", lw=1.5, ls="--", zorder=4)
    ax.plot(bt.index, bt["voo_nav"],   label="VOO",       color="#4CAF50", lw=1.5, ls="--")
    ax.plot(bt.index, bt["tqqq_nav"],  label="TQQQ",      color="#F44336", lw=1.5, ls=":")
    ax.plot(bt.index, bt["qqq_nav"],   label="QQQ",       color="#9C27B0", lw=1.2, ls="-.", alpha=0.7)
    ax.plot(bt.index, bt["qld_nav"],   label="QLD",       color="#FF9800", lw=1.2, ls="-.", alpha=0.7)
    ax.set_ylabel("Portfolio NAV (start = 1.0)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.set_title("Cumulative Returns", fontsize=11)

    # Panel 2: TQQQ allocation over time
    ax2 = axes[1]
    ax2.fill_between(bt.index, bt["tqqq_alloc"], alpha=0.6, color="#2196F3", label="TQQQ %")
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("TQQQ Weight")
    ax2.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax2.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax2.set_title("APEX TQQQ Allocation", fontsize=11)

    # Panel 3: Drawdown
    ax3 = axes[2]
    dd_apex = (bt["apex_nav"] / bt["apex_nav"].cummax() - 1) * 100
    dd_voo  = (bt["voo_nav"]  / bt["voo_nav"].cummax()  - 1) * 100
    ax3.fill_between(bt.index, dd_apex, 0, alpha=0.5, color="#2196F3", label="APEX DD")
    ax3.fill_between(bt.index, dd_voo,  0, alpha=0.3, color="#4CAF50", label="VOO DD")
    ax3.set_ylabel("Drawdown (%)")
    ax3.legend(loc="lower left", fontsize=9)
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax3.set_title("Drawdown", fontsize=11)

    plt.tight_layout()
    out_path = f"backtest_ytd_{year}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  📈 Chart saved → {out_path}")
    plt.show()


# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def main():
    args    = sys.argv[1:]
    do_plot = "--plot" in args

    # Parse --year YYYY
    year = datetime.now().year
    if "--year" in args:
        idx = args.index("--year")
        year = int(args[idx + 1])

    ytd_start = pd.Timestamp(f"{year}-01-01")
    # Need 14 months of history for SMA200 (200 trading days ≈ 10 months),
    # plus 60 days for momentum, plus buffer → start 16 months before YTD
    hist_start = (ytd_start - pd.DateOffset(months=16)).strftime("%Y-%m-%d")
    today      = datetime.now().strftime("%Y-%m-%d")

    cfg = CONFIG.copy()

    # 1. Download data
    data = load_data(hist_start, today)

    # 2. Build indicator matrix
    print("⚙️   Computing indicators …")
    ind = build_indicators(data, cfg)

    # 3. Run both versions
    print(f"🔁  Simulating APEX v1 (original) …")
    bt_v1 = run_backtest_v1(ind, ytd_start)
    print(f"🔁  Simulating APEX v2 (improved) …")
    bt_v2 = run_backtest(ind, cfg, ytd_start)

    # Share benchmarks from v2 bt
    bt_v1["voo_nav"]  = bt_v2["voo_nav"]
    bt_v1["tqqq_nav"] = bt_v2["tqqq_nav"]
    bt_v1["qqq_nav"]  = bt_v2["qqq_nav"]
    bt_v1["qld_nav"]  = bt_v2["qld_nav"]

    # 4. Metrics
    print(f"\n{'═'*72}")
    print(f"  APEX Strategy — YTD {year} Backtest  "
          f"(through {bt_v2.index[-1].strftime('%Y-%m-%d')}, "
          f"{len(bt_v2)} trading days)")
    print(f"{'═'*72}\n")

    v1_m   = metrics(bt_v1["apex_nav"], "APEX v1.0 (original)")
    v2_m   = metrics(bt_v2["apex_nav"], "APEX v2.0 (improved) ◀")
    voo_m  = metrics(bt_v2["voo_nav"],  "Buy & Hold VOO")
    tqqq_m = metrics(bt_v2["tqqq_nav"], "Buy & Hold TQQQ")
    qqq_m  = metrics(bt_v2["qqq_nav"],  "Buy & Hold QQQ  ← target")
    qld_m  = metrics(bt_v2["qld_nav"],  "Buy & Hold QLD")

    all_metrics = [v2_m, v1_m, voo_m, qqq_m, qld_m, tqqq_m]
    print_metrics_table(all_metrics)

    print(f"\n  v2 vs v1 CAGR delta:   {v2_m['cagr']-v1_m['cagr']:+.1%}")
    print(f"  v2 vs QQQ CAGR delta:  {v2_m['cagr']-qqq_m['cagr']:+.1%}")
    print(f"  v2 Sharpe vs VOO:      {v2_m['sharpe']-voo_m['sharpe']:+.2f}")

    # v2 changes summary
    print(f"\n  v2 changes applied:")
    print(f"    1. EMA death cross removed from Layer 1 (now Layer-2 -4 only)")
    print(f"    2. VIX momentum (5d Δ) added as 9th scoring dimension")
    print(f"    3. Dynamic vol target: 25% when score≥5 & VIX<20 (else 20%)")
    print(f"    4. alloc_map score=6 raised from 85% → 90%")

    # 5. Allocation timeline (v2)
    print_monthly_allocations(bt_v2)
    print(f"\n  v1 vs v2 monthly TQQQ allocation:")
    print(f"  {'Month':<10} {'v1':>6} {'v2':>6} {'delta':>8}")
    print("  " + "─" * 34)
    for period in bt_v2["tqqq_alloc"].resample("ME").mean().index:
        end = period
        mask = bt_v2.index.to_period("M") == period.to_period("M")
        a1 = bt_v1["tqqq_alloc"][mask].mean()
        a2 = bt_v2["tqqq_alloc"][mask].mean()
        print(f"  {period.strftime('%Y-%m'):<10} {a1:>5.0%} {a2:>6.0%} {a2-a1:>+7.0%}")

    print_regime_changes(bt_v2)

    # 6. Optional plot
    if do_plot:
        # Add v1 NAV to v2 df for comparison
        bt_v2["apex_v1_nav"] = bt_v1["apex_nav"].values
        plot_results(bt_v2, year)
    else:
        print("\n  Tip: run with --plot to generate a comparison chart")

    print(f"\n{'═'*72}\n")

    return bt_v2, all_metrics


if __name__ == "__main__":
    bt, m = main()
