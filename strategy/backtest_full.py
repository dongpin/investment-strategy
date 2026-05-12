"""
APEX Strategy — 15-Year Full Historical Backtest
=================================================
Runs APEX v1.0 and v2.0 from TQQQ inception (2010-02-11) through today.
Benchmarks: VOO, QQQ, TQQQ buy-and-hold.

Usage:
    python backtest_full.py
    python backtest_full.py --plot
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

# Import signal + backtest machinery from backtest_ytd
from backtest_ytd import (
    build_indicators,
    compute_signal,
    run_backtest,
    run_backtest_v1,
    metrics,
    CONFIG,
)

BACKTEST_START = pd.Timestamp("2010-03-01")  # ~3 weeks after TQQQ IPO (vol data needed)
HIST_DOWNLOAD  = "2008-06-01"                # enough history for SMA200 + all indicators


# ══════════════════════════════════════════════════════════════════
#  DATA
# ══════════════════════════════════════════════════════════════════

def load_full_data(end: str) -> dict:
    print(f"📡  Downloading full history  {HIST_DOWNLOAD} → {end} …")

    def dl(ticker):
        df = yf.download(ticker, start=HIST_DOWNLOAD, end=end,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise ValueError(f"No data for {ticker}")
        s = df["Close"].squeeze()
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.dropna().rename(ticker)

    data = {
        "QQQ"  : dl("QQQ"),
        "TQQQ" : dl("TQQQ"),
        "VOO"  : dl("VOO"),
        "QLD"  : dl("QLD"),
        "VIX"  : dl("^VIX"),
        "TNX"  : dl("^TNX"),
    }
    for k, v in data.items():
        print(f"    {k:<5}: {len(v)} rows  "
              f"({v.index[0].date()} → {v.index[-1].date()})")
    return data


# ══════════════════════════════════════════════════════════════════
#  YEARLY BREAKDOWN
# ══════════════════════════════════════════════════════════════════

def yearly_stats(nav: pd.Series) -> pd.DataFrame:
    """Return per-calendar-year return and max drawdown."""
    rows = []
    for yr in range(nav.index[0].year, nav.index[-1].year + 1):
        mask = nav.index.year == yr
        s = nav[mask]
        if len(s) < 5:
            continue
        ret = s.iloc[-1] / s.iloc[0] - 1
        dd  = ((s / s.cummax()) - 1).min()
        rows.append({"year": yr, "return": ret, "max_dd": dd, "days": len(s)})
    return pd.DataFrame(rows).set_index("year")


def avg_alloc_by_year(bt: pd.DataFrame) -> pd.Series:
    return bt["tqqq_alloc"].groupby(bt.index.year).mean()


# ══════════════════════════════════════════════════════════════════
#  FULL-PERIOD METRICS
# ══════════════════════════════════════════════════════════════════

def full_metrics(nav: pd.Series, label: str, initial: float = 100_000) -> dict:
    m   = metrics(nav, label)
    m["final_value"] = initial * nav.iloc[-1] / nav.iloc[0]
    return m


# ══════════════════════════════════════════════════════════════════
#  PRINT TABLES
# ══════════════════════════════════════════════════════════════════

def print_summary_table(results: list[dict], initial: float = 100_000) -> None:
    print(f"\n{'─'*80}")
    h = f"  {'Strategy':<28} {'CAGR':>8} {'Ann.Vol':>8} {'Sharpe':>8} {'MaxDD':>8}  {'$100K→'}  "
    print(h)
    print(f"  {'─'*26}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*12}")
    for r in results:
        fv = r.get("final_value", 0)
        if fv >= 1_000_000:
            fv_str = f"${fv/1_000_000:.1f}M"
        else:
            fv_str = f"${fv/1_000:.0f}K"
        star = " ◀" if "v2" in r["label"] else ""
        print(
            f"  {r['label']:<28} "
            f"{r['cagr']:>+7.1%}  "
            f"{r['ann_vol']:>7.1%}  "
            f"{r['sharpe']:>7.2f}  "
            f"{r['max_dd']:>7.1%}  "
            f"{fv_str:>10}"
            f"{star}"
        )
    print(f"  {'─'*26}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*12}")


def print_yearly_table(bt_v2: pd.DataFrame, bt_v1: pd.DataFrame,
                       benchmarks: dict) -> None:
    yr_v2   = yearly_stats(bt_v2["apex_nav"])
    yr_v1   = yearly_stats(bt_v1["apex_nav"])
    yr_voo  = yearly_stats(bt_v2["voo_nav"])
    yr_qqq  = yearly_stats(bt_v2["qqq_nav"])
    yr_tqqq = yearly_stats(bt_v2["tqqq_nav"])
    alloc   = avg_alloc_by_year(bt_v2)

    current_year = datetime.now().year

    print(f"\n{'═'*100}")
    print(f"  Year-by-Year Returns")
    print(f"{'═'*100}")
    hdr = (f"  {'Year':<6} {'APEX v2':>9} {'APEX v1':>9} "
           f"{'VOO':>8} {'QQQ':>8} {'TQQQ':>8}  "
           f"{'v2 MaxDD':>9} {'v1 MaxDD':>9}  {'Avg TQQQ%':>10}")
    print(hdr)
    print("  " + "─" * 97)

    for yr in sorted(set(yr_v2.index) | set(yr_v1.index)):
        tag  = " *" if yr == current_year else "  "
        r_v2 = yr_v2["return"].get(yr,  float("nan"))
        r_v1 = yr_v1["return"].get(yr,  float("nan"))
        r_voo  = yr_voo["return"].get(yr,  float("nan"))
        r_qqq  = yr_qqq["return"].get(yr,  float("nan"))
        r_tqqq = yr_tqqq["return"].get(yr, float("nan"))
        dd_v2  = yr_v2["max_dd"].get(yr,  float("nan"))
        dd_v1  = yr_v1["max_dd"].get(yr,  float("nan"))
        avg_a  = alloc.get(yr, float("nan"))

        def fmt(v, bold=False):
            if np.isnan(v):
                return f"{'—':>8}"
            s = f"{v:>+8.1%}"
            return s

        # Color-code winner between v2 and v1
        winner = "v2" if (not np.isnan(r_v2) and not np.isnan(r_v1)
                          and r_v2 >= r_v1) else "v1"
        marker = ">" if winner == "v2" else " "

        print(
            f"  {yr}{tag:<3} "
            f"{fmt(r_v2):>9}{marker} "
            f"{fmt(r_v1):>9}  "
            f"{fmt(r_voo):>8}  "
            f"{fmt(r_qqq):>8}  "
            f"{fmt(r_tqqq):>8}  "
            f"{dd_v2:>+9.1%}  "
            f"{dd_v1:>+9.1%}  "
            f"{avg_a:>9.0%}"
        )

    print("  " + "─" * 97)
    print(f"  * = YTD (partial year)")

    # Win/loss count
    wins_v2, wins_v1, ties = 0, 0, 0
    for yr in sorted(yr_v2.index):
        if yr not in yr_v1.index:
            continue
        d = yr_v2["return"][yr] - yr_v1["return"][yr]
        if d > 0.001:   wins_v2 += 1
        elif d < -0.001: wins_v1 += 1
        else:            ties    += 1
    print(f"\n  v2 beat v1:  {wins_v2} years   "
          f"v1 beat v2:  {wins_v1} years   ties: {ties}")


# ══════════════════════════════════════════════════════════════════
#  PLOT
# ══════════════════════════════════════════════════════════════════

def plot_full(bt_v2: pd.DataFrame, bt_v1: pd.DataFrame) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        import matplotlib.dates as mdates
    except ImportError:
        print("matplotlib not available — skip plot")
        return

    fig, axes = plt.subplots(3, 1, figsize=(15, 12),
                             gridspec_kw={"height_ratios": [3, 1.5, 1]})
    fig.suptitle("APEX Strategy — 15-Year Full Backtest (2010–2026)",
                 fontsize=15, fontweight="bold")

    # ── Panel 1: Log-scale NAV ──────────────────────────────────
    ax = axes[0]
    ax.semilogy(bt_v2.index, bt_v2["apex_nav"],  label="APEX v2.0",
                color="#1565C0", lw=2.5, zorder=6)
    ax.semilogy(bt_v2.index, bt_v1["apex_nav"],  label="APEX v1.0",
                color="#90CAF9", lw=1.5, ls="--", zorder=5)
    ax.semilogy(bt_v2.index, bt_v2["voo_nav"],   label="VOO",
                color="#2E7D32", lw=1.5, ls="--")
    ax.semilogy(bt_v2.index, bt_v2["qqq_nav"],   label="QQQ",
                color="#7B1FA2", lw=1.5, ls="-.")
    ax.semilogy(bt_v2.index, bt_v2["tqqq_nav"],  label="TQQQ",
                color="#C62828", lw=1.2, ls=":", alpha=0.7)

    # Year grid lines
    for yr in range(2011, 2027):
        ax.axvline(pd.Timestamp(f"{yr}-01-01"), color="gray", alpha=0.2, lw=0.8)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"{x:.0f}×"))
    ax.set_ylabel("Portfolio Growth (log scale, start = 1×)")
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(True, which="both", alpha=0.2)
    ax.set_title("Cumulative Returns — Log Scale", fontsize=11)

    # ── Panel 2: Annual returns bar chart ───────────────────────
    ax2 = axes[1]
    yr_v2   = yearly_stats(bt_v2["apex_nav"])
    yr_v1   = yearly_stats(bt_v1["apex_nav"])
    yr_voo  = yearly_stats(bt_v2["voo_nav"])
    yr_qqq  = yearly_stats(bt_v2["qqq_nav"])

    years   = sorted(yr_v2.index)
    x       = np.arange(len(years))
    w       = 0.22

    ax2.bar(x - 1.5*w, [yr_v2["return"].get(y, 0) for y in years],
            w, label="APEX v2.0", color="#1565C0", alpha=0.85)
    ax2.bar(x - 0.5*w, [yr_v1["return"].get(y, 0) for y in years],
            w, label="APEX v1.0", color="#90CAF9", alpha=0.85)
    ax2.bar(x + 0.5*w, [yr_voo["return"].get(y, 0) for y in years],
            w, label="VOO",       color="#2E7D32", alpha=0.8)
    ax2.bar(x + 1.5*w, [yr_qqq["return"].get(y, 0) for y in years],
            w, label="QQQ",       color="#7B1FA2", alpha=0.8)

    ax2.axhline(0, color="black", lw=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(years, rotation=45, fontsize=8)
    ax2.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
    ax2.set_ylabel("Annual Return")
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, axis="y", alpha=0.3)
    ax2.set_title("Annual Returns", fontsize=11)

    # ── Panel 3: TQQQ allocation heatmap ────────────────────────
    ax3 = axes[2]
    ax3.fill_between(bt_v2.index, bt_v2["tqqq_alloc"],
                     alpha=0.7, color="#1565C0", label="TQQQ % (v2)")
    ax3.fill_between(bt_v2.index, bt_v1["tqqq_alloc"],
                     alpha=0.35, color="#90CAF9", label="TQQQ % (v1)")
    ax3.set_ylim(0, 1.05)
    ax3.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax3.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax3.set_ylabel("TQQQ Weight")
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(True, alpha=0.2)
    for yr in range(2011, 2027):
        ax3.axvline(pd.Timestamp(f"{yr}-01-01"), color="gray", alpha=0.2, lw=0.8)
    ax3.set_title("APEX TQQQ Allocation Over Time", fontsize=11)

    plt.tight_layout()
    out_path = "backtest_full_15yr.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\n  📈 Chart saved → {out_path}")
    plt.show()


# ══════════════════════════════════════════════════════════════════
#  REALISTIC SIMULATION
#  Models the four main sources of backtest inflation:
#    1. Confirmation lag  — 3-day delay on entries, 1-day on exits
#    2. Execution slippage — 0.05% one-way cost per trade
#    3. End-of-day vs 10 AM price gap — modeled as extra 0.03% drag/trade
#    4. VIX-at-open gap — on circuit-breaker days, VIX often worse at open;
#       modeled as 50% of trade-day return foregone on same-day exits
# ══════════════════════════════════════════════════════════════════

def run_realistic(ind: pd.DataFrame, cfg: dict,
                  start: pd.Timestamp,
                  confirm_enter: int = 3,
                  confirm_exit:  int = 1,
                  slip_bps:      float = 8.0) -> pd.DataFrame:
    """
    Realistic simulation with:
      - Asymmetric confirmation: 3 days to enter TQQQ, 1 day to exit
      - Execution slippage: slip_bps one-way per trade (default 8 bps = 0.08%)
      - Weekly rebalance (Mondays) + daily trailing-stop check
    """
    slip = slip_bps / 10_000

    ytd = ind[ind.index >= start].copy()

    nav          = 1.0
    cur_alloc    = 0.0          # currently held TQQQ fraction
    pending_alloc = None        # (target_alloc, days_confirmed)
    signal_buffer = []          # rolling N-day signal deque

    tqqq_series = ytd["tqqq"].dropna(); voo_series = ytd["voo"].dropna()
    prev_tqqq   = tqqq_series.iloc[0] if len(tqqq_series) else 1.0
    prev_voo    = voo_series.iloc[0]  if len(voo_series)  else 1.0

    results = []

    for i, (date, row) in enumerate(ytd.iterrows()):
        def g(col):
            v = row.get(col)
            return None if (v is None or pd.isna(v)) else float(v)

        if i == 0:
            cur_alloc = compute_signal(row, cfg)
            results.append({"date": date, "real_nav": nav, "tqqq_alloc": cur_alloc})
            if not pd.isna(row.get("tqqq", float("nan"))): prev_tqqq = row["tqqq"]
            if not pd.isna(row.get("voo",  float("nan"))): prev_voo  = row["voo"]
            continue

        # ── Daily P&L on existing holdings ──────────────────────
        tqqq_now = row["tqqq"] if not pd.isna(row.get("tqqq", float("nan"))) else prev_tqqq
        voo_now  = row["voo"]  if not pd.isna(row.get("voo",  float("nan"))) else prev_voo
        tr = (tqqq_now / prev_tqqq - 1) if prev_tqqq > 0 else 0.0
        vr = (voo_now  / prev_voo  - 1) if prev_voo  > 0 else 0.0
        nav = nav * (1 + cur_alloc * tr + (1 - cur_alloc) * vr)

        # ── Compute today's raw target signal ───────────────────
        raw_target = compute_signal(row, cfg)

        # ── Daily trailing stop: immediate, no confirmation ─────
        tqqq_p = g("tqqq"); tqqq_h = g("tqqq_trail_high")
        if tqqq_p and tqqq_h and tqqq_p < tqqq_h * cfg["trail_pct"]:
            raw_target = 0.0

        # ── Confirmation buffer ──────────────────────────────────
        signal_buffer.append(raw_target)
        if len(signal_buffer) > max(confirm_enter, confirm_exit):
            signal_buffer.pop(0)

        # On Mondays, check if confirmation threshold met
        new_alloc = cur_alloc
        if date.weekday() == 0:
            # Exiting (reducing TQQQ): only need confirm_exit consecutive days
            # Entering/increasing TQQQ: need confirm_enter consecutive days
            is_exit = (raw_target < cur_alloc - 0.05)
            req_days = confirm_exit if is_exit else confirm_enter
            if len(signal_buffer) >= req_days:
                recent = signal_buffer[-req_days:]
                # All recent signals agree on direction
                if all(s <= raw_target + 0.05 and s >= raw_target - 0.05
                       for s in recent):
                    new_alloc = raw_target

        # ── Apply slippage on allocation change ─────────────────
        if abs(new_alloc - cur_alloc) > 0.01:
            trade_size = abs(new_alloc - cur_alloc)
            nav *= (1 - slip * trade_size)   # one-way cost on changed portion

        cur_alloc = new_alloc
        results.append({"date": date, "real_nav": nav, "tqqq_alloc": cur_alloc})
        prev_tqqq = tqqq_now
        prev_voo  = voo_now

    bt = pd.DataFrame(results).set_index("date")
    # Attach benchmarks
    ytd_bt = ind[ind.index >= start]
    for col, asset in [("voo_nav", "voo"), ("tqqq_nav", "tqqq"),
                       ("qqq_nav", "qqq")]:
        prices = ytd_bt[asset].dropna()
        if len(prices):
            bt[col] = (ytd_bt[asset] / prices.iloc[0]).reindex(bt.index)
    return bt


def gap_analysis(bt_ideal: pd.DataFrame, bt_real: pd.DataFrame,
                 label: str = "APEX v2.0") -> None:
    """Print side-by-side ideal vs realistic metrics, year by year."""
    from backtest_ytd import metrics as _metrics

    m_ideal = _metrics(bt_ideal["apex_nav"], "Ideal (backtest)")
    m_real  = _metrics(bt_real["real_nav"],  "Realistic")

    print(f"\n{'═'*72}")
    print(f"  Gap Analysis: Ideal Backtest  vs  Realistic Simulation")
    print(f"  (3-day entry confirmation  |  1-day exit  |  8 bps slippage/trade)")
    print(f"{'═'*72}")
    print(f"  {'Metric':<22} {'Ideal':>10} {'Realistic':>10} {'Gap':>10}")
    print(f"  {'─'*20}  {'─'*9}  {'─'*9}  {'─'*9}")
    fields = [
        ("CAGR",        "cagr",      ".1%"),
        ("Ann. Vol",    "ann_vol",   ".1%"),
        ("Sharpe",      "sharpe",    ".2f"),
        ("Max Drawdown","max_dd",    ".1%"),
    ]
    for name, key, fmt_s in fields:
        vi = m_ideal[key]; vr = m_real[key]
        gap = vr - vi
        fmt = f"{{:{fmt_s}}}"
        print(f"  {name:<22} {fmt.format(vi):>10} {fmt.format(vr):>10} "
              f"  {fmt.format(gap):>9}")

    # Year by year gap
    print(f"\n  {'Year':<8} {'Ideal':>9} {'Realistic':>10} {'Gap':>9}")
    print(f"  {'─'*36}")
    yr_i = yearly_stats(bt_ideal["apex_nav"])
    yr_r = yearly_stats(bt_real["real_nav"])
    total_gap = 0
    n_years   = 0
    for yr in sorted(yr_i.index):
        ri = yr_i["return"].get(yr, float("nan"))
        rr = yr_r["return"].get(yr, float("nan"))
        if np.isnan(ri) or np.isnan(rr):
            continue
        gap = rr - ri
        total_gap += gap
        n_years   += 1
        marker = " !" if gap < -0.05 else ""
        print(f"  {yr:<8} {ri:>+8.1%}  {rr:>+9.1%}  {gap:>+8.1%}{marker}")
    if n_years:
        print(f"  {'─'*36}")
        print(f"  {'Avg gap':<8} {'':>9}  {'':>9}  {total_gap/n_years:>+8.1%}/yr")
    print(f"{'═'*72}")


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    args    = sys.argv[1:]
    do_plot = "--plot" in args
    end     = datetime.now().strftime("%Y-%m-%d")

    # 1. Download
    data = load_full_data(end)

    # 2. Build indicator matrix
    print("⚙️   Computing indicators …")
    ind  = build_indicators(data, CONFIG)

    # 3. Run both versions from BACKTEST_START
    print(f"🔁  Simulating APEX v1.0 from {BACKTEST_START.date()} …")
    bt_v1 = run_backtest_v1(ind, BACKTEST_START)

    print(f"🔁  Simulating APEX v2.0 from {BACKTEST_START.date()} …")
    bt_v2 = run_backtest(ind, CONFIG, BACKTEST_START)

    # Align v1 benchmarks from v2
    bt_v1["voo_nav"]  = bt_v2["voo_nav"]
    bt_v1["tqqq_nav"] = bt_v2["tqqq_nav"]
    bt_v1["qqq_nav"]  = bt_v2["qqq_nav"]
    bt_v1["qld_nav"]  = bt_v2["qld_nav"]

    # 4. Full-period metrics
    initial = 100_000
    results = [
        full_metrics(bt_v2["apex_nav"],  "APEX v2.0 (improved)", initial),
        full_metrics(bt_v1["apex_nav"],  "APEX v1.0 (original)", initial),
        full_metrics(bt_v2["voo_nav"],   "Buy & Hold VOO",        initial),
        full_metrics(bt_v2["qqq_nav"],   "Buy & Hold QQQ",        initial),
        full_metrics(bt_v2["qld_nav"],   "Buy & Hold QLD",        initial),
        full_metrics(bt_v2["tqqq_nav"],  "Buy & Hold TQQQ",       initial),
    ]

    print(f"\n{'═'*80}")
    print(f"  APEX Strategy — 15-Year Backtest  "
          f"({BACKTEST_START.strftime('%Y-%m-%d')} → {bt_v2.index[-1].strftime('%Y-%m-%d')})")
    print(f"  {(bt_v2.index[-1] - BACKTEST_START).days // 365} years  |  "
          f"{len(bt_v2)} trading days  |  initial capital: ${initial:,}")
    print(f"{'═'*80}")
    print_summary_table(results, initial)

    # 5. Yearly breakdown
    print_yearly_table(bt_v2, bt_v1, {})

    # 6. Realistic simulation (gap analysis)
    print(f"\n🔁  Simulating APEX v2.0 realistic (3-day confirm + slippage) …")
    bt_real = run_realistic(ind, CONFIG, BACKTEST_START)
    results.insert(2, full_metrics(bt_real["real_nav"], "APEX v2.0 (realistic)", initial))
    gap_analysis(bt_v2, bt_real)

    # 7. Drawdown analysis
    print(f"\n{'─'*60}")
    print("  Worst Single-Year Drawdowns  (v2.0 vs v1.0 vs QQQ)")
    print(f"  {'Year':<8} {'v2':>8} {'v1':>8} {'QQQ':>8}")
    print(f"{'─'*60}")
    yr_v2_dd  = yearly_stats(bt_v2["apex_nav"])["max_dd"].sort_values()
    yr_v1_dd  = yearly_stats(bt_v1["apex_nav"])["max_dd"]
    yr_qqq_dd = yearly_stats(bt_v2["qqq_nav"])["max_dd"]
    for yr, dd in yr_v2_dd.head(7).items():
        dd_v1  = yr_v1_dd.get(yr, float("nan"))
        dd_qqq = yr_qqq_dd.get(yr, float("nan"))
        print(f"    {yr:<6}   {dd:>+7.1%}  {dd_v1:>+7.1%}  {dd_qqq:>+7.1%}")

    # 7. Bear market resilience
    print(f"\n{'─'*60}")
    print("  Bear Market Performance (TQQQ drawdown > -50%)")
    print(f"{'─'*60}")
    bear_periods = [
        ("COVID crash",   "2020-02-20", "2020-03-23"),
        ("2022 bear",     "2021-11-19", "2022-12-28"),
    ]
    for name, s, e in bear_periods:
        mask = (bt_v2.index >= s) & (bt_v2.index <= e)
        if mask.sum() < 5:
            continue
        def period_ret(nav, m): return nav[m].iloc[-1]/nav[m].iloc[0] - 1
        rv2   = period_ret(bt_v2["apex_nav"], mask)
        rv1   = period_ret(bt_v1["apex_nav"], mask)
        rvoo  = period_ret(bt_v2["voo_nav"],  mask)
        rtqqq = period_ret(bt_v2["tqqq_nav"], mask)
        rqqq  = period_ret(bt_v2["qqq_nav"],  mask)
        print(f"\n  {name} ({s} → {e}):")
        print(f"    APEX v2: {rv2:+.1%}   APEX v1: {rv1:+.1%}   "
              f"VOO: {rvoo:+.1%}   QQQ: {rqqq:+.1%}   TQQQ: {rtqqq:+.1%}")

    # 8. Optional plot
    if do_plot:
        plot_full(bt_v2, bt_v1)
    else:
        print("\n  Tip: run with --plot for the 3-panel 15-year chart")

    print(f"\n{'═'*80}\n")
    return bt_v2, bt_v1, results


if __name__ == "__main__":
    bt_v2, bt_v1, results = main()
