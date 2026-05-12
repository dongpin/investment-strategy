# APEX Strategy 🔺
### Adaptive Position EXecution Protocol  `v2.0`

> A **VOO ↔ TQQQ** intelligent rotation system based on 9-dimension signal scoring, dynamic volatility targeting, and trailing stops.

---

## Table of Contents

1. [Strategy Overview](#strategy-overview)
2. [Core Idea](#core-idea)
3. [Three-Layer Architecture](#three-layer-architecture)
4. [Signal Scoring System](#signal-scoring-system)
5. [Position Sizing](#position-sizing)
6. [Backtest Results](#backtest-results)
7. [Installation & Usage](#installation--usage)
8. [User Guide](#user-guide)
9. [Disclaimer](#disclaimer)

---

## Strategy Overview

The core premise of APEX Strategy:

> **TQQQ (3× Nasdaq 100) is only an effective tool under specific market conditions.** Holding TQQQ in the right conditions can dramatically amplify returns; holding it in the wrong conditions causes catastrophic losses due to daily compounding decay.

The strategy precisely identifies "the right conditions" through **9-dimension signal scoring**, combined with **dynamic volatility targeting** — maximizing the Sharpe ratio while maintaining high returns.

**Designed for: Roth IRA (tax-free accounts)**
Tax drag would reduce annualized returns from ~22% to ~9% in a taxable account. This strategy is not suitable for taxable accounts.

### v1.0 → v2.0 Changes

| Change | v1.0 | v2.0 | Impact |
|---|---|---|---|
| EMA death cross | Layer 1 hard stop | Layer 2 score only (−4) | Earlier recovery re-entry |
| Signal dimensions | 8 | 9 (+ VIX Momentum) | Better timing in transitions |
| Volatility budget | Fixed 20% | Dynamic: 25% in strong bull, 20% otherwise | Higher TQQQ in clean bull regimes |
| Score=6 allocation | 85% | 90% | More aggressive in near-ideal conditions |

---

## Core Idea

```
Good market conditions  → Hold TQQQ (3× leverage amplifies gains)
Bad market conditions   → Hold VOO  (S&P 500 defense / capital preservation)

"Good" = composite score across 9 signal dimensions + volatility environment permits
```

The key mechanism behind TQQQ — **Volatility Decay (daily compounding drag)**:

```
Extreme example:
  Day 1: QQQ +10% → TQQQ +30%: $100 → $130
  Day 2: QQQ -10% → TQQQ -30%: $130 → $91

Result: QQQ -1%, TQQQ -9%
In high-volatility markets, TQQQ can lose money even when the direction is right.
```

Therefore, APEX's volatility targeting layer automatically reduces TQQQ exposure during high volatility.

---

## Three-Layer Architecture

### 🔴 Layer 1: Hard Circuit Breakers (Highest Priority)

Any one triggered → **100% VOO, ignore all other signals**

| Condition | Threshold | Reason |
|---|---|---|
| QQQ drawdown from ATH | `> −10%` | Trend breakdown, TQQQ decay accelerates |
| VIX fear index | `> 25` | Extreme volatility, massive daily compounding drag |
| QQQ vs 200-day SMA | `falls below` | Entering structural bear market regime |

> **v2.0:** EMA death cross is no longer a Layer 1 hard stop. It caused a 41-day exit blackout (Feb 12 – Apr 14 2026) during a market recovery, because EMAs lag price by definition. The −4 score penalty in Layer 2 already drives allocation to 0% during genuine downtrends. The SMA200 hard stop reliably covers structural bear markets.

---

### 🟢 Layer 2: 9-Dimension Signal Scoring

9 independent dimensions scored and summed; total maps to TQQQ base allocation:

```
Score ≤ 0  →   0%      Pure defense
Score   1  →  20%      Cautious entry
Score   2  →  35%      Cautious bull
Score   3  →  50%      Neutral bull
Score   4  →  65%      Standard bull
Score   5  →  75%      Strong bull
Score   6  →  90%      Very strong bull
Score ≥ 7  → 100%      Ideal leverage conditions
```

---

### 🟡 Layer 3: Dynamic Volatility Targeting

```
Final TQQQ allocation = min(Layer 2 allocation, target_vol / TQQQ realized vol)

TQQQ realized volatility = 20-day rolling daily std × √252

Regime-adaptive vol budget:
  Strong-bull (score ≥ 5 AND VIX < 20): target_vol = 25%
  All other conditions:                  target_vol = 20%

Example — standard regime:
  TQQQ 20-day ann. vol = 55%, target = 20%
  Vol cap = 20% / 55% = 36%
  Signal score gives 75% → final = min(75%, 36%) = 36%

Example — strong-bull regime (score = 8, VIX = 17):
  TQQQ 20-day ann. vol = 43%, target = 25%
  Vol cap = 25% / 43% = 58%
  Signal score gives 100% → final = min(100%, 58%) = 58%
```

**Why dynamic vol target?** A fixed 20% cap is overly conservative when all 9 signals align bullishly and VIX is below 20. In those conditions, the incremental return from extra TQQQ exposure outweighs the incremental volatility. This raised January and May 2026 average allocations from ~40% to ~50%, directly contributing to APEX v2.0 beating QQQ YTD.

---

### 🔵 Trailing Stop

```
Daily check: if TQQQ current price < 15-day high × 92%
→ Immediately exit to 100% VOO (do not wait for Sunday signal)

Fires 7–18 days faster than EMA death cross.
Triggered on Day 3 of the 2020 COVID crash, avoiding most of the decline.
No confirmation period required — exits are immediate.
```

---

## Signal Scoring System

APEX v2.0 uses **9 independent scoring dimensions**. Each captures a distinct aspect of market conditions. Scores are summed; the total maps to a TQQQ base allocation via the table above.

---

### Dimension 1 — EMA Trend (EMA20 vs EMA50)

| Condition | Score |
|---|---|
| EMA20 > EMA50 (golden cross) | **+3** |
| EMA20 < EMA50 (death cross) | **−4** |

**What it measures:** Whether short-term price momentum is accelerating or decelerating, as reflected in exponential moving average crossovers.

**Why it's included:** The EMA20/50 cross is one of the most reliable trend-confirmation signals in technical analysis. A golden cross means recent prices are consistently above medium-term average — the regime where TQQQ's compounding works *in your favor*. A death cross means the opposite: holding TQQQ in a downtrend erodes capital through volatility decay each day.

**Why asymmetric (+3 / −4):** Drawdown in TQQQ (3× leveraged) is far more destructive than missing upside. A false death cross costs a few percent; a missed true bear market costs 50–80%. The penalty is larger than the reward.

> **v2.0:** Removed from Layer 1 hard stops. The −4 score penalty alone drives allocation to 0% during genuine downtrends, while allowing partial re-entry when other signals improve during early recoveries.

---

### Dimension 2 — SMA200 Regime

| Condition | Score |
|---|---|
| QQQ above 200-day SMA | **+2** |
| QQQ below 200-day SMA | **−3** |

**What it measures:** The long-term trend regime — whether the market is in a structural bull or bear.

**Why it's included:** The 200-day SMA is the most widely-watched long-term trend indicator. Bear market regimes (below SMA200) are defined by high volatility and sustained downtrends where TQQQ's daily compounding decay destroys value rapidly.

**Why this is also in Layer 1:** Unlike EMA cross, SMA200 changes slowly (200-day window) and rarely gives false signals in short corrections. QQQ below its 200-day average signals a confirmed structural bear — the environment where TQQQ has historically lost 79–95%.

---

### Dimension 3 — RSI(14) Momentum

| Condition | Score |
|---|---|
| RSI > 70 (overbought) | 0 |
| RSI 60–70 (strong momentum) | **+2** |
| RSI 50–60 (positive) | **+1** |
| RSI 40–50 (weakening) | **−1** |
| RSI < 40 (weak / oversold) | **−3** |

**What it measures:** Whether buying or selling pressure dominates on a 14-day timeframe.

**Why it's included:** RSI bridges trend (EMAs) and volatility (VIX). RSI 60–70 is the sweet spot for TQQQ: strong buying pressure but not yet overbought. RSI > 70 scores 0, not negative — overbought does not mean reversal is imminent, it just means the premium for adding leverage has diminished.

**Why RSI < 40 scores −3:** Oversold conditions in Nasdaq almost always coincide with high volatility — exactly when TQQQ's daily rebalancing cost is highest.

---

### Dimension 4 — VIX Level

| Condition | Score |
|---|---|
| VIX < 13 (extremely calm) | **+3** |
| VIX 13–18 (calm) | **+2** |
| VIX 18–22 (mild concern) | **+1** |
| VIX 22–27 (elevated) | **−1** |
| VIX > 27 (fear zone) | **−3** |

**What it measures:** The market's expectation of near-term volatility — the "fear gauge."

**Why it's included:** VIX is the most direct input into TQQQ's volatility decay calculation. Low VIX → small daily QQQ swings → TQQQ compounds efficiently. High VIX → large daily swings → TQQQ loses money to decay even in sideways markets.

**Why VIX > 25 is also a Layer 1 hard stop:** Above 25, TQQQ's realized annualized volatility typically exceeds 60–80%. The mathematical cost of holding TQQQ for 2–3 weeks at that level can wipe out weeks of prior gains — regardless of direction.

---

### Dimension 5 — 20-Day Price Momentum

| Condition | Score |
|---|---|
| 20d return > +8% | **+2** |
| 20d return +3% to +8% | **+1** |
| 20d return −3% to +3% | 0 |
| 20d return < −3% | **−2** |

**What it measures:** Short-term (1-month) price trend of QQQ.

**Why it's included:** Momentum is one of the most durable factors in equity markets. An index that has risen 5–10% in the last month is statistically more likely to continue rising over the next 2–4 weeks. TQQQ performs best in trending phases, not choppy sideways phases.

**Why the negative score is weaker (−2 vs +2):** Short-term momentum can reverse quickly via oversold bounces. The negative signal is a warning flag, not a decisive exit trigger.

---

### Dimension 6 — 60-Day Price Momentum

| Condition | Score |
|---|---|
| 60d return > +15% | **+2** |
| 60d return +5% to +15% | **+1** |
| 60d return −5% to +5% | 0 |
| 60d return < −5% | **−2** |

**What it measures:** Medium-term (3-month) price trend of QQQ.

**Why it's included:** Complements D5. The 60-day window filters out short-term noise and confirms whether a bull trend has genuine medium-term momentum. A market can bounce 5% in 20 days while still being in a 60-day downtrend. Holding both timeframes avoids acting on false reversals.

**Why separate from 20-day:** When both are positive (+2 each), it signals a clean, broad-based rally — the best environment for TQQQ leverage. When they diverge (20d positive, 60d still negative), the mixed signal appropriately reduces confidence and allocation.

---

### Dimension 7 — 10Y Treasury Rate 60-Day Change

| Condition | Score |
|---|---|
| 10Y yield fell > 0.25 pp | **+1** |
| 10Y yield stable (±0.25 pp) | 0 |
| 10Y yield rose 0.25–0.75 pp | **−1** |
| 10Y yield rose > 0.75 pp | **−2** |

**What it measures:** Direction and magnitude of change in the 10-year US Treasury yield over 60 trading days.

**Why it's included:** Rising long-term interest rates compress Nasdaq valuations disproportionately — high-multiple tech stocks are discounted more aggressively. The 60-day window filters daily noise while capturing genuine rate trend shifts. A spike of +0.75 pp in 60 days ≈ one full Fed hike equivalent and historically marks the start of Nasdaq underperformance.

**Why the asymmetric scores:** Falling rates are already partially priced in by the market (+1 only). A rate spike is a direct and immediate headwind (−2 at the extreme).

---

### Dimension 8 — QQQ Drawdown Depth

| Condition | Score |
|---|---|
| QQQ drawdown < −15% from ATH | **−3** |
| QQQ drawdown −8% to −15% | **−1** |
| QQQ drawdown > −8% (near highs) | 0 |

**What it measures:** How far QQQ has fallen from its all-time high.

**Why it's included:** Drawdown depth is a direct proxy for TQQQ's accumulated volatility decay. At −10% off ATH, TQQQ has typically lost 25–30% from its own high; at −15%, it's lost 40–45%. Even if short-term momentum turns positive in a relief bounce, a deep drawdown means the compounding hole is already severe.

**Why no positive score near ATH:** Near-ATH conditions are already captured by D5 (20d momentum) and D1 (EMA trend). Adding a bonus would double-count bull conditions.

---

### Dimension 9 — VIX Momentum (5-Day Change) *(v2.0)*

| Condition | Score |
|---|---|
| VIX 5d change < −3 (fear dropping fast) | **+2** |
| VIX 5d change −3 to −1 (easing) | **+1** |
| VIX 5d change ±1 (stable) | 0 |
| VIX 5d change +1 to +4 (rising) | **−1** |
| VIX 5d change > +4 (spiking) | **−2** |

**What it measures:** The *rate of change* of VIX — whether fear is actively subsiding or building.

**Why it's included:** D4 tells you *where* VIX is; D9 tells you *where it's going*. A VIX at 22 that was 29 five days ago is fundamentally different from a VIX at 22 that was 17 five days ago. The former signals improving conditions; the latter signals deteriorating ones.

**The problem it solves:** In April 2026, VIX fell from 31 to 18 over 10 days. D4 scored only +1 (VIX 22 = "mild concern"). D9 scored +2 (fast-falling VIX), pushing total score high enough to trigger earlier re-entry at 25–30% TQQQ — capturing the first leg of the recovery that v1.0 missed entirely.

---

## Position Sizing

Full calculation flow (executed every Sunday evening):

```python
# Step 1: Compute 9-dimension total score
score = (ema_score       # D1: EMA20 vs EMA50      (+3 / -4)
       + sma200_score    # D2: QQQ vs SMA200        (+2 / -3)
       + rsi_score       # D3: RSI(14)               (0 to +2 / -3)
       + vix_score       # D4: VIX level             (+3 to -3)
       + mom20_score     # D5: 20-day momentum       (+2 / -2)
       + mom60_score     # D6: 60-day momentum       (+2 / -2)
       + tnx_score       # D7: 10Y rate 60d change   (+1 / -2)
       + dd_score        # D8: QQQ drawdown depth     (0 / -3)
       + vix_mom_score)  # D9: VIX 5-day change      (+2 / -2)

# Step 2: Score → base allocation
alloc_map = {0:0.00, 1:0.20, 2:0.35, 3:0.50,
             4:0.65, 5:0.75, 6:0.90}  # score ≥ 7 → 1.00
base_alloc = alloc_map.get(min(score, 6), 1.0) if score > 0 else 0.0

# Step 3: Dynamic volatility cap
if score >= 5 and vix < 20:
    target_vol = 0.25   # strong-bull regime: higher leverage budget
else:
    target_vol = 0.20   # standard regime

tqqq_vol = rolling_std(TQQQ_returns, 20_days) × √252
vol_cap  = target_vol / tqqq_vol

# Step 4: Take minimum
final_alloc = min(base_alloc, vol_cap)

# Step 5: Trailing stop (checked daily, overrides everything)
if TQQQ_price < TQQQ_15day_high × 0.92:
    final_alloc = 0.0   # emergency exit to VOO

# Result
TQQQ = final_alloc × total_assets
VOO  = (1 - final_alloc) × total_assets
```

**Execution rules:**
- Signal computed Sunday evening → executed Monday 10:00 AM (T+1)
- Confirm signal for 3 consecutive days before entering TQQQ; 1 day to exit
- Only act if allocation change > 5%
- Use limit orders, not market orders

---

## Backtest Results

### 15-Year Full Backtest (2010–2026, Roth IRA, tax-free)

Simulation from TQQQ inception (2010-03-01) through May 2026. Run `python backtest_full.py --plot` to reproduce.

| Strategy | CAGR | Vol | Sharpe | Max Drawdown | $100K → |
|---|---|---|---|---|---|
| **APEX v2.0** | **+23.9%** | 25.7% | **0.96** | −36.3% | **$3.2M** |
| APEX v1.0 | +22.2% | 23.8% | 0.96 | −35.9% | $2.6M |
| APEX v2.0 (realistic) | +22.4% | 26.4% | 0.90 | −40.5% | $2.7M |
| Buy & Hold VOO | +15.0% | 17.1% | 0.90 | −34.0% | — |
| Buy & Hold QQQ | +19.6% | 20.6% | 0.97 | −35.1% | $1.8M |
| Buy & Hold QLD | +33.3% | 41.1% | 0.91 | −63.7% | $10.4M |
| Buy & Hold TQQQ | +43.2% | 61.0% | 0.90 | −81.7% | $33.3M |

> **Realistic simulation** applies 3-day entry confirmation, 1-day exit confirmation, and 8 bps execution slippage per trade. This is the more honest estimate of live performance.

**Key finding:** APEX v2.0 beats QQQ by +4.3% CAGR over 16 years with nearly identical Sharpe (0.96 vs 0.97). $100K compounded at 23.9% for 16 years = $3.2M vs $1.8M at 19.6%.

### Year-by-Year Returns

| Year | APEX v2 | APEX v1 | VOO | QQQ | TQQQ | Avg TQQQ% |
|---|---|---|---|---|---|---|
| 2010 | +39.4% | +38.5% | — | +21.0% | +58.8% | 38% |
| 2011 | +0.5% | +3.0% | +1.0% | +1.9% | −12.0% | 20% |
| 2012 | +37.8% | +36.0% | +14.3% | +15.9% | +44.1% | 35% |
| 2013 | +56.8% | +52.7% | +29.2% | +32.4% | +119.3% | 64% |
| 2014 | +30.5% | +24.8% | +14.0% | +20.1% | +60.8% | 55% |
| 2015 | −17.5% | −16.3% | +1.3% | +9.8% | +18.2% | 33% |
| 2016 | +0.4% | +2.6% | +13.8% | +9.4% | +18.6% | 37% |
| 2017 | +78.4% | +70.9% | +20.9% | +31.5% | +112.9% | 78% |
| 2018 | −11.3% | −9.5% | −5.2% | −1.8% | −23.7% | 30% |
| 2019 | +59.0% | +51.1% | +31.3% | +38.4% | +130.5% | 44% |
| 2020 | +31.4% | +29.8% | +17.3% | +46.0% | +100.1% | 15% |
| 2021 | +44.2% | +40.1% | +30.6% | +29.2% | +91.3% | 39% |
| 2022 | −23.2% | −22.3% | −18.7% | −33.2% | **−79.7%** | **0%** |
| 2023 | +38.3% | +34.5% | +26.8% | +55.9% | +204.9% | 18% |
| 2024 | +31.4% | +32.3% | +25.8% | +27.7% | +66.7% | 38% |
| 2025 | +19.4% | +17.2% | +18.1% | +21.0% | +35.2% | 35% |
| 2026* | +17.6% | +13.0% | +8.5% | +16.5% | +47.3% | 22% |

*YTD through May 2026. v2 beat v1 in 11 of 17 years.*

**2022 bear market:** Circuit breakers fired in Nov 2021 (QQQ below SMA200). APEX held 0% TQQQ for the full year, limiting loss to −23.2% vs TQQQ's −79.7%.

### Realistic Gap Analysis

The gap between idealized backtest and realistic live performance averages **−1.3%/yr**, driven by:

| Source | Estimated Drag | Notes |
|---|---|---|
| Confirmation delay (3d entry / 1d exit) | ~−1.0%/yr avg | High variance: −18% in 2019, +8% in 2013 |
| Execution slippage (8 bps/trade) | ~−0.3%/yr | ~15 rebalance trades/yr × 8 bps |
| Price gap (close vs Monday 10 AM) | ~−0.2%/yr | Largest on high-VIX Mondays |

Realistic expected forward CAGR: **~19–22%** (accounting for confirmation lag and modest parameter overfitting).

### YTD 2026 Backtest (89 trading days through May 11)

| Strategy | YTD Return | Ann. CAGR | Vol | Sharpe | Max Drawdown |
|---|---|---|---|---|---|
| **APEX v2.0** | **+17.6%** | **+59.1%** | 23.8% | **2.07** | −17.2% |
| APEX v1.0 | +13.1% | +42.1% | 21.0% | 1.78 | −15.6% |
| Buy & Hold VOO | +8.5% | +26.3% | 14.0% | 1.74 | −8.9% |
| Buy & Hold QQQ | +16.5% | +54.8% | 18.6% | 2.44 | −11.7% |
| Buy & Hold TQQQ | +47.3% | +202.9% | 55.3% | 2.28 | −33.5% |

Key 2026 allocation changes (v1 → v2):

| Month | v1 Avg | v2 Avg | Driver |
|---|---|---|---|
| January | 40% | 49% | Dynamic vol target (25%) raised cap in clean bull regime |
| February | 5% | 6% | VIX > 25 → hard stop; both versions defensive |
| March | 0% | 0% | Multiple hard stops active |
| April | 14% | 25% | EMA hard stop removed + VIX momentum signal → earlier re-entry |
| May | 42% | 53% | Dynamic vol target; strong bull score (9) |

---

## Installation & Usage

### Requirements

```bash
Python 3.8+
pip install yfinance pandas numpy matplotlib
```

### Quick Start

```bash
# Get today's signal (run every Sunday evening)
python apex_strategy.py

# View strategy summary card
python apex_strategy.py --summary

# View weekly execution checklist
python apex_strategy.py --checklist

# Output JSON (for programmatic use)
python apex_strategy.py --json

# YTD backtest with chart
python backtest_ytd.py --plot

# 15-year full backtest with chart
python backtest_full.py --plot
```

### Calling from Python

```python
from apex_strategy import run_apex, CONFIG

# Default config
result = run_apex()
print(f"TQQQ: {result['tqqq_pct']:.0%}  VOO: {result['voo_pct']:.0%}")
print(f"Score: {result['score']}  Vol cap: {result['vol_cap']:.0%}")

# More conservative: lower vol target, tighter stops
my_config = {
    **CONFIG,
    "target_vol"     : 0.15,   # 15% base vol target
    "target_vol_bull": 0.20,   # 20% in bull regime (was 25%)
    "vix_threshold"  : 22.0,   # tighter VIX hard stop
    "trail_pct"      : 0.94,   # tighter trailing stop (−6%)
}
result = run_apex(cfg=my_config)
```

---

## User Guide

### CONFIG Reference

```python
CONFIG = {
    # Assets
    "signal_ticker"   : "QQQ",   # signal source
    "growth_asset"    : "TQQQ",  # leveraged ETF
    "stable_asset"    : "VOO",   # defensive ETF

    # Layer 1: Hard circuit breakers
    "dd_threshold"    : -10.0,   # QQQ drawdown from ATH → force VOO
    "vix_threshold"   : 25.0,    # VIX level → force VOO

    # Layer 2: Score → allocation map
    "alloc_map"       : {0:0.00, 1:0.20, 2:0.35, 3:0.50,
                         4:0.65, 5:0.75, 6:0.90},  # ≥7 → 1.00

    # Layer 3: Dynamic volatility targeting
    "target_vol"      : 0.20,    # base vol budget (standard regime)
    "target_vol_bull" : 0.25,    # vol budget in strong-bull regime
    "dynamic_vol"     : True,    # enable regime-adaptive vol budget
    "vol_window"      : 20,      # days for realized vol calculation

    # Trailing stop
    "trail_window"    : 15,      # trailing high lookback (days)
    "trail_pct"       : 0.92,    # exit if price < high × 0.92 (−8%)

    # Execution
    "confirm_days"    : 3,       # days to confirm signal before acting
    "exec_delay"      : 1,       # T+1 execution
}
```

### Weekly Workflow

```
Sunday 8–9 PM
  □ Run: python apex_strategy.py
  □ Record: score, final allocation, VIX, vol cap, regime (bull/standard)

Monday 9:30–10:00 AM  ← Do NOT trade (widest spreads at open)

Monday 10:00 AM+
  □ Confirm signal matches Sunday's (3-day confirmation check)
  □ Check if VIX has moved significantly since Sunday close
  □ If allocation change > 5%: execute in Fidelity
    Fidelity → Trade → ETFs → Enter dollar amount (not share count)
  □ Use limit orders, not market orders

Daily (2 minutes)
  □ Check: TQQQ current price vs 15-day high × 0.92
  □ If triggered → exit to VOO immediately, do not wait for Sunday
  □ Watch for VIX crossing 25 intraday
```

### Emergency Procedures

| Situation | Trigger | Immediate Action |
|---|---|---|
| Trailing stop | TQQQ < 15d high × 92% | Same-day exit → VOO; no confirmation needed |
| Intraday VIX spike | VIX crosses 25 intraday | Execute circuit breaker before close |
| Major geopolitical event | War / financial crisis | Check trailing stop + VIX immediately |
| Signal whipsaw | 3+ consecutive weeks flipping 0%↔50% | Lock to VOO 100% until signal stabilizes |

### Tuning Parameters

| Parameter | Conservative | Default | Aggressive |
|---|---|---|---|
| `dd_threshold` | −8% | **−10%** | −12% |
| `vix_threshold` | 22 | **25** | 28 |
| `target_vol` | 0.15 | **0.20** | 0.25 |
| `target_vol_bull` | 0.20 | **0.25** | 0.30 |
| `trail_pct` | 0.94 | **0.92** | 0.88 |
| `confirm_days` | 5 | **3** | 1 |

---

## Disclaimer

```
⚠ This strategy is for educational and research purposes only.
  It does not constitute investment advice.

1. Account type: In a taxable account, after-tax CAGR is only ~9%,
   far below simple buy-and-hold VOO. Must be executed in a
   Roth IRA or other tax-free account.

2. Past performance: Backtest results are based on historical data
   simulation and do not represent future returns. Actual results
   will differ due to slippage, execution delays, and other factors.
   Realistic live performance is estimated at ~19–22% CAGR, not 23.9%.

3. Leverage risk: TQQQ can lose over 79% in a single bear market year
   (actual 2022 data). While the strategy attempts to avoid this,
   complete protection cannot be guaranteed. Max simulated drawdown
   is −36% (realistic: −40%).

4. Backtest period bias: The 2010–2026 test period covers one of the
   greatest Nasdaq bull markets in history, supported by a decade of
   near-zero rates and Fed QE. Future regimes may differ significantly.

5. Please consult a licensed financial advisor before making any
   investment decisions.

APEX Strategy v2.0 — For reference use only
```

---

## File Structure

```
apex_strategy.py     Main strategy file — run this for Sunday signals
backtest_ytd.py      YTD backtest engine (v1 vs v2, with --plot)
backtest_full.py     15-year full backtest (v1 vs v2 vs benchmarks, with --plot)
README.md            This document
```

---

*APEX Strategy v2.0 · Python 3.8+ · Roth IRA only · Not financial advice*
