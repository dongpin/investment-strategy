"""
NOVA Strategy — Historical Backtest
=====================================
Runs the NOVA strategy (Cash / SOXX 1× / USD 2× / SOXL 3×) over two periods:
  • 5-year:  2021-01-01 → today  (includes 2022 semi bear −47%, AI boom)
  • 10-year: 2016-01-01 → today  (includes COVID crash, full 2022 bear)

Benchmarks: SOXX B&H, USD B&H, SOXL B&H, 50/50 SOXX/SOXL static.

Realistic cost model:
  • No synthetic decay — actual LETF historical prices capture vol decay
  • Slippage: 8 bps one-way on each trade
  • Entry confirmation: 2 days  |  Exit: 1 day
  • T+1 execution (signal day → trade next day)

Usage:
    python backtest_nova.py           # text output
    python backtest_nova.py --plot    # + save PNG chart
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


# ══════════════════════════════════════════════════════════════════
#  TIER CONSTANTS  (kept in sync with nova_strategy.py)
# ══════════════════════════════════════════════════════════════════

TIER_ALLOC = {
    "cash":      (0.0, 0.0, 0.0, 1.0),   # (soxx, usd, soxl, cash)
    "soxx":      (1.0, 0.0, 0.0, 0.0),
    "soxx_usd":  (0.5, 0.5, 0.0, 0.0),
    "usd":       (0.0, 1.0, 0.0, 0.0),
    "usd_soxl":  (0.0, 0.5, 0.5, 0.0),
    "soxl":      (0.0, 0.0, 1.0, 0.0),
}
TIER_EFF_LEV = {
    "cash": 0.0, "soxx": 1.0, "soxx_usd": 1.5,
    "usd":  2.0, "usd_soxl": 2.5, "soxl": 3.0,
}
TIER_ORDER    = ["cash", "soxx", "soxx_usd", "usd", "usd_soxl", "soxl"]
TIER_BASE_VOLS = {
    "cash": 0.0, "soxx": 0.35, "soxx_usd": 0.50,
    "usd": 0.65, "usd_soxl": 0.80, "soxl": 0.95,
}

BT_CONFIG = {
    # Layer 1 thresholds (NEUTRAL regime defaults)
    "dd_threshold":          -15.0,
    "dd_lookback":            126,
    "vix_threshold":          28.0,
    "extreme_dd_threshold":  -30.0,
    # Layer 2 thresholds (score → tier)
    "score_thresholds": {
        "cash":     (float("-inf"), -8),
        "soxx":     (-7,  -2),
        "soxx_usd": (-1,   4),
        "usd":      ( 5,  10),
        "usd_soxl": (11,  15),
        "soxl":     (16, float("inf")),
    },
    # Layer 3
    "target_vol":       0.35,
    "target_vol_bull":  0.45,
    "vol_window":        15,
    # Trailing stop
    "trail_window":  15,
    "trail_pct":     0.90,
    # Realistic sim
    "confirm_enter": 2,
    "confirm_exit":  1,
    "slip_bps":      8.0,
}

HIST_DOWNLOAD = "2013-01-01"   # enough history for SMA200 + 120d momentum


# ══════════════════════════════════════════════════════════════════
#  DATA
# ══════════════════════════════════════════════════════════════════

def load_data(end: str) -> dict:
    print(f"📡  Downloading history {HIST_DOWNLOAD} → {end} …")

    def dl(ticker):
        df = yf.download(ticker, start=HIST_DOWNLOAD, end=end,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"No data: {ticker}")
        s = df["Close"].squeeze()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.dropna().rename(ticker)

    data = {k: dl(t) for k, t in [
        ("soxx", "SOXX"), ("usd",  "USD"),  ("soxl", "SOXL"),
        ("spy",  "SPY"),  ("nvda", "NVDA"), ("mu",   "MU"),
        ("vix",  "^VIX"),
    ]}
    # ^IRX = 13-week T-bill annualised yield (%) — used as SGOV proxy in 3T backtest
    try:
        irx_raw = yf.download("^IRX", start=HIST_DOWNLOAD, end=end,
                              progress=False, auto_adjust=True)["Close"].squeeze().dropna()
        data["irx"] = irx_raw.rename("^IRX")
        print(f"    IRX  : {len(irx_raw)} rows  (latest: {irx_raw.iloc[-1]:.2f}%/yr)")
    except Exception:
        data["irx"] = pd.Series(dtype=float)
        print("    IRX  : unavailable — cash yield set to 0%")

    for k, s in data.items():
        print(f"    {k.upper():<5}: {len(s)} rows  "
              f"({s.index[0].date()} → {s.index[-1].date()})")
    return data


# ══════════════════════════════════════════════════════════════════
#  INDICATORS  — build full historical matrix
# ══════════════════════════════════════════════════════════════════

def build_indicators(data: dict) -> pd.DataFrame:
    """Vectorised indicator computation across the full history."""
    soxx = data["soxx"]
    spy  = data["spy"]
    soxl = data["soxl"]
    nvda = data["nvda"]
    mu   = data["mu"]
    vix  = data["vix"]

    idx = soxx.index

    def rsi(s, n=14):
        d = s.diff()
        g = d.clip(lower=0).rolling(n).mean()
        l = (-d.clip(upper=0)).rolling(n).mean()
        return 100 - 100 / (1 + g / l)

    ema20  = soxx.ewm(span=20,  adjust=False).mean()
    ema50  = soxx.ewm(span=50,  adjust=False).mean()
    ema200 = soxx.ewm(span=200, adjust=False).mean()
    sma200 = soxx.rolling(200).mean()
    rsi14  = rsi(soxx)
    dd_ath = (soxx / soxx.cummax() - 1) * 100
    dd_126 = (soxx / soxx.rolling(126).max() - 1) * 100
    mom20  = (soxx / soxx.shift(20)  - 1) * 100
    mom60  = (soxx / soxx.shift(60)  - 1) * 100
    mom120 = (soxx / soxx.shift(120) - 1) * 100

    spy_a  = spy.reindex(idx, method="ffill")
    rel_str = ((soxx / soxx.shift(60)) / (spy_a / spy_a.shift(60)) - 1) * 100

    nvda_a     = nvda.reindex(idx, method="ffill")
    mu_a       = mu.reindex(idx, method="ffill")
    nvda_mom20  = (nvda_a / nvda_a.shift(20)  - 1) * 100
    nvda_mom120 = (nvda_a / nvda_a.shift(120) - 1) * 100   # 6-month — regime proxy
    mu_mom20    = (mu_a   / mu_a.shift(20)    - 1) * 100
    mu_mom120   = (mu_a   / mu_a.shift(120)   - 1) * 100   # 6-month — regime proxy

    # Daily risk-free yield from ^IRX (annualised %)  → daily fraction
    irx_raw   = data.get("irx", pd.Series(dtype=float))
    irx_daily = (irx_raw / 100 / 252).reindex(idx, method="ffill").fillna(0.0)

    soxl_a          = soxl.reindex(idx, method="ffill")
    soxl_vol        = soxl_a.pct_change().rolling(15).std() * np.sqrt(252)
    soxl_trail_high = soxl_a.rolling(15).max()

    usd_a           = data["usd"].reindex(idx, method="ffill")
    usd_vol         = usd_a.pct_change().rolling(15).std() * np.sqrt(252)
    usd_trail_high  = usd_a.rolling(15).max()

    vix_a = vix.reindex(idx, method="ffill")

    ind = pd.DataFrame({
        "soxx":            soxx,
        "usd":             usd_a,
        "soxl":            soxl_a,
        "ema20":           ema20,
        "ema50":           ema50,
        "ema200":          ema200,
        "sma200":          sma200,
        "rsi":             rsi14,
        "dd_ath":          dd_ath,
        "dd_126":          dd_126,
        "mom20":           mom20,
        "mom60":           mom60,
        "mom120":          mom120,
        "rel_str":         rel_str,
        "nvda_mom20":      nvda_mom20,
        "nvda_mom120":     nvda_mom120,
        "mu_mom20":        mu_mom20,
        "mu_mom120":       mu_mom120,
        "irx_daily":       irx_daily,
        "soxl_vol":        soxl_vol,
        "soxl_trail_high": soxl_trail_high,
        "usd_vol":         usd_vol,
        "usd_trail_high":  usd_trail_high,
        "vix":             vix_a,
    }, index=idx)

    return ind


# ══════════════════════════════════════════════════════════════════
#  SIGNAL COMPUTATION  (row-wise — mirrors nova_strategy.py)
# ══════════════════════════════════════════════════════════════════

def _score_row(row: pd.Series) -> int:
    """Compute raw signal score from one indicator row."""
    def g(col):
        v = row.get(col, np.nan)
        return float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else None

    m20  = g("mom20");  m60  = g("mom60");  m120 = g("mom120")
    rsi  = g("rsi");    ema20 = g("ema20"); ema50 = g("ema50")
    ema200 = g("ema200"); rel_str = g("rel_str")
    nvda_m = g("nvda_mom20"); mu_m = g("mu_mom20")
    vix    = g("vix");  dd_ath = g("dd_ath")

    sc = 0

    # D1: 20d momentum (±3)
    if m20 is not None:
        if   m20 > 10:  sc += 3
        elif m20 >  5:  sc += 2
        elif m20 >  2:  sc += 1
        elif m20 > -2:  sc += 0
        elif m20 > -5:  sc -= 1
        elif m20 > -10: sc -= 2
        else:           sc -= 3

    # D2: 60d momentum (±2)
    if m60 is not None:
        if   m60 >  15: sc += 2
        elif m60 >   5: sc += 1
        elif m60 >  -5: sc += 0
        elif m60 > -15: sc -= 1
        else:           sc -= 2

    # D3: 120d momentum (±1)
    if m120 is not None:
        if   m120 >  10: sc += 1
        elif m120 > -10: sc += 0
        else:            sc -= 1

    # D4: RSI (±2)
    if rsi is not None:
        if   rsi > 70: sc += 0
        elif rsi > 60: sc += 2
        elif rsi > 50: sc += 1
        elif rsi > 40: sc -= 1
        else:          sc -= 2

    # D5: EMA20/EMA50 (±2)
    if ema20 is not None and ema50 is not None:
        sc += 2 if ema20 > ema50 else -2

    # D6: EMA50/EMA200 (±2)
    if ema50 is not None and ema200 is not None:
        sc += 2 if ema50 > ema200 else -2

    # D7: SOXX vs SPY relative strength (±2)
    if rel_str is not None:
        if   rel_str >  10: sc += 2
        elif rel_str >   3: sc += 1
        elif rel_str >  -3: sc += 0
        elif rel_str > -10: sc -= 1
        else:               sc -= 2

    # D8: NVDA 20d momentum (±3)
    if nvda_m is not None:
        if   nvda_m >  15: sc += 3
        elif nvda_m >   7: sc += 2
        elif nvda_m >   2: sc += 1
        elif nvda_m >  -2: sc += 0
        elif nvda_m >  -7: sc -= 1
        elif nvda_m > -15: sc -= 2
        else:              sc -= 3

    # D9: MU 20d momentum (±2)
    if mu_m is not None:
        if   mu_m >  10: sc += 2
        elif mu_m >   3: sc += 1
        elif mu_m >  -3: sc += 0
        elif mu_m > -10: sc -= 1
        else:            sc -= 2

    # D10: VIX (±2)
    if vix is not None:
        if   vix < 15: sc += 2
        elif vix < 20: sc += 1
        elif vix < 25: sc += 0
        elif vix < 30: sc -= 1
        else:          sc -= 2

    # D11: ATH drawdown (±1)
    if dd_ath is not None:
        if   dd_ath > -10: sc += 1
        elif dd_ath > -25: sc += 0
        else:              sc -= 1

    return sc


def _score_to_tier(score: int, cfg: dict) -> str:
    th = cfg["score_thresholds"]
    for tier in ["soxl", "usd_soxl", "usd", "soxx_usd", "soxx", "cash"]:
        lo, hi = th[tier]
        if lo <= score <= hi:
            return tier
    return "cash"


def compute_signal(row: pd.Series, cfg: dict) -> tuple:
    """
    Full signal pipeline for one indicator row.
    Returns (soxx_w, usd_w, soxl_w, cash_w, tier, raw_score).
    """
    def g(col):
        v = row.get(col, np.nan)
        return float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else None

    soxx_p  = g("soxx");  sma200  = g("sma200")
    dd126   = g("dd_126"); vix    = g("vix")
    soxl_v  = g("soxl_vol"); soxl_p = g("soxl")
    soxl_hi = g("soxl_trail_high")

    # Layer 1: force cash on extreme drawdown
    if dd126 is not None and dd126 < cfg["extreme_dd_threshold"]:
        return (0.0, 0.0, 0.0, 1.0, "cash", 0)

    # Layer 2: score
    score = _score_row(row)
    tier  = _score_to_tier(score, cfg)

    # Layer 1: standard circuit breaker → cap at SOXX
    cb = False
    if dd126  is not None and dd126  < cfg["dd_threshold"]:      cb = True
    if vix    is not None and vix    > cfg["vix_threshold"]:      cb = True
    if soxx_p is not None and sma200 is not None and soxx_p < sma200: cb = True
    if cb and TIER_ORDER.index(tier) > TIER_ORDER.index("soxx"):
        tier = "soxx"

    # Layer 3: vol cap → step down if needed
    if soxl_v is not None and soxl_v > 0:
        target = cfg["target_vol"]
        if score >= 6 and vix is not None and vix < 20:
            target = cfg.get("target_vol_bull", 0.45)
        scale = soxl_v / 0.95
        idx_t = TIER_ORDER.index(tier)
        while idx_t > 1:
            if TIER_BASE_VOLS[TIER_ORDER[idx_t]] * scale <= target:
                break
            idx_t -= 1
        tier = TIER_ORDER[idx_t]

    # Trailing stop: step down from SOXL/USD-SOXL tiers
    if (soxl_p is not None and soxl_hi is not None and
            soxl_hi > 0 and soxl_p < soxl_hi * cfg["trail_pct"]):
        if tier == "soxl":
            tier = "usd_soxl"
        elif tier == "usd_soxl":
            tier = "usd"

    a = TIER_ALLOC[tier]
    return (a[0], a[1], a[2], a[3], tier, score)


# ══════════════════════════════════════════════════════════════════
#  NOVA-USD: USD / CASH 2-ASSET SIGNAL  (mirrors nova_strategy.py)
# ══════════════════════════════════════════════════════════════════

# Regime-based overrides — kept in sync with nova_strategy.py
_REGIME_USD_CASH_BT = {
    "BULL":    {"bull_threshold": 3,  "neutral_threshold": -4,
                "target_vol": 0.65, "target_vol_bull": 0.80,
                "vix_threshold": 30.0, "dd_threshold": -18.0,
                "extreme_dd_threshold": -35.0},
    "NEUTRAL": {"bull_threshold": 5,  "neutral_threshold": -2,
                "target_vol": 0.55, "target_vol_bull": 0.70,
                "vix_threshold": 28.0, "dd_threshold": -15.0,
                "extreme_dd_threshold": -30.0},
    "BEAR":    {"bull_threshold": 8,  "neutral_threshold":  0,
                "target_vol": 0.45, "target_vol_bull": 0.55,
                "vix_threshold": 22.0, "dd_threshold": -10.0,
                "extreme_dd_threshold": -22.0},
}

BT_USD_CASH_CONFIG = {
    **_REGIME_USD_CASH_BT["NEUTRAL"],  # default to NEUTRAL thresholds
    "trail_pct":     0.92,
    "confirm_enter": 2,
    "confirm_exit":  1,
    "slip_bps":      8.0,
}


def compute_signal_usd_cash(row: pd.Series, cfg: dict) -> tuple:
    """
    USD/Cash signal for one indicator row.
    Returns (usd_pct, cash_pct, zone, raw_score).
    """
    def g(col):
        v = row.get(col, np.nan)
        return float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else None

    soxx_p  = g("soxx");  sma200   = g("sma200")
    dd126   = g("dd_126"); vix     = g("vix")
    usd_v   = g("usd_vol"); usd_p  = g("usd")
    usd_hi  = g("usd_trail_high")

    # Layer 1: extreme CB → cash
    if dd126 is not None and dd126 < cfg["extreme_dd_threshold"]:
        return (0.0, 1.0, "cash", 0)

    # Layer 2: score
    score = _score_row(row)

    # Zone from score
    if score >= cfg["bull_threshold"]:
        usd_w = 1.0
    elif score >= cfg["neutral_threshold"]:
        usd_w = 0.5
    else:
        usd_w = 0.0

    # Layer 1: standard CB → cap at neutral
    cb = False
    if dd126  is not None and dd126  < cfg["dd_threshold"]:       cb = True
    if vix    is not None and vix    > cfg["vix_threshold"]:       cb = True
    if soxx_p is not None and sma200 is not None and soxx_p < sma200: cb = True
    if cb and usd_w > 0.5:
        usd_w = 0.5

    # Layer 3: vol cap on USD
    if usd_v is not None and usd_v > 0:
        target = cfg.get("target_vol", 0.55)
        if score >= cfg["bull_threshold"] and vix is not None and vix < 20:
            target = cfg.get("target_vol_bull", 0.70)
        cap   = min(1.0, target / usd_v)
        usd_w = min(usd_w, cap)

    # Trailing stop on USD
    if (usd_p is not None and usd_hi is not None and
            usd_hi > 0 and usd_p < usd_hi * cfg.get("trail_pct", 0.92)):
        usd_w = min(usd_w, 0.5)

    cash_w = 1.0 - usd_w
    if   usd_w >= 0.99: zone = "usd_full"
    elif usd_w >= 0.01: zone = "usd_half"
    else:               zone = "cash"

    return (usd_w, cash_w, zone, score)


def run_backtest_ideal_usd_cash(ind: pd.DataFrame, cfg: dict,
                                start: pd.Timestamp) -> pd.DataFrame:
    """Ideal USD/Cash backtest: signal → allocation changes same-day close."""
    sub = ind[ind.index >= start].copy()
    nav = 1.0
    cur_usd = cur_cash = 0.0
    records = []

    for i, (date, row) in enumerate(sub.iterrows()):
        tgt_usd, tgt_cash, zone, score = compute_signal_usd_cash(row, cfg)

        if i == 0:
            cur_usd, cur_cash = tgt_usd, tgt_cash
            records.append({"date": date, "nav": nav, "zone": zone,
                             "score": score, "eff_lev": cur_usd * 2,
                             "usd_w": cur_usd, "cash_w": cur_cash})
            continue

        prev  = sub.iloc[i - 1]
        p_usd = prev.get("usd", np.nan)
        c_usd = row.get("usd", p_usd)
        r_usd = (c_usd / p_usd - 1) if (p_usd and p_usd > 0) else 0.0
        nav  *= (1 + cur_usd * r_usd)

        cur_usd, cur_cash = tgt_usd, tgt_cash
        records.append({"date": date, "nav": nav, "zone": zone,
                         "score": score, "eff_lev": cur_usd * 2,
                         "usd_w": cur_usd, "cash_w": cur_cash})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks_usd_cash(bt, sub)
    return bt


def run_backtest_realistic_usd_cash(ind: pd.DataFrame, cfg: dict,
                                    start: pd.Timestamp) -> pd.DataFrame:
    """Realistic USD/Cash: confirmation delay + slippage."""
    sub  = ind[ind.index >= start].copy()
    slip = cfg.get("slip_bps", 8.0) / 10_000.0
    conf_en = cfg.get("confirm_enter", 2)
    conf_ex = cfg.get("confirm_exit",  1)

    nav = 1.0
    cur_usd = cur_cash = 0.0
    sig_buf = []  # rolling zone history

    records = []
    for i, (date, row) in enumerate(sub.iterrows()):
        tgt_usd, tgt_cash, zone, score = compute_signal_usd_cash(row, cfg)

        if i == 0:
            cur_usd, cur_cash = tgt_usd, tgt_cash
            sig_buf.append(tgt_usd)
            records.append({"date": date, "nav": nav, "zone": zone,
                             "score": score, "eff_lev": cur_usd * 2,
                             "usd_w": cur_usd, "cash_w": cur_cash})
            continue

        # Daily P&L
        prev  = sub.iloc[i - 1]
        p_usd = prev.get("usd", np.nan)
        c_usd = row.get("usd", p_usd)
        r_usd = (c_usd / p_usd - 1) if (p_usd and p_usd > 0) else 0.0
        nav  *= (1 + cur_usd * r_usd)

        sig_buf.append(tgt_usd)
        if len(sig_buf) > max(conf_en, conf_ex) + 1:
            sig_buf.pop(0)

        new_usd = cur_usd
        # Immediate cash on extreme CB
        if tgt_usd == 0.0 and cur_usd > 0.0 and zone == "cash":
            new_usd = 0.0
        elif date.weekday() == 0:   # Monday rebalance
            is_up  = tgt_usd > cur_usd
            req    = conf_en if is_up else conf_ex
            if len(sig_buf) >= req:
                recent = sig_buf[-req:]
                if is_up  and all(r >= tgt_usd - 0.01 for r in recent):
                    new_usd = tgt_usd
                elif not is_up and all(r <= tgt_usd + 0.01 for r in recent):
                    new_usd = tgt_usd

        changed = abs(new_usd - cur_usd)
        if changed > 0.01:
            nav *= (1 - slip * changed)

        cur_usd  = new_usd
        cur_cash = 1.0 - cur_usd
        if   cur_usd >= 0.99: cur_zone = "usd_full"
        elif cur_usd >= 0.01: cur_zone = "usd_half"
        else:                 cur_zone = "cash"

        records.append({"date": date, "nav": nav, "zone": cur_zone,
                         "score": score, "eff_lev": cur_usd * 2,
                         "usd_w": cur_usd, "cash_w": cur_cash})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks_usd_cash(bt, sub)
    return bt


def _attach_benchmarks_usd_cash(bt: pd.DataFrame, sub: pd.DataFrame) -> None:
    for col, asset in [("soxx_nav", "soxx"), ("usd_nav", "usd"), ("soxl_nav", "soxl")]:
        s = sub[asset].dropna()
        if len(s):
            bt[col] = (sub[asset] / s.iloc[0]).reindex(bt.index)


def run_period_usd_cash(ind: pd.DataFrame, start: pd.Timestamp,
                        label: str, do_plot: bool, initial: float = 100_000):
    print(f"\n{'═'*72}")
    print(f"  NOVA-USD Strategy — {label}")
    print(f"  {start.date()} → {ind.index[-1].date()}  |  initial: ${initial:,}")
    print(f"{'═'*72}")

    print("⚙️   Running ideal simulation…")
    bt_i = run_backtest_ideal_usd_cash(ind, BT_USD_CASH_CONFIG, start)
    print("⚙️   Running realistic simulation…")
    bt_r = run_backtest_realistic_usd_cash(ind, BT_USD_CASH_CONFIG, start)

    results = [
        metrics_final(bt_i["nav"],       f"NOVA-USD {label} (ideal)",     initial),
        metrics_final(bt_r["nav"],        f"NOVA-USD {label} (realistic)", initial),
        metrics_final(bt_i["soxx_nav"],  "SOXX B&H (1×)",                initial),
        metrics_final(bt_i["usd_nav"],   "USD B&H  (2×)",                initial),
        metrics_final(bt_i["soxl_nav"],  "SOXL B&H (3×)",                initial),
    ]
    print_summary_table(results, initial)

    # Year-by-year
    yi = yearly_stats(bt_i["nav"])
    yr = yearly_stats(bt_r["nav"])
    ys = yearly_stats(bt_i["soxx_nav"].dropna())
    yu = yearly_stats(bt_i["usd_nav"].dropna())
    yl = yearly_stats(bt_i["soxl_nav"].dropna())
    current_year = datetime.now().year

    print(f"\n{'═'*100}")
    print(f"  Year-by-Year Returns — NOVA-USD {label}")
    print(f"{'═'*100}")
    print(f"  {'Year':<6} {'NOVA-USD ideal':>14} {'NOVA-USD real':>14} "
          f"{'SOXX':>8} {'USD 2×':>8} {'SOXL':>8}  {'DD ideal':>9} {'DD real':>9}  {'Avg Lev':>8}")
    print("  " + "─" * 97)
    for y in sorted(set(yi.index) | set(yr.index)):
        tag = " *" if y == current_year else "  "
        def f(df, col="ret"):
            v = df[col].get(y, float("nan")) if len(df) else float("nan")
            return f"{v:>+7.1%}" if not np.isnan(v) else f"{'—':>7}"
        yr_lev = bt_i[bt_i.index.year == y]["eff_lev"]
        avg_lev = f"{yr_lev.mean():.2f}×" if len(yr_lev) else "—"
        print(f"  {y}{tag:<3} {f(yi):>14} {f(yr):>14} {f(ys):>8} {f(yu):>8} {f(yl):>8}  "
              f"{f(yi,'max_dd'):>9} {f(yr,'max_dd'):>9}  {avg_lev:>8}")
    print("  " + "─" * 97)
    print("  * = YTD")

    gap_analysis(bt_i, bt_r)
    print_bear_periods(bt_i, bt_r)

    if do_plot:
        import os
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker

        fig, axes = plt.subplots(3, 1, figsize=(15, 12),
                                 gridspec_kw={"height_ratios": [3, 1.5, 1]})
        fig.suptitle(f"NOVA-USD Strategy — {label}  (USD 2× / Cash)",
                     fontsize=14, fontweight="bold")

        ax = axes[0]
        ax.semilogy(bt_i.index, bt_i["nav"],   label="NOVA-USD (ideal)",
                    color="#E91E63", lw=2.5, zorder=7)
        ax.semilogy(bt_r.index, bt_r["nav"],    label="NOVA-USD (realistic)",
                    color="#C2185B", lw=1.8, ls="--", zorder=6)
        ax.semilogy(bt_i.index, bt_i["soxx_nav"], label="SOXX B&H (1×)",
                    color="#2E7D32", lw=1.5, ls="--")
        ax.semilogy(bt_i.index, bt_i["usd_nav"],  label="USD B&H (2×)",
                    color="#7B1FA2", lw=1.5, ls="-.")
        ax.semilogy(bt_i.index, bt_i["soxl_nav"], label="SOXL B&H (3×)",
                    color="#C62828", lw=1.2, ls=":", alpha=0.7)
        for yr_l in range(bt_i.index[0].year, bt_i.index[-1].year + 2):
            ax.axvline(pd.Timestamp(f"{yr_l}-01-01"), color="gray", alpha=0.15, lw=0.8)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}×"))
        ax.set_ylabel("Portfolio Growth (log, start=1×)")
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(True, which="both", alpha=0.2)
        ax.set_title("Cumulative Return — Log Scale", fontsize=11)

        ax2 = axes[1]
        ax2.fill_between(bt_i.index, bt_i["usd_w"],  alpha=0.7, color="#7B1FA2",
                         label="USD weight (ideal)")
        ax2.fill_between(bt_r.index, bt_r["usd_w"],  alpha=0.35, color="#E91E63",
                         label="USD weight (realistic)")
        ax2.set_ylim(-0.05, 1.1)
        ax2.set_yticks([0, 0.5, 1.0])
        ax2.set_yticklabels(["0% (Cash)", "50%", "100% (USD)"])
        ax2.set_ylabel("USD Weight")
        ax2.legend(loc="upper left", fontsize=9)
        ax2.grid(True, alpha=0.2)
        ax2.set_title("USD Allocation Over Time", fontsize=11)

        ax3 = axes[2]
        ax3.fill_between(bt_i.index, bt_i["score"].clip(-22, 22), 0,
                         where=bt_i["score"] >= 0, alpha=0.7, color="#4CAF50",
                         label="Bullish")
        ax3.fill_between(bt_i.index, bt_i["score"].clip(-22, 22), 0,
                         where=bt_i["score"] < 0, alpha=0.7, color="#F44336",
                         label="Bearish")
        ax3.axhline(0, color="black", lw=0.8)
        ax3.axhline(5, color="#7B1FA2", lw=0.5, ls="--", alpha=0.7)   # bull threshold
        ax3.axhline(-2, color="#F44336", lw=0.5, ls="--", alpha=0.7)  # bear threshold
        ax3.set_ylabel("Signal Score")
        ax3.legend(loc="upper left", fontsize=9)
        ax3.grid(True, alpha=0.2)
        ax3.set_title("Layer 2 Signal Score  (dashed = zone boundaries)", fontsize=11)
        for yr_l in range(bt_i.index[0].year, bt_i.index[-1].year + 2):
            for axx in [ax2, ax3]:
                axx.axvline(pd.Timestamp(f"{yr_l}-01-01"), color="gray", alpha=0.15, lw=0.8)

        plt.tight_layout()
        out_dir  = os.path.dirname(os.path.abspath(__file__))
        slug     = label.lower().replace("-","").replace(" ","_")
        out_path = os.path.join(out_dir, f"backtest_nova_usd_{slug}.png")
        plt.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"\n  📈  Chart saved → {out_path}")
        plt.show()

    return bt_i, bt_r, results


# ══════════════════════════════════════════════════════════════════
#  NOVA-3T: SGOV / SOXX / USD — REGIME-GATED 3-TIER STRATEGY
# ══════════════════════════════════════════════════════════════════
#
#  Regime gates the maximum allowed tier:
#    BULL    →  SGOV / SOXX / USD  (full range)
#    NEUTRAL →  SGOV / SOXX only   (no leverage)
#    BEAR    →  SGOV only
#
#  Score selects the tier within the regime ceiling.
#  Cash (SGOV) earns the T-bill yield (^IRX) — not 0%.

BT_3T_CONFIG = {
    # Score thresholds per regime (BULL defaults; NEUTRAL/BEAR override max_tier)
    "bull_threshold":         5,   # score ≥ 5 → USD   (BULL only)
    "soxx_threshold_bull":   -2,   # score -2..4 → SOXX (BULL)
    "soxx_threshold_neutral": 0,   # score ≥ 0 → SOXX  (NEUTRAL; tighter)

    # Circuit breakers (NEUTRAL regime defaults)
    "dd_threshold":          -15.0,
    "dd_lookback":            126,
    "vix_threshold":          28.0,
    "extreme_dd_threshold":  -30.0,

    # Vol cap on USD (BULL only)
    "target_vol":      0.55,
    "target_vol_bull": 0.70,

    # Trailing stop on USD
    "trail_pct": 0.92,

    # Realistic sim
    "confirm_enter": 2,
    "confirm_exit":  1,
    "slip_bps":      8.0,
}

# Per-regime CB overrides (same as NOVA_3T in nova_strategy.py)
_REGIME_3T_BT = {
    "BULL":    {"max_tier": "usd",  "dd_threshold": -18.0,
                "extreme_dd_threshold": -35.0, "vix_threshold": 30.0,
                "soxx_threshold": -2},
    "NEUTRAL": {"max_tier": "soxx", "dd_threshold": -15.0,
                "extreme_dd_threshold": -30.0, "vix_threshold": 28.0,
                "soxx_threshold": 0},
    "BEAR":    {"max_tier": "sgov", "dd_threshold": -10.0,
                "extreme_dd_threshold": -22.0, "vix_threshold": 22.0,
                "soxx_threshold": 99},
}


def _estimate_regime_bt(row: pd.Series) -> str:
    """
    Approximate Layer 0 regime from rolling NVDA + MU 120-day price momentum.
    Used only in backtesting where quarterly revenue data is unavailable.

    BULL:    NVDA 6m > +15% AND MU 6m > +5%  (both demand signals positive)
    BEAR:    NVDA 6m < -15% OR  MU 6m < -10% (either signal deeply negative)
    NEUTRAL: everything else
    """
    def g(col):
        v = row.get(col, np.nan)
        return float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else None

    n120 = g("nvda_mom120")
    m120 = g("mu_mom120")

    if n120 is None or m120 is None:
        return "NEUTRAL"
    if n120 > 15 and m120 > 5:
        return "BULL"
    if n120 < -15 or m120 < -10:
        return "BEAR"
    return "NEUTRAL"


def compute_signal_3t(row: pd.Series, cfg: dict) -> tuple:
    """
    NOVA-3T signal for one indicator row.
    Returns (soxx_w, usd_w, sgov_w, tier, regime, raw_score).
    """
    def g(col):
        v = row.get(col, np.nan)
        return float(v) if not (v is None or (isinstance(v, float) and np.isnan(v))) else None

    soxx_p = g("soxx");  sma200 = g("sma200")
    dd126  = g("dd_126"); vix   = g("vix")
    usd_v  = g("usd_vol"); usd_p = g("usd")
    usd_hi = g("usd_trail_high")

    # Regime determines ceiling
    regime = _estimate_regime_bt(row)
    rcfg   = {**cfg, **_REGIME_3T_BT.get(regime, _REGIME_3T_BT["NEUTRAL"])}
    max_tier      = rcfg["max_tier"]
    soxx_threshold = rcfg["soxx_threshold"]
    extr_dd = rcfg["extreme_dd_threshold"]
    dd_thr  = rcfg["dd_threshold"]
    vix_thr = rcfg["vix_threshold"]

    # Layer 1: extreme CB → SGOV always
    if dd126 is not None and dd126 < extr_dd:
        return (0.0, 0.0, 1.0, "sgov", regime, 0)

    # BEAR regime: always SGOV
    if max_tier == "sgov":
        return (0.0, 0.0, 1.0, "sgov", regime, 0)

    # Layer 2: score
    score = _score_row(row)

    # Standard CB → cap at SOXX (block USD even in BULL)
    cb = (dd126 is not None and dd126 < dd_thr) or \
         (vix   is not None and vix   > vix_thr) or \
         (soxx_p is not None and sma200 is not None and soxx_p < sma200)

    # NEUTRAL regime: SOXX or SGOV only
    if max_tier == "soxx":
        if cb or score < soxx_threshold:
            return (0.0, 0.0, 1.0, "sgov", regime, score)
        return (1.0, 0.0, 0.0, "soxx", regime, score)

    # BULL regime: full 3-tier logic
    bull_threshold = cfg.get("bull_threshold", 5)

    # Score → raw tier
    if score >= bull_threshold:
        tier = "usd"
    elif score >= soxx_threshold:
        tier = "soxx"
    else:
        tier = "sgov"

    # CB caps at SOXX in BULL regime
    if cb and tier == "usd":
        tier = "soxx"

    # Vol cap on USD
    if tier == "usd" and usd_v is not None and usd_v > 0:
        target = cfg["target_vol"]
        if score >= bull_threshold and vix is not None and vix < 20:
            target = cfg.get("target_vol_bull", 0.70)
        if usd_v > target:
            tier = "soxx"   # step down, don't blend

    # Trailing stop on USD
    if (tier == "usd" and usd_p is not None and usd_hi is not None
            and usd_hi > 0 and usd_p < usd_hi * cfg.get("trail_pct", 0.92)):
        tier = "soxx"

    if tier == "usd":
        return (0.0, 1.0, 0.0, "usd",  regime, score)
    if tier == "soxx":
        return (1.0, 0.0, 0.0, "soxx", regime, score)
    return (0.0, 0.0, 1.0, "sgov", regime, score)


def run_backtest_ideal_3t(ind: pd.DataFrame, cfg: dict,
                          start: pd.Timestamp) -> pd.DataFrame:
    """Ideal 3T: signal → allocation changes same-day close. SGOV earns T-bill yield."""
    sub = ind[ind.index >= start].copy()
    nav = 1.0
    cur_soxx = cur_usd = cur_sgov = 0.0
    records = []

    for i, (date, row) in enumerate(sub.iterrows()):
        soxx_w, usd_w, sgov_w, tier, regime, score = compute_signal_3t(row, cfg)

        if i == 0:
            cur_soxx, cur_usd, cur_sgov = soxx_w, usd_w, sgov_w
            records.append({"date": date, "nav": nav, "tier": tier,
                             "regime": regime, "score": score,
                             "eff_lev": usd_w * 2 + soxx_w * 1,
                             "soxx_w": cur_soxx, "usd_w": cur_usd,
                             "sgov_w": cur_sgov})
            continue

        prev = sub.iloc[i - 1]
        def ret(col):
            pv = prev.get(col, np.nan); cv = row.get(col, pv)
            return (cv / pv - 1) if (pv and pv > 0) else 0.0

        irx = row.get("irx_daily", 0.0) or 0.0
        nav *= (1 + cur_soxx * ret("soxx") + cur_usd * ret("usd") +
                cur_sgov * float(irx))

        cur_soxx, cur_usd, cur_sgov = soxx_w, usd_w, sgov_w
        records.append({"date": date, "nav": nav, "tier": tier,
                         "regime": regime, "score": score,
                         "eff_lev": usd_w * 2 + soxx_w * 1,
                         "soxx_w": cur_soxx, "usd_w": cur_usd,
                         "sgov_w": cur_sgov})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks_3t(bt, sub)
    return bt


def run_backtest_realistic_3t(ind: pd.DataFrame, cfg: dict,
                               start: pd.Timestamp) -> pd.DataFrame:
    """Realistic 3T: confirmation + slippage. SGOV earns T-bill yield."""
    sub  = ind[ind.index >= start].copy()
    slip = cfg.get("slip_bps", 8.0) / 10_000.0
    conf_en = cfg.get("confirm_enter", 2)
    conf_ex = cfg.get("confirm_exit",  1)

    nav = 1.0
    cur_soxx = cur_usd = cur_sgov = 0.0
    sig_buf = []   # rolling tier history

    _tier_rank = {"sgov": 0, "soxx": 1, "usd": 2}

    records = []
    for i, (date, row) in enumerate(sub.iterrows()):
        soxx_w, usd_w, sgov_w, tier, regime, score = compute_signal_3t(row, cfg)

        if i == 0:
            cur_soxx, cur_usd, cur_sgov = soxx_w, usd_w, sgov_w
            sig_buf.append(tier)
            records.append({"date": date, "nav": nav, "tier": tier,
                             "regime": regime, "score": score,
                             "eff_lev": usd_w * 2 + soxx_w * 1,
                             "soxx_w": cur_soxx, "usd_w": cur_usd,
                             "sgov_w": cur_sgov})
            continue

        # Daily P&L
        prev = sub.iloc[i - 1]
        def ret(col):
            pv = prev.get(col, np.nan); cv = row.get(col, pv)
            return (cv / pv - 1) if (pv and pv > 0) else 0.0

        irx = row.get("irx_daily", 0.0) or 0.0
        nav *= (1 + cur_soxx * ret("soxx") + cur_usd * ret("usd") +
                cur_sgov * float(irx))

        sig_buf.append(tier)
        if len(sig_buf) > max(conf_en, conf_ex) + 1:
            sig_buf.pop(0)

        cur_tier = "usd" if cur_usd > 0.5 else ("soxx" if cur_soxx > 0.5 else "sgov")
        new_tier  = cur_tier

        # Immediate SGOV on extreme CB (BEAR regime or extreme DD)
        if tier == "sgov" and sgov_w == 1.0 and cur_tier != "sgov":
            new_tier = "sgov"
        elif date.weekday() == 0:  # Monday rebalance
            tgt_rank = _tier_rank[tier]
            cur_rank = _tier_rank[cur_tier]
            is_up    = tgt_rank > cur_rank
            req      = conf_en if is_up else conf_ex
            if len(sig_buf) >= req:
                recent_ranks = [_tier_rank[t] for t in sig_buf[-req:]]
                if is_up  and all(r >= tgt_rank for r in recent_ranks):
                    new_tier = tier
                elif not is_up and all(r <= tgt_rank for r in recent_ranks):
                    new_tier = tier

        if new_tier != cur_tier:
            # Slippage: one-way on traded fraction
            nav *= (1 - slip)

        cur_soxx = 1.0 if new_tier == "soxx" else 0.0
        cur_usd  = 1.0 if new_tier == "usd"  else 0.0
        cur_sgov = 1.0 if new_tier == "sgov" else 0.0

        records.append({"date": date, "nav": nav, "tier": new_tier,
                         "regime": regime, "score": score,
                         "eff_lev": cur_usd * 2 + cur_soxx * 1,
                         "soxx_w": cur_soxx, "usd_w": cur_usd,
                         "sgov_w": cur_sgov})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks_3t(bt, sub)
    return bt


def _attach_benchmarks_3t(bt: pd.DataFrame, sub: pd.DataFrame) -> None:
    for col, asset in [("soxx_nav", "soxx"), ("usd_nav", "usd"), ("soxl_nav", "soxl")]:
        s = sub[asset].dropna()
        if len(s):
            bt[col] = (sub[asset] / s.iloc[0]).reindex(bt.index)
    # SGOV buy-and-hold: compound daily IRX yield
    irx = sub["irx_daily"].fillna(0)
    bt["sgov_nav"] = (1 + irx).cumprod().reindex(bt.index)
    bt["sgov_nav"] /= bt["sgov_nav"].iloc[0]


def run_period_3t(ind: pd.DataFrame, start: pd.Timestamp,
                  label: str, do_plot: bool, initial: float = 100_000):
    print(f"\n{'═'*72}")
    print(f"  NOVA-3T Strategy — {label}")
    print(f"  {start.date()} → {ind.index[-1].date()}  |  initial: ${initial:,}")
    print(f"  (regime estimated from NVDA+MU 6m price momentum)")
    print(f"{'═'*72}")

    print("⚙️   Running ideal simulation…")
    bt_i = run_backtest_ideal_3t(ind, BT_3T_CONFIG, start)
    print("⚙️   Running realistic simulation…")
    bt_r = run_backtest_realistic_3t(ind, BT_3T_CONFIG, start)

    results = [
        metrics_final(bt_i["nav"],       f"NOVA-3T {label} (ideal)",     initial),
        metrics_final(bt_r["nav"],        f"NOVA-3T {label} (realistic)", initial),
        metrics_final(bt_i["soxx_nav"],  "SOXX B&H (1×)",               initial),
        metrics_final(bt_i["usd_nav"],   "USD B&H  (2×)",               initial),
        metrics_final(bt_i["soxl_nav"],  "SOXL B&H (3×)",               initial),
        metrics_final(bt_i["sgov_nav"],  "SGOV B&H (T-bill)",           initial),
    ]
    print_summary_table(results, initial)

    # Year-by-year
    yi  = yearly_stats(bt_i["nav"])
    yr  = yearly_stats(bt_r["nav"])
    ys  = yearly_stats(bt_i["soxx_nav"].dropna())
    yu  = yearly_stats(bt_i["usd_nav"].dropna())
    current_year = datetime.now().year

    print(f"\n{'═'*112}")
    print(f"  Year-by-Year — NOVA-3T {label}")
    print(f"{'═'*112}")
    print(f"  {'Year':<6} {'3T ideal':>9} {'3T real':>9} {'SOXX':>8} {'USD 2×':>8}  "
          f"{'DD ideal':>9} {'DD real':>9}  {'Avg Lev':>8}  {'Regime %':>18}")
    print("  " + "─" * 109)
    for y in sorted(set(yi.index) | set(yr.index)):
        tag = " *" if y == current_year else "  "
        def f(df, col="ret"):
            v = df[col].get(y, float("nan")) if len(df) else float("nan")
            return f"{v:>+7.1%}" if not np.isnan(v) else f"{'—':>7}"

        yr_sub  = bt_i[bt_i.index.year == y]
        avg_lev = f"{yr_sub['eff_lev'].mean():.2f}×" if len(yr_sub) else "—"

        reg_counts = yr_sub["regime"].value_counts() if len(yr_sub) else {}
        total_days = max(len(yr_sub), 1)
        reg_str = "  ".join(f"{r[0]}:{rc/total_days:.0%}"
                            for r, rc in zip(reg_counts.items(), reg_counts.values))

        print(f"  {y}{tag:<3} {f(yi):>9} {f(yr):>9} {f(ys):>8} {f(yu):>8}  "
              f"{f(yi,'max_dd'):>9} {f(yr,'max_dd'):>9}  {avg_lev:>8}  {reg_str}")

    print("  " + "─" * 109)
    print("  * = YTD")

    gap_analysis(bt_i, bt_r)
    print_bear_periods(bt_i, bt_r)

    if do_plot:
        import os
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        import matplotlib.patches as mpatches

        fig, axes = plt.subplots(3, 1, figsize=(15, 13),
                                 gridspec_kw={"height_ratios": [3, 1.5, 1]})
        fig.suptitle(f"NOVA-3T Strategy — {label}  (SGOV / SOXX / USD)",
                     fontsize=14, fontweight="bold")

        # Panel 1: cumulative return
        ax = axes[0]
        ax.semilogy(bt_i.index, bt_i["nav"],     label="NOVA-3T (ideal)",
                    color="#E91E63", lw=2.5, zorder=7)
        ax.semilogy(bt_r.index, bt_r["nav"],      label="NOVA-3T (realistic)",
                    color="#C2185B", lw=1.8, ls="--", zorder=6)
        ax.semilogy(bt_i.index, bt_i["soxx_nav"], label="SOXX B&H (1×)",
                    color="#2E7D32", lw=1.5, ls="--")
        ax.semilogy(bt_i.index, bt_i["usd_nav"],  label="USD B&H (2×)",
                    color="#7B1FA2", lw=1.5, ls="-.")
        ax.semilogy(bt_i.index, bt_i["soxl_nav"], label="SOXL B&H (3×)",
                    color="#C62828", lw=1.2, ls=":", alpha=0.7)
        ax.semilogy(bt_i.index, bt_i["sgov_nav"], label="SGOV B&H (T-bill)",
                    color="#78909C", lw=1.0, ls=":", alpha=0.8)

        # Shade BEAR regime periods (pink background)
        bear_mask = bt_i["regime"] == "BEAR"
        for start_d, end_d in _regime_spans(bt_i.index, bear_mask):
            ax.axvspan(start_d, end_d, alpha=0.12, color="red")

        for yr_l in range(bt_i.index[0].year, bt_i.index[-1].year + 2):
            ax.axvline(pd.Timestamp(f"{yr_l}-01-01"), color="gray", alpha=0.15, lw=0.8)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}×"))
        ax.set_ylabel("Portfolio Growth (log)")
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(True, which="both", alpha=0.2)
        ax.set_title("Cumulative Return — Log Scale  (red shading = BEAR regime)", fontsize=11)

        # Panel 2: tier bands
        ax2 = axes[1]
        colors = {"usd": "#7B1FA2", "soxx": "#2E7D32", "sgov": "#78909C"}
        for tier_name, color in colors.items():
            mask_t = bt_i["tier"] == tier_name
            ax2.fill_between(bt_i.index,
                             bt_i["eff_lev"].where(mask_t, np.nan),
                             0, alpha=0.75, color=color, label=tier_name.upper())
        ax2.set_ylim(-0.1, 2.3)
        ax2.set_yticks([0, 1, 2])
        ax2.set_yticklabels(["0× SGOV", "1× SOXX", "2× USD"])
        ax2.set_ylabel("Effective Leverage")
        ax2.legend(loc="upper left", fontsize=9)
        ax2.grid(True, alpha=0.2)
        ax2.set_title("Tier Allocation Over Time", fontsize=11)
        for yr_l in range(bt_i.index[0].year, bt_i.index[-1].year + 2):
            ax2.axvline(pd.Timestamp(f"{yr_l}-01-01"), color="gray", alpha=0.15, lw=0.8)

        # Panel 3: score + regime
        ax3 = axes[2]
        ax3.fill_between(bt_i.index, bt_i["score"].clip(-22, 22), 0,
                         where=bt_i["score"] >= 0, alpha=0.6, color="#4CAF50")
        ax3.fill_between(bt_i.index, bt_i["score"].clip(-22, 22), 0,
                         where=bt_i["score"] < 0,  alpha=0.6, color="#F44336")
        ax3.axhline(0, color="black", lw=0.8)
        ax3.axhline( 5, color="#7B1FA2", lw=0.6, ls="--", alpha=0.7)   # USD threshold
        ax3.axhline(-2, color="#2E7D32", lw=0.6, ls="--", alpha=0.7)   # SOXX threshold
        ax3.set_ylabel("Signal Score")
        ax3.grid(True, alpha=0.2)
        ax3.set_title("Signal Score  (purple dash=USD gate, green dash=SOXX gate)", fontsize=11)
        for yr_l in range(bt_i.index[0].year, bt_i.index[-1].year + 2):
            ax3.axvline(pd.Timestamp(f"{yr_l}-01-01"), color="gray", alpha=0.15, lw=0.8)

        plt.tight_layout()
        out_dir  = os.path.dirname(os.path.abspath(__file__))
        slug     = label.lower().replace("-","").replace(" ","_")
        out_path = os.path.join(out_dir, f"backtest_nova_3t_{slug}.png")
        plt.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"\n  📈  Chart saved → {out_path}")
        plt.show()

    return bt_i, bt_r, results


def _regime_spans(idx, mask):
    """Yield (start, end) date pairs for contiguous True runs in mask."""
    spans = []; in_span = False; s = None
    for d, v in zip(idx, mask):
        if v and not in_span:
            s = d; in_span = True
        elif not v and in_span:
            spans.append((s, d)); in_span = False
    if in_span:
        spans.append((s, idx[-1]))
    return spans


# ══════════════════════════════════════════════════════════════════
#  METRICS
# ══════════════════════════════════════════════════════════════════

def metrics(nav: pd.Series, label: str) -> dict:
    nav = nav.dropna()
    if len(nav) < 5:
        return {"label": label, "cagr": 0.0, "ann_vol": 0.0,
                "sharpe": 0.0, "max_dd": 0.0}
    days  = (nav.index[-1] - nav.index[0]).days
    years = max(days / 365.25, 0.01)
    cagr  = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    rets  = nav.pct_change().dropna()
    vol   = rets.std() * np.sqrt(252)
    sr    = (cagr - 0.02) / vol if vol > 0 else 0.0
    dd    = ((nav / nav.cummax()) - 1).min()
    return {"label": label, "cagr": cagr, "ann_vol": vol,
            "sharpe": sr, "max_dd": dd}


def yearly_stats(nav: pd.Series) -> pd.DataFrame:
    rows = []
    for yr in range(nav.index[0].year, nav.index[-1].year + 1):
        s = nav[nav.index.year == yr]
        if len(s) < 5:
            continue
        ret = s.iloc[-1] / s.iloc[0] - 1
        dd  = ((s / s.cummax()) - 1).min()
        rows.append({"year": yr, "ret": ret, "max_dd": dd})
    return pd.DataFrame(rows).set_index("year")


# ══════════════════════════════════════════════════════════════════
#  IDEAL BACKTEST  (no slippage, no confirmation delay)
# ══════════════════════════════════════════════════════════════════

def run_backtest_ideal(ind: pd.DataFrame, cfg: dict,
                       start: pd.Timestamp) -> pd.DataFrame:
    """Ideal simulation: signal fires → allocation changes same day close."""
    sub = ind[ind.index >= start].copy()

    nav = 1.0
    cur_soxx = cur_usd = cur_soxl = cur_cash = 0.0

    records = []
    for i, (date, row) in enumerate(sub.iterrows()):
        target_soxx, target_usd, target_soxl, target_cash, tier, score = \
            compute_signal(row, cfg)

        if i == 0:
            cur_soxx, cur_usd, cur_soxl, cur_cash = \
                target_soxx, target_usd, target_soxl, target_cash
            records.append({"date": date, "nav": nav, "tier": tier,
                             "score": score, "eff_lev": TIER_EFF_LEV[tier],
                             "soxx_w": cur_soxx, "usd_w": cur_usd,
                             "soxl_w": cur_soxl, "cash_w": cur_cash})
            continue

        prev = sub.iloc[i - 1]
        pr_soxx = prev.get("soxx",  np.nan)
        pr_usd  = prev.get("usd",   np.nan)
        pr_soxl = prev.get("soxl",  np.nan)
        cr_soxx = row.get("soxx",   pr_soxx)
        cr_usd  = row.get("usd",    pr_usd)
        cr_soxl = row.get("soxl",   pr_soxl)

        def ret(now, prev_v):
            return (now / prev_v - 1) if (prev_v and prev_v > 0 and now and now > 0) else 0.0

        r_soxx = ret(cr_soxx, pr_soxx)
        r_usd  = ret(cr_usd,  pr_usd)
        r_soxl = ret(cr_soxl, pr_soxl)

        nav *= (1 + cur_soxx * r_soxx + cur_usd * r_usd +
                cur_soxl * r_soxl + cur_cash * 0.0)

        cur_soxx, cur_usd, cur_soxl, cur_cash = \
            target_soxx, target_usd, target_soxl, target_cash

        records.append({"date": date, "nav": nav, "tier": tier,
                         "score": score, "eff_lev": TIER_EFF_LEV[tier],
                         "soxx_w": cur_soxx, "usd_w": cur_usd,
                         "soxl_w": cur_soxl, "cash_w": cur_cash})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks(bt, sub)
    return bt


# ══════════════════════════════════════════════════════════════════
#  REALISTIC BACKTEST  (confirmation delay + slippage)
# ══════════════════════════════════════════════════════════════════

def run_backtest_realistic(ind: pd.DataFrame, cfg: dict,
                           start: pd.Timestamp) -> pd.DataFrame:
    """
    Realistic simulation:
      - Entries (upgrade leverage): confirm_enter consecutive days
      - Exits (downgrade / cash):   confirm_exit  consecutive days
      - Cash trigger (extreme CB):  immediate
      - Slippage: slip_bps one-way on changed fraction
      - Weekly rebalance on Mondays; daily trailing-stop check
    """
    sub  = ind[ind.index >= start].copy()
    slip = cfg.get("slip_bps", 8.0) / 10_000.0
    conf_en = cfg.get("confirm_enter", 2)
    conf_ex = cfg.get("confirm_exit",  1)

    nav = 1.0
    cur_soxx, cur_usd, cur_soxl, cur_cash = 0.0, 0.0, 0.0, 0.0
    sig_buf = []  # rolling signal buffer

    records = []
    for i, (date, row) in enumerate(sub.iterrows()):
        tgt_soxx, tgt_usd, tgt_soxl, tgt_cash, tier, score = \
            compute_signal(row, cfg)

        if i == 0:
            cur_soxx, cur_usd, cur_soxl, cur_cash = \
                tgt_soxx, tgt_usd, tgt_soxl, tgt_cash
            sig_buf.append(tier)
            records.append({"date": date, "nav": nav, "tier": tier,
                             "score": score, "eff_lev": TIER_EFF_LEV[tier],
                             "soxx_w": cur_soxx, "usd_w": cur_usd,
                             "soxl_w": cur_soxl, "cash_w": cur_cash})
            continue

        # Daily P&L
        prev = sub.iloc[i - 1]
        def ret(col):
            pv = prev.get(col, np.nan); cv = row.get(col, pv)
            return (cv / pv - 1) if (pv and pv > 0) else 0.0

        nav *= (1 + cur_soxx * ret("soxx") + cur_usd * ret("usd") +
                cur_soxl * ret("soxl") + cur_cash * 0.0)

        sig_buf.append(tier)
        if len(sig_buf) > max(conf_en, conf_ex) + 1:
            sig_buf.pop(0)

        new_soxx, new_usd, new_soxl, new_cash = \
            cur_soxx, cur_usd, cur_soxl, cur_cash
        cur_tier_idx = TIER_ORDER.index(
            _current_tier(cur_soxx, cur_usd, cur_soxl, cur_cash))
        tgt_tier_idx = TIER_ORDER.index(tier)

        # Immediate cash on extreme CB
        if tier == "cash" and tgt_cash == 1.0 and cur_cash < 1.0:
            new_soxx, new_usd, new_soxl, new_cash = 0.0, 0.0, 0.0, 1.0
        elif date.weekday() == 0:   # Monday rebalance
            is_upgrade = tgt_tier_idx > cur_tier_idx
            req_days   = conf_en if is_upgrade else conf_ex
            if len(sig_buf) >= req_days:
                recent = sig_buf[-req_days:]
                # All recent signals at same or consistent direction
                recent_idx = [TIER_ORDER.index(t) for t in recent]
                if is_upgrade:
                    if all(ri >= tgt_tier_idx for ri in recent_idx):
                        new_soxx, new_usd, new_soxl, new_cash = \
                            tgt_soxx, tgt_usd, tgt_soxl, tgt_cash
                else:
                    if all(ri <= tgt_tier_idx for ri in recent_idx):
                        new_soxx, new_usd, new_soxl, new_cash = \
                            tgt_soxx, tgt_usd, tgt_soxl, tgt_cash

        # Slippage on changed fraction
        changed = (abs(new_soxx - cur_soxx) + abs(new_usd - cur_usd) +
                   abs(new_soxl - cur_soxl) + abs(new_cash - cur_cash)) / 2.0
        if changed > 0.01:
            nav *= (1 - slip * changed)

        cur_soxx, cur_usd, cur_soxl, cur_cash = \
            new_soxx, new_usd, new_soxl, new_cash
        new_tier = _current_tier(cur_soxx, cur_usd, cur_soxl, cur_cash)

        records.append({"date": date, "nav": nav, "tier": new_tier,
                         "score": score, "eff_lev": TIER_EFF_LEV[new_tier],
                         "soxx_w": cur_soxx, "usd_w": cur_usd,
                         "soxl_w": cur_soxl, "cash_w": cur_cash})

    bt = pd.DataFrame(records).set_index("date")
    _attach_benchmarks(bt, sub)
    return bt


def _current_tier(soxx_w, usd_w, soxl_w, cash_w) -> str:
    """Infer tier from weights (best-match)."""
    weights = (soxx_w, usd_w, soxl_w, cash_w)
    best = min(TIER_ALLOC,
               key=lambda t: sum(abs(a - b) for a, b in zip(TIER_ALLOC[t], weights)))
    return best


def _attach_benchmarks(bt: pd.DataFrame, sub: pd.DataFrame) -> None:
    """Attach benchmark buy-and-hold NAVs to the backtest DataFrame."""
    for col, asset in [("soxx_nav", "soxx"), ("usd_nav", "usd"),
                       ("soxl_nav", "soxl")]:
        s = sub[asset].dropna()
        if len(s):
            bt[col] = (sub[asset] / s.iloc[0]).reindex(bt.index)

    # 50/50 SOXX/SOXL static rebalance
    soxx_s = sub["soxx"].dropna()
    soxl_s = sub["soxl"].dropna()
    if len(soxx_s) and len(soxl_s):
        soxx_r = (sub["soxx"] / soxx_s.iloc[0]).reindex(bt.index)
        soxl_r = (sub["soxl"] / soxl_s.iloc[0]).reindex(bt.index)
        bt["half_nav"] = 0.5 * soxx_r + 0.5 * soxl_r


# ══════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════

def metrics_final(nav: pd.Series, label: str, initial: float = 100_000) -> dict:
    m = metrics(nav, label)
    m["final_value"] = initial * nav.iloc[-1] / nav.iloc[0]
    return m


def print_summary_table(results: list, initial: float = 100_000) -> None:
    print(f"\n{'─'*82}")
    print(f"  {'Strategy':<30} {'CAGR':>8} {'Ann.Vol':>8} {'Sharpe':>7} {'MaxDD':>8}  {'$100K→':>10}")
    print(f"  {'─'*28}  {'─'*7}  {'─'*7}  {'─'*6}  {'─'*7}  {'─'*10}")
    for r in results:
        fv = r.get("final_value", 0)
        fv_str = f"${fv/1_000_000:.2f}M" if fv >= 1_000_000 else f"${fv/1_000:.0f}K"
        star = " ◀" if "realistic" in r["label"].lower() else ""
        print(f"  {r['label']:<30} {r['cagr']:>+7.1%}  {r['ann_vol']:>7.1%}  "
              f"{r['sharpe']:>6.2f}  {r['max_dd']:>7.1%}  {fv_str:>10}{star}")
    print(f"  {'─'*28}  {'─'*7}  {'─'*7}  {'─'*6}  {'─'*7}  {'─'*10}")


def print_yearly_table(bt_ideal: pd.DataFrame, bt_real: pd.DataFrame,
                       period_label: str) -> None:
    yi = yearly_stats(bt_ideal["nav"])
    yr = yearly_stats(bt_real["nav"])
    ys = yearly_stats(bt_ideal["soxx_nav"].dropna()) if "soxx_nav" in bt_ideal else pd.DataFrame()
    yu = yearly_stats(bt_ideal["usd_nav"].dropna())  if "usd_nav"  in bt_ideal else pd.DataFrame()
    yl = yearly_stats(bt_ideal["soxl_nav"].dropna()) if "soxl_nav" in bt_ideal else pd.DataFrame()
    current_year = datetime.now().year

    print(f"\n{'═'*108}")
    print(f"  Year-by-Year Returns — {period_label}")
    print(f"{'═'*108}")
    hdr = (f"  {'Year':<6} {'NOVA ideal':>10} {'NOVA real':>10} "
           f"{'SOXX':>8} {'USD 2×':>8} {'SOXL 3×':>9}  "
           f"{'Ideal DD':>9} {'Real DD':>9}  {'Avg Lev':>8}")
    print(hdr)
    print("  " + "─" * 106)

    all_years = sorted(set(yi.index) | set(yr.index))
    for y in all_years:
        tag = " *" if y == current_year else "  "
        def f(df, col="ret"):
            v = df[col].get(y, float("nan")) if len(df) else float("nan")
            return f"{v:>+8.1%}" if not np.isnan(v) else f"{'—':>8}"
        ri  = f(yi);  rr  = f(yr)
        rs  = f(ys);  ru  = f(yu);  rl  = f(yl)
        ddi = f(yi, "max_dd"); ddr = f(yr, "max_dd")
        # average effective leverage for this year
        yr_lev = bt_ideal[bt_ideal.index.year == y]["eff_lev"]
        avg_lev = f"{yr_lev.mean():.2f}×" if len(yr_lev) else "—"
        print(f"  {y}{tag:<3} {ri:>10} {rr:>10} {rs:>8} {ru:>8} {rl:>9}  "
              f"{ddi:>9} {ddr:>9}  {avg_lev:>8}")

    print("  " + "─" * 106)
    print(f"  * = YTD (partial year)")


def gap_analysis(bt_ideal: pd.DataFrame, bt_real: pd.DataFrame) -> None:
    mi = metrics(bt_ideal["nav"], "Ideal")
    mr = metrics(bt_real["nav"],  "Realistic")

    print(f"\n{'═'*70}")
    print("  Gap Analysis: Ideal vs Realistic")
    print("  (2-day entry confirm | 1-day exit | 8 bps slippage/trade)")
    print(f"{'═'*70}")
    print(f"  {'Metric':<20} {'Ideal':>10} {'Realistic':>10} {'Gap':>10}")
    print(f"  {'─'*18}  {'─'*9}  {'─'*9}  {'─'*9}")
    for name, key, fmt_s in [("CAGR", "cagr", ".1%"), ("Ann. Vol", "ann_vol", ".1%"),
                               ("Sharpe", "sharpe", ".2f"), ("Max DD", "max_dd", ".1%")]:
        vi = mi[key]; vr = mr[key]; gap = vr - vi
        fmt = f"{{:{fmt_s}}}"
        print(f"  {name:<20} {fmt.format(vi):>10} {fmt.format(vr):>10} {fmt.format(gap):>10}")
    print(f"{'═'*70}")


def print_bear_periods(bt_ideal: pd.DataFrame, bt_real: pd.DataFrame) -> None:
    """Performance during the worst semiconductor crashes."""
    periods = [
        ("COVID crash",       "2020-02-20", "2020-03-23"),
        ("2022 semi bear",    "2021-11-19", "2022-10-13"),
        ("2025 tariff shock", "2025-02-19", "2025-04-08"),
    ]
    print(f"\n{'─'*72}")
    print("  Performance During Semiconductor Bear Events")
    print(f"{'─'*72}")
    for name, s, e in periods:
        mask_i = (bt_ideal.index >= s) & (bt_ideal.index <= e)
        mask_r = (bt_real.index  >= s) & (bt_real.index  <= e)
        if mask_i.sum() < 3:
            continue
        def pr(nav, m):
            sub = nav[m].dropna()
            return sub.iloc[-1] / sub.iloc[0] - 1 if len(sub) else float("nan")
        ri = pr(bt_ideal["nav"],      mask_i)
        rr = pr(bt_real["nav"],       mask_r)
        rs = pr(bt_ideal.get("soxx_nav", pd.Series()), mask_i)
        rl = pr(bt_ideal.get("soxl_nav", pd.Series()), mask_i)
        print(f"\n  {name}  ({s} → {e})")
        print(f"    NOVA ideal: {ri:+.1%}   NOVA real: {rr:+.1%}   "
              f"SOXX B&H: {rs:+.1%}   SOXL B&H: {rl:+.1%}")


# ══════════════════════════════════════════════════════════════════
#  PLOT
# ══════════════════════════════════════════════════════════════════

def plot_nova(bt_ideal: pd.DataFrame, bt_real: pd.DataFrame,
              period_label: str, out_path: str) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        print("matplotlib not available — skip plot")
        return

    fig, axes = plt.subplots(3, 1, figsize=(15, 13),
                             gridspec_kw={"height_ratios": [3, 1.5, 1]})
    fig.suptitle(f"NOVA Strategy v1.0 — {period_label}",
                 fontsize=14, fontweight="bold")

    # Panel 1: Log-scale NAV
    ax = axes[0]
    ax.semilogy(bt_ideal.index, bt_ideal["nav"],
                label="NOVA (ideal)", color="#E91E63", lw=2.5, zorder=7)
    ax.semilogy(bt_real.index,  bt_real["nav"],
                label="NOVA (realistic)", color="#C2185B", lw=1.8, ls="--", zorder=6)
    if "soxx_nav" in bt_ideal:
        ax.semilogy(bt_ideal.index, bt_ideal["soxx_nav"],
                    label="SOXX B&H (1×)", color="#2E7D32", lw=1.5, ls="--")
    if "usd_nav" in bt_ideal:
        ax.semilogy(bt_ideal.index, bt_ideal["usd_nav"],
                    label="USD B&H (2×)", color="#7B1FA2", lw=1.4, ls="-.")
    if "soxl_nav" in bt_ideal:
        ax.semilogy(bt_ideal.index, bt_ideal["soxl_nav"],
                    label="SOXL B&H (3×)", color="#C62828", lw=1.2, ls=":", alpha=0.7)
    if "half_nav" in bt_ideal:
        ax.semilogy(bt_ideal.index, bt_ideal["half_nav"],
                    label="50/50 SOXX/SOXL", color="#F57F17", lw=1.2, ls=":", alpha=0.8)
    for yr in range(bt_ideal.index[0].year, bt_ideal.index[-1].year + 2):
        ax.axvline(pd.Timestamp(f"{yr}-01-01"), color="gray", alpha=0.15, lw=0.8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}×"))
    ax.set_ylabel("Portfolio Growth (log, start = 1×)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, which="both", alpha=0.2)
    ax.set_title("Cumulative Return — Log Scale", fontsize=11)

    # Panel 2: Effective leverage over time
    ax2 = axes[1]
    ax2.fill_between(bt_ideal.index, bt_ideal["eff_lev"],
                     alpha=0.7, color="#E91E63", label="NOVA ideal")
    ax2.fill_between(bt_real.index, bt_real["eff_lev"],
                     alpha=0.4, color="#7B1FA2", label="NOVA realistic")
    ax2.set_ylim(-0.1, 3.3)
    ax2.set_yticks([0, 1, 1.5, 2, 2.5, 3])
    ax2.set_yticklabels(["0× Cash", "1× SOXX", "1.5×", "2× USD", "2.5×", "3× SOXL"])
    ax2.set_ylabel("Effective Leverage")
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.2)
    for yr in range(bt_ideal.index[0].year, bt_ideal.index[-1].year + 2):
        ax2.axvline(pd.Timestamp(f"{yr}-01-01"), color="gray", alpha=0.15, lw=0.8)
    ax2.set_title("Effective Leverage Over Time", fontsize=11)

    # Panel 3: Score heat
    ax3 = axes[2]
    ax3.fill_between(bt_ideal.index, bt_ideal["score"].clip(-22, 22),
                     0, where=bt_ideal["score"] >= 0,
                     alpha=0.7, color="#4CAF50", label="Bullish score")
    ax3.fill_between(bt_ideal.index, bt_ideal["score"].clip(-22, 22),
                     0, where=bt_ideal["score"] < 0,
                     alpha=0.7, color="#F44336", label="Bearish score")
    ax3.axhline(0, color="black", lw=0.8)
    ax3.axhline(16, color="#E91E63", lw=0.5, ls="--", alpha=0.6)   # SOXL threshold
    ax3.axhline(-8, color="#F44336", lw=0.5, ls="--", alpha=0.6)   # Cash threshold
    ax3.set_ylabel("Signal Score")
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(True, alpha=0.2)
    for yr in range(bt_ideal.index[0].year, bt_ideal.index[-1].year + 2):
        ax3.axvline(pd.Timestamp(f"{yr}-01-01"), color="gray", alpha=0.15, lw=0.8)
    ax3.set_title("Layer 2 Signal Score", fontsize=11)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  📈  Chart saved → {out_path}")
    plt.show()


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def run_period(ind: pd.DataFrame, start: pd.Timestamp,
               label: str, do_plot: bool, initial: float = 100_000):
    print(f"\n{'═'*72}")
    print(f"  NOVA Strategy — {label}")
    print(f"  {start.date()} → {ind.index[-1].date()}  |  "
          f"{(ind.index[-1] - start).days // 365} years  |  initial: ${initial:,}")
    print(f"{'═'*72}")

    # Ideal
    print(f"\n⚙️   Running ideal simulation…")
    bt_ideal = run_backtest_ideal(ind, BT_CONFIG, start)

    # Realistic
    print(f"⚙️   Running realistic simulation (confirm + slippage)…")
    bt_real  = run_backtest_realistic(ind, BT_CONFIG, start)

    # Metrics
    results = [
        metrics_final(bt_ideal["nav"],       f"NOVA {label} (ideal)",     initial),
        metrics_final(bt_real["nav"],        f"NOVA {label} (realistic)", initial),
        metrics_final(bt_ideal["soxx_nav"],  "SOXX B&H (1×)",            initial),
        metrics_final(bt_ideal["usd_nav"],   "USD B&H  (2×)",            initial),
        metrics_final(bt_ideal["soxl_nav"],  "SOXL B&H (3×)",            initial),
        metrics_final(bt_ideal["half_nav"],  "50/50 SOXX/SOXL",          initial),
    ]

    print_summary_table(results, initial)
    print_yearly_table(bt_ideal, bt_real, label)
    gap_analysis(bt_ideal, bt_real)
    print_bear_periods(bt_ideal, bt_real)

    if do_plot:
        import os
        out_dir = os.path.dirname(os.path.abspath(__file__))
        period_slug = label.lower().replace("-", "").replace(" ", "_")
        out_path = os.path.join(out_dir, f"backtest_nova_{period_slug}.png")
        plot_nova(bt_ideal, bt_real, label, out_path)

    return bt_ideal, bt_real, results


def main():
    args     = sys.argv[1:]
    do_plot  = "--plot"    in args
    run_3t   = "--3tier"   in args   # run NOVA-3T only
    run_usd  = "--usd-cash" in args  # run NOVA-USD only
    run_all  = not run_3t and not run_usd
    end      = datetime.now().strftime("%Y-%m-%d")

    # 1. Download full history
    data = load_data(end)

    # 2. Build indicator matrix
    print("\n⚙️   Computing full indicator matrix…")
    ind = build_indicators(data)
    print(f"   {len(ind)} trading days  ({ind.index[0].date()} → {ind.index[-1].date()})")

    start_5y  = pd.Timestamp("2021-01-04")
    start_10y = pd.Timestamp("2016-01-04")

    res5 = res10 = ures5 = ures10 = t5r = t10r = None

    if run_all:
        # NOVA 4-tier
        _, _, res5  = run_period(ind, start_5y,  "5-Year (2021–2026)",  do_plot)
        _, _, res10 = run_period(ind, start_10y, "10-Year (2016–2026)", do_plot)

    if run_all or run_usd:
        # NOVA-USD 2-asset
        _, _, ures5  = run_period_usd_cash(ind, start_5y,  "5-Year (2021–2026)",  do_plot)
        _, _, ures10 = run_period_usd_cash(ind, start_10y, "10-Year (2016–2026)", do_plot)

    if run_all or run_3t:
        # NOVA-3T  (SGOV / SOXX / USD)
        _, _, t5r  = run_period_3t(ind, start_5y,  "5-Year (2021–2026)",  do_plot)
        _, _, t10r = run_period_3t(ind, start_10y, "10-Year (2016–2026)", do_plot)

    # ── Final cross-strategy summary ─────────────────────────────
    def summary_row(key5, r5l, key10, r10l, lbl):
        if r5l is None or r10l is None:
            return
        r5  = next((r for r in r5l  if key5  in r["label"]), None)
        r10 = next((r for r in r10l if key10 in r["label"]), None)
        if r5 and r10:
            print(f"  {lbl:<36} {r5['cagr']:>+8.1%}  {r10['cagr']:>+9.1%}  "
                  f"{r5['max_dd']:>+9.1%}  {r10['max_dd']:>+9.1%}")

    ref5  = t5r  or ures5  or res5
    print(f"\n{'═'*82}")
    print("  Final Cross-Strategy Summary")
    print(f"{'═'*82}")
    print(f"  {'Strategy':<36} {'5Y CAGR':>9} {'10Y CAGR':>10} {'5Y MaxDD':>10} {'10Y MaxDD':>10}")
    print(f"  {'─'*34}  {'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")
    if res5  and res10:
        summary_row("realistic", res5,  "realistic", res10,  "NOVA 4-tier (realistic)          ")
    if ures5 and ures10:
        summary_row("realistic", ures5, "realistic", ures10, "NOVA-USD    (realistic)          ")
    if t5r   and t10r:
        summary_row("realistic", t5r,   "realistic", t10r,   "NOVA-3T     (realistic) ◀ NEW    ")
    ref5 = t5r or ures5 or res5
    ref10= t10r or ures10 or res10
    if ref5 and ref10:
        summary_row("SOXX", ref5, "SOXX", ref10, "SOXX B&H (1×)                    ")
        summary_row("USD B",ref5, "USD B",ref10, "USD B&H  (2×)                    ")
        summary_row("SOXL", ref5, "SOXL", ref10, "SOXL B&H (3×)                    ")
    if t5r and t10r:
        summary_row("SGOV", t5r, "SGOV", t10r,  "SGOV B&H (T-bill)                ")
    print(f"{'═'*82}\n")

    if not do_plot:
        print("  Flags: --plot  --3tier  --usd-cash\n")


if __name__ == "__main__":
    main()
