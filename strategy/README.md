# APEX Strategy 🔺
### Adaptive Position EXecution Protocol  `v3.0`

> A **VOO ↔ TQQQ** intelligent rotation system with a 4-layer architecture: quarterly macro regime overlay, hard circuit breakers, 10-dimension signal scoring, and dynamic volatility targeting.

---

## Table of Contents

1. [Strategy Overview](#strategy-overview)
2. [Core Idea](#core-idea)
3. [Four-Layer Architecture](#four-layer-architecture)
4. [Signal Scoring System](#signal-scoring-system)
5. [Position Sizing](#position-sizing)
6. [Backtest Results](#backtest-results)
7. [Installation & Usage](#installation--usage)
8. [Reading the Output & Taking Action](#reading-the-output--taking-action)
9. [User Guide](#user-guide)
10. [Disclaimer](#disclaimer)

---

## Strategy Overview

The core premise of APEX Strategy:

> **TQQQ (3× Nasdaq 100) is only an effective tool under specific market conditions.** Holding TQQQ in the right conditions can dramatically amplify returns; holding it in the wrong conditions causes catastrophic losses due to daily compounding decay.

The strategy identifies "the right conditions" through **10-dimension signal scoring**, combined with **dynamic volatility targeting** and a **quarterly macro regime layer** — maximizing the Sharpe ratio while maintaining high returns.

**Designed for: Roth IRA (tax-free accounts)**
Tax drag would reduce annualized returns from ~22% to ~9% in a taxable account. This strategy is not suitable for taxable accounts.

### Version History

| Version | Key Change | Backtest CAGR |
|---|---|---|
| v1.0 | Original (8 dims, 4 hard stops) | +22.8% |
| v2.0 | EMA hard stop → L2 penalty; VIX momentum (9th dim); dynamic vol | +25.7% |
| **v3.0** | **Layer 0 macro regime (FRED CP/GDP); 10th dim (TNX level)** | **+26.1%** |

---

## Core Idea

```
Good market conditions  → Hold TQQQ (3× leverage amplifies gains)
Bad market conditions   → Hold VOO  (S&P 500 defense / capital preservation)

"Good" = composite score across 10 signal dimensions
       + volatility environment permits
       + macro regime (quarterly fundamental overlay) allows
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

## Four-Layer Architecture

### ⚫ Layer 0: Macro Regime  (quarterly update)

Reads S&P 500 profit margin direction from FRED (Corporate Profits / GDP) and Shiller CAPE to determine a macro regime that **adjusts the risk parameters** of Layer 1 and Layer 3. Layer 2 scoring is completely unchanged.

```
Run each quarter: python sp500_margin_tracker.py --save
Update in apex_strategy.py: CAPE_RATIO = <current Shiller CAPE>
Timing: end of January / April / July / October (after BEA data release)
```

**Regime scoring:**
```
s  =  MARGIN_SCORES[yoy]  +  MARGIN_SCORES[qoq] // 2
s  -= 1  if CAPE > 35

MARGIN_SCORES: MAJOR_EXPANSION +4  |  EXPANSION +2  |  FLAT 0
               CONTRACTION -2  |  MAJOR_CONTRACTION -4
```

**Regime → risk parameters:**

| Regime | Condition | target_vol | VIX thresh | DD thresh | max TQQQ |
|---|---|---|---|---|---|
| 🟢 EXPANSION | s ≥ 3 | 22% | 28 | −12% | 100% |
| ⚪ NEUTRAL | −2 < s < 3 | 20% | 25 | −10% | 100% |
| 🔴 CONTRACTION | s ≤ −2 | 16% | 22 | −8% | **50%** |

> **Current regime (May 2026): NEUTRAL** — Margin YoY EXPANSION (+2), QoQ FLAT (0), CAPE 37 (−1) = total +1. Default parameters in effect.  
> **Next update: August 2026** (after Q2 earnings / BEA advance estimate).

---

### 🔴 Layer 1: Hard Circuit Breakers  (thresholds set by Layer 0)

Any one triggered → **100% VOO, ignore all other signals**

| Condition | NEUTRAL threshold | EXPANSION | CONTRACTION |
|---|---|---|---|
| QQQ drawdown from 6-month high | `> −10%` | `> −12%` | `> −8%` |
| VIX fear index | `> 25` | `> 28` | `> 22` |
| QQQ vs 200-day SMA | falls below | falls below | falls below |

> **v2.0 note:** EMA death cross is no longer a Layer 1 hard stop. It caused a 41-day exit blackout (Feb 12 – Apr 14 2026) during a market recovery. The −4 score penalty in Layer 2 already drives allocation to 0% during genuine downtrends.

---

### 🟢 Layer 2: 10-Dimension Signal Scoring  (unchanged by Layer 0)

10 independent dimensions scored and summed; total maps to TQQQ base allocation:

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

### 🟡 Layer 3: Dynamic Volatility Targeting  (target_vol set by Layer 0)

```
Final TQQQ allocation = min(Layer 2 allocation, target_vol / TQQQ realized vol,
                            max_alloc [from Layer 0])

TQQQ realized volatility = 15-day rolling daily std × √252

Vol budget by regime (Layer 0) + in-regime bull adjustment:
  CONTRACTION:                         target_vol = 16%  (max_alloc = 50%)
  NEUTRAL (default):                   target_vol = 20%
  EXPANSION:                           target_vol = 22%
  NEUTRAL/EXPANSION + bull (score ≥ 4 AND VIX < 20): target_vol += 5pp

Example — NEUTRAL regime, standard:
  TQQQ 15-day ann. vol = 55%, target = 20%
  Vol cap = 20% / 55% = 36%
  Signal score gives 75% → final = min(75%, 36%) = 36%

Example — NEUTRAL regime, strong-bull (score = 8, VIX = 17):
  TQQQ 15-day ann. vol = 43%, target = 25%
  Vol cap = 25% / 43% = 58%
  Signal score gives 100% → final = min(100%, 58%) = 58%

Example — CONTRACTION regime:
  Same vol calc, but capped at 50%: final = min(calc, 50%)
```

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

APEX v3.0 uses **10 independent scoring dimensions**. Each captures a distinct aspect of market conditions. Scores are summed; the total maps to a TQQQ base allocation via the table above.

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
# Step 0: Layer 0 — load quarterly macro regime (run sp500_margin_tracker.py --save first)
regime_params = get_regime_params()  # reads sp500_output/sp500_margin_latest.json
# regime_params = {"target_vol": 0.20, "vix_threshold": 25,
#                  "dd_threshold": -10, "max_alloc": 1.0}  # example: NEUTRAL

# Apply regime to Layer 1 and Layer 3 thresholds
vix_threshold = regime_params["vix_threshold"]   # e.g. 25 (NEUTRAL)
dd_threshold  = regime_params["dd_threshold"]    # e.g. -10%
max_alloc     = regime_params["max_alloc"]       # e.g. 1.0 (0.5 in CONTRACTION)

# Step 1: Layer 1 — circuit breakers (regime-adjusted thresholds)
if QQQ_drawdown_6m < dd_threshold: return VOO_100%
if VIX > vix_threshold:            return VOO_100%
if QQQ < SMA200:                   return VOO_100%

# Step 2: Layer 2 — compute 10-dimension total score (unchanged by Layer 0)
score = (ema_score       # D1: EMA20 vs EMA50      (+3 / -4)
       + sma200_score    # D2: QQQ vs SMA200        (+2 / -3)
       + rsi_score       # D3: RSI(14)               (0 to +2 / -3)
       + vix_score       # D4: VIX level             (+3 to -3)
       + mom20_score     # D5: 20-day momentum       (+2 / -2)
       + mom60_score     # D6: 60-day momentum       (+2 / -2)
       + tnx_score       # D7: 10Y rate 60d change   (+1 / -2)
       + dd_score        # D8: QQQ drawdown depth     (0 / -3)
       + vix_mom_score   # D9: VIX 5-day change      (+2 / -2)
       + tnx_level_score)# D10: 10Y rate level        (+1 / -2)

# Step 3: Score → base allocation (unchanged)
alloc_map = {0:0.00, 1:0.20, 2:0.35, 3:0.50,
             4:0.65, 5:0.75, 6:0.90}  # score ≥ 7 → 1.00
base_alloc = alloc_map.get(min(score, 6), 1.0) if score > 0 else 0.0

# Step 4: Layer 3 — dynamic volatility cap (target_vol from Layer 0)
base_target = regime_params["target_vol"]  # 0.16/0.20/0.22 by regime
if score >= 4 and vix < 20:               # in-regime bull adjustment
    target_vol = base_target + 0.05
else:
    target_vol = base_target

tqqq_vol = rolling_std(TQQQ_returns, 15_days) × √252
vol_cap  = target_vol / tqqq_vol

# Step 5: Take minimum including Layer 0 max_alloc cap
final_alloc = min(base_alloc, vol_cap, max_alloc)

# Step 6: Trailing stop (checked daily, overrides everything)
if TQQQ_price < TQQQ_15day_high × 0.92:
    final_alloc = 0.0   # emergency exit to VOO

# Result
TQQQ = final_alloc × total_assets
VOO  = (1 - final_alloc) × total_assets
```

**Execution rules:**
- Signal computed Sunday evening → executed Monday 10:00 AM (T+1)
- Confirm signal for 2 consecutive days before entering TQQQ; 1 day to exit
- Only act if allocation change > 5%
- Use limit orders, not market orders

---

## Backtest Results

### 15-Year Full Backtest (2010–2026, Roth IRA, tax-free)

Simulation from TQQQ inception (2010-03-01) through May 2026. Run `python backtest_full.py --plot` to reproduce.

| Strategy | CAGR | Vol | Sharpe | Max Drawdown | $100K → |
|---|---|---|---|---|---|
| **APEX v3.0 (Layer 0)** | **+26.1%** | 25.6% | **1.03** | −36.1% | **$4.2M** |
| APEX v2.0 | +25.7% | 26.2% | 1.01 | −36.1% | $4.1M |
| **APEX v3.0 (realistic, confirm=2)** | **+24.2%** | **26.9%** | **0.94** | **−39.0%** | **$2.9M** |
| APEX v3.0 (realistic, confirm=3) | +22.2% | 26.5% | 0.89 | −39.0% | $2.6M |
| APEX v1.0 | +22.8% | 24.0% | 0.98 | −35.7% | $2.8M |
| Buy & Hold VOO | +15.0% | 17.1% | 0.90 | −34.0% | — |
| Buy & Hold QQQ | +19.5% | 20.6% | 0.97 | −35.1% | $1.8M |
| Buy & Hold QLD | +33.1% | 41.1% | 0.90 | −63.7% | $10.2M |
| Buy & Hold TQQQ | +43.0% | 61.0% | 0.89 | −81.7% | $32.4M |

> **Realistic simulation** applies entry confirmation, 1-day exit, and 8 bps slippage per trade.  
> **v3.1 change (May 2026):** `confirm_days` reduced 3 → 2. Backtested impact: +2% CAGR, Sharpe 0.89 → 0.94 (15yr); Sharpe 1.04 vs QQQ 0.99 over 10yr window.

**Key finding:** APEX v3.0 beats QQQ by +6.6% CAGR over 16 years with higher Sharpe (1.03 vs 0.97). $100K → $4.2M vs $1.8M. v3 beat v2 in 7 of 17 years, v2 beat v3 in 5 years.

**Layer 0 impact:** During expansion cycles (2013–2014, 2017–2018), loosened thresholds allow more TQQQ exposure. During contractions (2015, 2022), tightened thresholds and 50% TQQQ cap reduce drawdowns. Net effect: +0.4% CAGR and Sharpe 1.03 vs 1.01.

### 10-Year and 5-Year Realistic Backtest (confirm=2)

| Window | Strategy | CAGR | Vol | Sharpe | Max DD | $100K→ |
|---|---|---|---|---|---|---|
| **10yr** (2016–2026) | **APEX v3.0 realistic** | **+27.8%** | 27.3% | **1.04** | −38.5% | **$1.16M** |
| 10yr | Buy & Hold QQQ | +21.7% | 22.3% | 0.99 | −35.1% | $711K |
| 10yr | Buy & Hold VOO | +15.4% | 18.0% | 0.89 | −34.0% | $421K |
| **5yr** (2021–2026) | **APEX v3.0 realistic** | **+23.2%** | 26.2% | **0.93** | **−31.3%** | **$284K** |
| 5yr | Buy & Hold QQQ | +16.7% | 22.4% | 0.80 | −35.1% | $217K |
| 5yr | Buy & Hold VOO | +13.6% | 16.8% | 0.84 | −24.5% | $190K |
| 5yr | Buy & Hold TQQQ | +24.2% | 66.6% | 0.66 | −81.7% | $296K |

> **10-year gap vs idealized: −0.2%/yr** (down from −3.1%/yr with confirm=3). Realistic beats idealized by +1.6%/yr over the 5-year window.

### Year-by-Year Returns

| Year | APEX v3 | APEX v2 | APEX v1 | VOO | QQQ | TQQQ | Avg TQQQ% |
|---|---|---|---|---|---|---|---|
| 2010 | +35.1% | +35.9% | +34.0% | — | +21.0% | +58.8% | 39% |
| 2011 | +1.3% | +1.8% | +2.4% | +1.0% | +1.9% | −12.0% | 18% |
| 2012 | +36.5% | +36.5% | +35.9% | +14.3% | +15.9% | +44.1% | 36% |
| 2013 | +52.4% | +54.2% | +50.3% | +29.2% | +32.4% | +119.3% | 48% |
| 2014 | **+37.5%** | +36.2% | +29.5% | +14.0% | +20.1% | +60.8% | 57% |
| 2015 | **−12.4%** | −14.4% | −16.4% | +1.3% | +9.8% | +18.2% | 34% |
| 2016 | **+5.3%** | +2.5% | +4.0% | +13.8% | +9.4% | +18.6% | 36% |
| 2017 | +79.5% | +79.5% | +73.3% | +20.9% | +31.5% | +112.9% | 78% |
| 2018 | **−7.3%** | −9.2% | −7.7% | −5.2% | −1.8% | −23.7% | 26% |
| 2019 | +60.9% | +60.9% | +52.5% | +31.3% | +38.4% | +130.5% | 47% |
| 2020 | **+32.7%** | +30.8% | +29.3% | +17.3% | +46.0% | +100.1% | 17% |
| 2021 | **+47.9%** | +47.2% | +43.1% | +30.6% | +29.2% | +91.3% | 40% |
| 2022 | **−22.1%** | −23.5% | −22.6% | −18.7% | −33.2% | **−79.7%** | **0%** |
| 2023 | +51.7% | +53.3% | +35.6% | +26.8% | +55.9% | +204.9% | 27% |
| 2024 | +33.4% | +33.4% | +34.0% | +25.8% | +27.7% | +66.7% | 39% |
| 2025 | +13.5% | **+18.8%** | +17.1% | +18.1% | +21.0% | +35.2% | 28% |
| 2026* | +15.7% | +15.7% | +11.0% | +8.4% | +15.5% | +43.4% | 25% |

*YTD through May 2026. **Bold** = v3 beat v2 or v2 beat v3.*

**2022 bear market:** Circuit breakers fired in late 2021 (QQQ below SMA200). Both v2 and v3 held 0% TQQQ for most of the year. v3's tighter thresholds (CONTRACTION regime: VIX 22, DD −8%) exited slightly earlier, limiting loss to −22.1% vs v2's −23.5%.

### Realistic Gap Analysis

With `confirm_days=2` the confirmation drag collapses to near-zero. Over the 10-year window (2016–2026) the realistic simulation trails the idealized by only **−0.2%/yr**; over the 5-year window it outperforms by **+1.6%/yr**.

| Source | Estimated Drag (confirm=2) |
|---|---|
| Confirmation delay (2d entry / 1d exit) | ~−0.1%/yr avg |
| Execution slippage (8 bps/trade) | ~−0.3%/yr |

Realistic expected forward CAGR: **~23–26%** (10yr window basis).

### YTD 2026 Backtest (90 trading days through May 12)

Layer 0 regime for all of 2026 YTD: **NEUTRAL** (score +1, as computed in the discussion).
v3 = v2 when regime is NEUTRAL (default parameters unchanged).

| Strategy | YTD Return | Ann. CAGR | Vol | Sharpe | Max Drawdown |
|---|---|---|---|---|---|
| **APEX v3.0 / v2.0** | **+15.7%** | **+51.2%** | 25.1% | **1.78** | −18.6% |
| APEX v1.0 | +11.0% | +34.5% | 21.9% | 1.46 | −16.7% |
| Buy & Hold VOO | +8.4% | +25.5% | 13.9% | 1.70 | −8.9% |
| Buy & Hold QQQ | +15.5% | +50.4% | 18.6% | 2.29 | −11.7% |
| Buy & Hold TQQQ | +43.4% | +177.6% | 55.3% | 2.12 | −33.5% |

Key 2026 allocation changes (v1 → v2/v3):

| Month | v1 Avg | v2/v3 Avg | Driver |
|---|---|---|---|
| January | 40% | 52% | Dynamic vol target raised cap in clean bull regime |
| February | 5% | 6% | VIX > 25 → hard stop; both versions defensive |
| March | 0% | 0% | Multiple hard stops active |
| April | 14% | 29% | EMA hard stop removed + VIX momentum → earlier re-entry |
| May | 42% | 55% | Dynamic vol target; strong bull score |

---

## Installation & Usage

### Requirements

```bash
Python 3.8+
pip install yfinance pandas numpy matplotlib requests
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

# ── Layer 0: quarterly margin tracker ──
# Run after BEA data release (end of Jan / Apr / Jul / Oct)
python sp500_margin_tracker.py --save     # fetch FRED data + save JSON
python sp500_margin_tracker.py --history  # view historical margin table
# Then update CAPE_RATIO in apex_strategy.py manually

# ── Backtests ──
python backtest_ytd.py --plot             # YTD backtest with chart
python backtest_ytd.py --year 2024 --plot # specific year
python backtest_full.py --plot            # 15-year full backtest
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

## Reading the Output & Taking Action

Run `python apex_strategy.py` and you get six sections in sequence. This guide walks through each one using a real example (May 13 2026) and tells you exactly what to look at and what to do.

---

### Section 1 — Data Block

```
📡 Fetching market data from Yahoo Finance...
   QQQ  : 251 days  (latest: 2026-05-13  $714.71)
   TQQQ : 42 days   (latest: $77.24)
   VIX  : 17.87
   10Y  : 4.48%
```

**What to check:**
- Date is today — if it shows yesterday's date, markets closed early or data is stale. Rerun after 5 PM ET.
- VIX level gives instant context before reading anything else: `< 18` calm, `18–25` normal, `> 25` danger zone.
- 10Y yield context: `< 3.5%` loose, `3.5–4.5%` neutral, `> 4.5%` tight (TACO risk zone).

**Action:** None — data check only.

---

### Section 2 — Layer 0: Macro Regime

```
  Data as of quarter: 2025-10-01  |  CAPE: 37.0
  Margin YoY: -0.302 pp  →  FLAT   (score  0)
  Margin QoQ: +0.520 pp  →  EXPANSION  (score +1)
  CAPE > 35   → penalty -1
  Layer 0 total: 0

  ⚪  REGIME: NEUTRAL
     target_vol=20%  vix_threshold=25  dd_threshold=-10%  max_alloc=100%
```

**What to check:**
- **Regime label** (`🟢 EXPANSION` / `⚪ NEUTRAL` / `🔴 CONTRACTION`) — sets the risk envelope for everything below.
- **Risk parameters** — these four numbers are the active thresholds for this quarter:

| Regime | target_vol | VIX threshold | DD threshold | max TQQQ |
|---|---|---|---|---|
| 🟢 EXPANSION | 22% | 28 | −12% | 100% |
| ⚪ NEUTRAL | 20% | 25 | −10% | 100% |
| 🔴 CONTRACTION | 16% | 22 | −8% | **50%** |

- **"Data as of quarter"** — if this is more than 6 months old, run `python sp500_margin_tracker.py --save` to refresh. Stale Layer 0 data defaults silently to NEUTRAL.

**Action:** Quarterly only. If the regime changed since last quarter, note the new `max_alloc` and `vix_threshold` — they change how aggressively you hold TQQQ and at what VIX you must exit.

---

### Section 3 — Layer 1: Circuit Breakers

```
  ✅ All clear — no hard stops triggered
```
or
```
  🔴 TRIGGERED  →  VOO 100%
     • QQQ drawdown from 6-month high -11.3% < threshold -10%
```

**What to check:**
- `✅ All clear` → proceed to Layer 2.
- `🔴 TRIGGERED` → **stop reading. The final allocation is 0% TQQQ regardless of score.** The conditions below tell you why.

The three circuit breakers (thresholds from Layer 0 regime):

| Breaker | NEUTRAL | EXPANSION | CONTRACTION |
|---|---|---|---|
| QQQ drawdown from 6-month high | > −10% | > −12% | > −8% |
| VIX | > 25 | > 28 | > 22 |
| QQQ below SMA200 | triggered | triggered | triggered |

**Action:** If triggered → exit TQQQ to 100% VOO at Monday 10 AM (no 2-day confirmation needed for exits — 1 day is sufficient). Do not override.

---

### Section 4 — Layer 2: Signal Scoring

```
  EMA Trend        ▲ ++3  ███   ✅ Golden cross  EMA20 675.1 > EMA50 644.1
  SMA200 Regime    ▲ ++2  ██    ✅ Bull regime   QQQ 714.71 > SMA200 608.35
  RSI Momentum     ─  +0  ·     ⚠️  Overbought    RSI 84.0 > 70
  VIX Environment  ▲ ++2  ██    ✅ Calm            VIX 17.9
  20d Momentum     ▲ ++2  ██    ✅ Strong surge   20d +12.1%
  60d Momentum     ▲ ++2  ██    ✅ Strong trend   60d +19.0%
  10Y Rate Change  ▼  -1  █     ⚠️  Rates rising   10Y Δ+0.43%
  Drawdown Depth   ─  +0  ·     ✅ Near highs      0.0% from ATH
  VIX Momentum     ─  +0  ·     ⚪ VIX stable      VIX 5d Δ+0.5
  10Y Rate Level   ─  +0  ·     ⚪ Neutral rates   10Y 4.48%

  TOTAL SCORE:  +10
  BASE ALLOC:   100% TQQQ
```

**What to check:**

**The score number is your primary signal.** Score maps to base TQQQ allocation:

| Score | Base Alloc | Meaning |
|---|---|---|
| ≤ 0 | 0% | Bearish — stay in VOO |
| 1 | 20% | Weak signal — toe in the water |
| 2 | 35% | Cautious bull |
| 3 | 50% | Neutral bull |
| 4 | 65% | Standard bull |
| 5 | 75% | Strong bull |
| 6 | 90% | Very strong bull |
| ≥ 7 | 100% | Ideal conditions |

**Red flags to watch within the score:**
- `EMA Trend ▼ −4` — death cross active. This alone pushes score below zero if other signals are mixed. Usually means 0% TQQQ regardless of other factors.
- `SMA200 ▼ −3` — this also appears as a Layer 1 hard stop, so TQQQ is 0% regardless.
- `RSI ⚠️ Overbought (0)` — not negative, but no bonus. Market may be stretched. Normal in strong rallies.
- `10Y Rate Change ▼ −2` — rates spiked > 0.75 pp in 60 days. Meaningful headwind for Nasdaq.
- `VIX Momentum ▼ −2` — VIX spiking fast. Fear building. Layer 3 will cap allocation further.

**Action:** Score alone does not determine your trade. It sets the *base* — Layer 3 applies the final cap.

---

### Section 5 — Layer 3: Volatility Targeting

```
  TQQQ ann. vol 50%  target=25% [bull]  →  cap = 25%/50% = 51%
  Target portfolio vol: 20%  →  TQQQ cap: 51%
```

**What to check:**
- **`[standard]` vs `[bull]`** — `[bull]` means score ≥ 4 AND VIX < 20, so the vol budget is raised from 20% → 25%. More TQQQ allowed in strong-bull conditions.
- **TQQQ ann. vol** — this is the 15-day realized volatility. Typical ranges:
  - `35–50%` = normal (calm market, standard TQQQ behavior)
  - `50–70%` = elevated (market stress, vol cap bites hard)
  - `> 70%` = crisis (cap will be very low, e.g. 20–30% even with a strong score)
- **Cap = target / TQQQ vol** — the formula is mechanical. If vol drops next week, the cap rises automatically.

**Final allocation = min(base alloc from score, vol cap, max_alloc from Layer 0)**

**Action:** The cap is non-negotiable. If the score says 100% but vol cap says 40%, your allocation is 40%. This is the volatility decay protection working as designed.

---

### Section 6 — Trailing Stop

```
  TQQQ $77.24  |  15d high $77.24  |  stop $71.06  |  +0.0% from high  |  ✅ OK
```
or
```
  TQQQ $68.10  |  15d high $77.24  |  stop $71.06  |  -12.0% from high  |  🔴 TRIGGERED
```

**What to check:**
- `% from high` — your real-time buffer before the stop fires:
  - `0% to −4%` — comfortable, no action
  - `−4% to −7%` — watch daily; the next down day could fire the stop
  - `−7% to −8%` — **stop is about to fire.** Consider reducing exposure proactively.
  - `< −8%` — **🔴 TRIGGERED. Exit to VOO today. No confirmation needed.**
- **This check is daily, not weekly.** The stop can fire Tuesday even if Sunday's signal was bullish.

**Action:** If triggered — log into Fidelity immediately, exit TQQQ position to VOO with a limit order. Do not wait for Sunday. This overrides everything else.

---

### Section 7 — Final Recommendation

```
  ⭐  RECOMMENDED ALLOCATION  (NORMAL SIGNAL)

     TQQQ  51%   ████████████████████
     VOO   49%   ░░░░░░░░░░░░░░░░░░░

  🟢 BULLISH   — Standard bull allocation.

  ⚠ Confirm signal holds for 2 consecutive days before acting.
```

**What to check:**
- **The allocation percentage** — this is what you are targeting.
- **The reason label:**

| Label | Meaning | Act? |
|---|---|---|
| `NORMAL SIGNAL` | All layers computed normally | Yes, after 2-day confirm |
| `LAYER 1 CIRCUIT BREAKER` | Hard stop fired | Yes — exit immediately (1-day confirm) |
| `TRAILING STOP` | TQQQ dropped 8% from 15d high | Yes — exit today, no confirm needed |
| `LAYER 0 MAX_ALLOC (CONTRACTION)` | Regime cap limiting allocation | Yes, capped at 50% |

- **Interpretation line:**

| Color | Label | What it means |
|---|---|---|
| 🟢 | AGGRESSIVE / BULLISH | All signals aligned. Hold full vol-capped allocation. |
| 🟡 | MODERATE / CAUTIOUS | Mixed signals. Smaller position, watch closely. |
| 🔴 | DEFENSIVE | Score ≤ 0 or circuit breaker. 100% VOO. |

**Action:** See the Decision Flow below.

---

### Section 8 — YTD Check (context only)

```
  TQQQ YTD:  +47.8%   (current $77.24)
  VOO  YTD:  +9.0%    (current $682.41)
  QQQ  YTD:  +16.7%
  TQQQ max drawdown YTD:  -33.5%
  VOO  max drawdown YTD:  -8.9%
```

**What to check:** Context only — not used in any calculation. Use TQQQ's YTD max drawdown as a gut check: if it's already down 30%+ YTD, the strategy is likely in or near a hard stop condition.

**Action:** None — informational only.

---

### Decision Flow

Read the output top to bottom and stop at the first decisive condition:

```
1. Layer 1 triggered?
   YES → Exit to 100% VOO on Monday (1-day confirm). STOP.
   NO  → continue

2. Trailing stop triggered?
   YES → Exit to 100% VOO TODAY (no confirm needed). STOP.
   NO  → continue

3. Score ≤ 0?
   YES → Hold 100% VOO. No trade needed if already in VOO. STOP.
   NO  → continue

4. Final allocation within 5% of current holding?
   YES → No trade. Recheck next Sunday. STOP.
   NO  → continue

5. Is this the 2nd consecutive day with the same signal direction?
   NO  → Wait. Do not trade yet.
   YES → Execute Monday 10 AM with a limit order.
```

**Practical threshold:** Only rebalance if the new allocation differs from your current by **more than 5%**. Small weekly drift (e.g. 51% → 53%) is noise from vol cap changes and does not warrant a trade.

---

### Reading the GitHub Actions Email

The automated email arrives at three points each weekday. The subject line tells you everything at a glance:

```
🟢 APEX ⚡ EXECUTION WINDOW 2026-05-14 Mon | 51% TQQQ / 49% VOO | Score +10 | NEUTRAL | BULLISH
🟡 APEX Pre-Market Check 2026-05-14 Mon | 35% TQQQ / 65% VOO | Score +3 | NEUTRAL | MODERATE
🔴 APEX After-Close Check 2026-05-14 Mon | 0% TQQQ / 100% VOO | Score -2 | NEUTRAL | DEFENSIVE
🚨🚨🚨 APEX After-Close Check 2026-05-14 Mon | 0% TQQQ / 100% VOO | Score +4 | NEUTRAL | EMERGENCY EXIT — ACT NOW
```

**Email timing and what to do:**

| Email label | When it arrives | What to do |
|---|---|---|
| `Weekly Signal` | Sunday ~8 PM ET | Read the full body. Plan Monday's trade. |
| `Pre-Market Confirm` | Monday 9 AM ET | Check if allocation matches Sunday. If yes, prepare limit order for 10 AM. |
| `⚡ EXECUTION WINDOW` | Monday 10 AM ET | Execute if: (a) matches Sunday's signal, (b) change > 5%, (c) no VIX spike since Sunday. |
| `Pre-Market Check` | Tue–Fri 9 AM ET | Skim subject line. Only open body if signal changed or score dropped significantly. |
| `After-Close Check` | 4:15 PM ET daily | Check for `🚨` in subject. If no emergency, archive. |

**Emergency email:** If subject starts with `🚨🚨🚨`, open immediately. The body will show a banner like:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  *** EMERGENCY EXIT — ACT IMMEDIATELY ***
  Trailing stop triggered.
  Log in to Fidelity NOW and exit TQQQ → VOO.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Log into Fidelity, place a limit order to sell your entire TQQQ position and buy VOO. Do not wait for Sunday.

---

## User Guide

### CONFIG Reference

```python
# Layer 0 constants (in apex_strategy.py, outside CONFIG)
CAPE_RATIO  = 37.0    # Shiller CAPE — update manually each quarter
MARGIN_SNAP = "sp500_output/sp500_margin_latest.json"  # from sp500_margin_tracker.py

CONFIG = {
    # Assets
    "signal_ticker"   : "QQQ",   # signal source
    "growth_asset"    : "TQQQ",  # leveraged ETF
    "stable_asset"    : "VOO",   # defensive ETF

    # Layer 1: Hard circuit breakers (defaults; overridden by Layer 0 regime)
    "dd_threshold"    : -10.0,   # QQQ 6-month-high drawdown → force VOO
    "vix_threshold"   : 25.0,    # VIX level → force VOO

    # Layer 2: Score → allocation map
    "alloc_map"       : {0:0.00, 1:0.20, 2:0.35, 3:0.50,
                         4:0.65, 5:0.75, 6:0.90},  # ≥7 → 1.00

    # Layer 3: Dynamic volatility targeting (base overridden by Layer 0 regime)
    "target_vol"      : 0.20,    # base vol budget (NEUTRAL regime)
    "target_vol_bull" : 0.25,    # +5pp in bull (score ≥ 4 AND VIX < 20)
    "dynamic_vol"     : True,    # enable in-regime bull vol adjustment
    "vol_window"      : 15,      # days for realized vol calculation

    # Trailing stop
    "trail_window"    : 15,      # trailing high lookback (days)
    "trail_pct"       : 0.92,    # exit if price < high × 0.92 (−8%)

    # Execution
    "confirm_days"    : 2,       # days to confirm signal before acting
    "exec_delay"      : 1,       # T+1 execution
}
```

### Weekly Workflow

```
Sunday 8–9 PM
  □ Run: python apex_strategy.py
  □ Record: Layer 0 regime, score, final allocation, VIX, vol cap
  □ Note: if regime changed this quarter, note new risk parameters

Monday 9:30–10:00 AM  ← Do NOT trade (widest spreads at open)

Monday 10:00 AM+
  □ Confirm signal matches Sunday's (2-day confirmation check)
  □ Check if VIX has moved significantly since Sunday close
  □ If allocation change > 5%: execute in Fidelity
    Fidelity → Trade → ETFs → Enter dollar amount (not share count)
  □ Use limit orders, not market orders

Daily (2 minutes)
  □ Check: TQQQ current price vs 15-day high × 0.92
  □ If triggered → exit to VOO immediately, do not wait for Sunday
  □ Watch for VIX crossing the regime threshold (25 NEUTRAL, 22 CONTRACTION, 28 EXPANSION)
```

### Quarterly Workflow (Layer 0 update)

```
End of January / April / July / October (after BEA advance estimate)

  □ Run: python sp500_margin_tracker.py --save
  □ Check the output: direction_yoy, direction_qoq, regime score
  □ Look up current Shiller CAPE at https://www.multpl.com/shiller-pe
  □ Update CAPE_RATIO in apex_strategy.py (line near top of file)
  □ Run: python apex_strategy.py  to see the new Layer 0 regime
  □ If regime changed (e.g. NEUTRAL → CONTRACTION):
      - New VIX threshold, DD threshold, and max TQQQ% take effect immediately
      - No need to re-run backtests; regime change is a risk-management override
```

**Next Layer 0 update: August 2026** (Q2 2026 BEA advance estimate)

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
| `confirm_days` | 3 | **2** | 1 |

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
apex_strategy.py          Main strategy file — run this for Sunday signals
sp500_margin_tracker.py   Layer 0 data: fetches FRED CP/GDP, saves margin snapshot
backtest_ytd.py           YTD backtest engine (v1 vs v2 vs v3, with --plot)
backtest_full.py          15-year full backtest (v1 vs v2 vs v3 vs benchmarks, --plot)
sp500_output/
  sp500_margin_latest.json  Layer 0 input — generated by sp500_margin_tracker.py
README.md                 This document
```

---

*APEX Strategy v3.0 · Python 3.8+ · Roth IRA only · Not financial advice*
