"""
Investment Strategy Email Builder
==================================
Generates a single combined HTML email for both APEX and NOVA Bear Monitor.
Called by the GitHub Action to produce the email body and subject.

Usage (from notifications/ or repo root):
    python email_builder.py           → prints JSON: {"subject": "...", "body": "<html>..."}
"""

import sys
import os
import json
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "strategy"))

from apex_strategy import run_apex
from nova_strategy import run_nova_bear_monitor
from portfolio_tracker import run_portfolio_tracker, _TOP_N


# ── Color palette ──────────────────────────────────────────────────────────
C = {
    "apex_blue":    "#1565C0",
    "apex_med":     "#1976D2",
    "apex_light":   "#E3F2FD",
    "nova_purple":  "#4A148C",
    "nova_light":   "#F3E5F5",
    "green_dark":   "#1B5E20",
    "green_med":    "#2E7D32",
    "green_bg":     "#E8F5E9",
    "green_bar":    "#43A047",
    "green_voo":    "#388E3C",
    "red_dark":     "#B71C1C",
    "red_bg":       "#FFEBEE",
    "red_bar":      "#E53935",
    "orange_dark":  "#BF360C",
    "orange_bg":    "#FBE9E7",
    "orange_bar":   "#FF5722",
    "yellow_bg":    "#FFFDE7",
    "yellow_dark":  "#F57F17",
    "gray_bg":      "#F5F5F5",
    "gray_light":   "#FAFAFA",
    "gray_border":  "#E0E0E0",
    "gray_bar":     "#BDBDBD",
    "gray_text":    "#757575",
    "white":        "#FFFFFF",
    "black":        "#212121",
    "teal_dark":    "#004D40",
    "teal_med":     "#00695C",
    "teal_light":   "#E0F2F1",
}

FONT = "font-family: -apple-system, 'Segoe UI', Arial, sans-serif;"


# ── Context helper ─────────────────────────────────────────────────────────

def run_context() -> str:
    now = datetime.now(timezone.utc)
    dow, hour = now.weekday(), now.hour
    if dow == 0 and hour < 3:   return "Weekly Signal"
    if dow == 0 and hour == 13: return "Pre-Market Confirm"
    if hour == 13:              return "Pre-Market Check"
    return "After-Close Check"


# ── Subject builder ────────────────────────────────────────────────────────

def build_subject(apex: dict, nova: dict, context: str) -> str:
    tqqq    = apex.get("tqqq_pct", 0)
    score   = apex.get("score", 0)
    regime  = apex.get("regime", "NEUTRAL")
    circuit = apex.get("circuit_triggered", False)
    trail   = apex.get("trail_stop_fired", False)

    nova_level = nova.get("confirmed_level", 0)
    nova_label = nova.get("confirmed_label", "All Clear")

    if circuit or trail:
        apex_icon, apex_status = "🚨", "EMERGENCY EXIT"
    elif tqqq == 0:    apex_icon, apex_status = "🔴", "DEFENSIVE"
    elif tqqq >= 0.60: apex_icon, apex_status = "🟢", "BULLISH"
    elif tqqq >= 0.30: apex_icon, apex_status = "🟡", "MODERATE"
    else:              apex_icon, apex_status = "🟡", "CAUTIOUS"

    nova_icon = {0:"🟢", 1:"🟡", 2:"🟠", 3:"🔴", 4:"⚫"}.get(nova_level, "⚪")
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %a")

    return (
        f"{apex_icon} APEX {context} {date_str} | "
        f"{int(tqqq*100)}%T {int((1-tqqq)*100)}%V "
        f"Score{score:+} {regime} {apex_status}"
        f"  ‖  "
        f"{nova_icon} NOVA L{nova_level} {nova_label}"
    )


# ── HTML primitives ────────────────────────────────────────────────────────

def _badge(text: str, bg: str, fg: str = "#fff", radius: str = "12px") -> str:
    return (f'<span style="{FONT} background:{bg}; color:{fg}; '
            f'padding:3px 10px; border-radius:{radius}; font-size:12px; '
            f'font-weight:600; white-space:nowrap;">{text}</span>')


def _pill(text: str, bg: str, fg: str = "#fff") -> str:
    return (f'<span style="{FONT} background:{bg}; color:{fg}; '
            f'padding:2px 8px; border-radius:4px; font-size:11px; '
            f'font-weight:600;">{text}</span>')


def _section_divider(cols: int = 3) -> str:
    return f'<tr><td colspan="{cols}" style="height:1px;background:{C["gray_border"]};padding:0;"></td></tr>'


def _score_bar(score: int, max_score: int = 4) -> str:
    magnitude  = min(abs(score), max_score)
    bar_color  = C["green_bar"] if score > 0 else (C["red_bar"] if score < 0 else C["gray_bar"])
    text_color = bar_color
    filled = "".join(
        f'<span style="display:inline-block;width:10px;height:11px;'
        f'background:{bar_color};margin:0 1px;border-radius:2px;"></span>'
        for _ in range(magnitude)
    )
    empty  = "".join(
        f'<span style="display:inline-block;width:10px;height:11px;'
        f'background:{C["gray_border"]};margin:0 1px;border-radius:2px;"></span>'
        for _ in range(max_score - magnitude)
    )
    sign_str = f"+{score}" if score > 0 else str(score)
    sign = f'<span style="font-weight:700;color:{text_color};margin-right:5px;min-width:20px;display:inline-block;text-align:right;">{sign_str}</span>'
    return f'{sign}{filled}{empty}'


def _risk_bar(score: int, max_score: int = 3) -> str:
    """Bear risk bar: positive score = red (risk), negative = green (safe)."""
    magnitude = min(abs(score), max_score)
    bar_color = C["red_bar"] if score > 0 else (C["green_bar"] if score < 0 else C["gray_bar"])
    filled = "".join(
        f'<span style="display:inline-block;width:10px;height:11px;'
        f'background:{bar_color};margin:0 1px;border-radius:2px;"></span>'
        for _ in range(magnitude)
    )
    empty = "".join(
        f'<span style="display:inline-block;width:10px;height:11px;'
        f'background:{C["gray_border"]};margin:0 1px;border-radius:2px;"></span>'
        for _ in range(max_score - magnitude)
    )
    sign_str = f"+{score}" if score > 0 else str(score)
    sign = f'<span style="font-weight:700;color:{bar_color};margin-right:5px;min-width:20px;display:inline-block;text-align:right;">{sign_str}</span>'
    return f'{sign}{filled}{empty}'


def _alloc_bar(pct_left: float, label_left: str, color_left: str,
               label_right: str, color_right: str, height: int = 22) -> str:
    pct_right = 1 - pct_left
    w_l = max(2, round(pct_left * 100))
    w_r = max(2, round(pct_right * 100))
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="{FONT} font-size:11px;color:{color_left};padding-bottom:5px;font-weight:600;">
          {label_left}&nbsp; <strong>{int(pct_left*100)}%</strong>
        </td>
        <td style="{FONT} font-size:11px;color:{color_right};padding-bottom:5px;
                   text-align:right;font-weight:600;">
          <strong>{int(pct_right*100)}%</strong>&nbsp; {label_right}
        </td>
      </tr>
      <tr>
        <td colspan="2">
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-radius:6px;overflow:hidden;">
            <tr>
              <td width="{w_l}%" height="{height}"
                  style="background:{color_left};border-radius:6px 0 0 6px;"></td>
              <td width="{w_r}%" height="{height}"
                  style="background:{color_right};border-radius:0 6px 6px 0;"></td>
            </tr>
          </table>
        </td>
      </tr>
    </table>"""


# ── APEX HTML builder ──────────────────────────────────────────────────────

APEX_DIM_ORDER = [
    ("ema_trend",    "EMA Trend",        3),
    ("sma200",       "SMA200 Regime",    2),
    ("rsi",          "RSI(14)",          2),
    ("vix",          "VIX Level",        3),
    ("mom20",        "20d Momentum",     2),
    ("mom60",        "60d Momentum",     2),
    ("tnx",          "10Y Rate Change",  2),
    ("drawdown",     "Drawdown",         3),
    ("vix_momentum", "VIX Momentum",     2),
    ("tnx_level",    "10Y Rate Level",   2),
]


def _apex_status_style(apex: dict):
    tqqq    = apex.get("tqqq_pct", 0)
    circuit = apex.get("circuit_triggered", False)
    trail   = apex.get("trail_stop_fired", False)
    if circuit or trail:
        return C["red_dark"], C["red_bg"], "🚨 EMERGENCY EXIT", C["red_dark"]
    if tqqq == 0:    return C["red_dark"],   C["red_bg"],    "🔴 DEFENSIVE",  C["red_dark"]
    if tqqq >= 0.60: return C["green_dark"], C["green_bg"], "🟢 BULLISH",    C["green_dark"]
    if tqqq >= 0.30: return C["apex_blue"],  C["apex_light"],"🟡 MODERATE",   C["apex_blue"]
    return C["orange_dark"], C["orange_bg"], "🟡 CAUTIOUS", C["orange_dark"]


def build_apex_html(apex: dict) -> str:
    tqqq    = apex.get("tqqq_pct", 0)
    score   = apex.get("score", 0)
    regime  = apex.get("regime", "NEUTRAL")
    base    = apex.get("base_alloc", tqqq)
    vol_cap = apex.get("vol_cap", 1.0)
    tqqq_vol= apex.get("tqqq_vol", 0) or 0
    reason  = apex.get("reason", "")
    circuit = apex.get("circuit_triggered", False)
    trail   = apex.get("trail_stop_fired", False)
    cb_conds= apex.get("circuit_conditions", [])
    scores  = apex.get("signal_scores", {})
    notes   = apex.get("signal_notes",  {})

    regime_icons  = {"EXPANSION": "🟢", "NEUTRAL": "⚪", "CONTRACTION": "🔴"}
    regime_icon   = regime_icons.get(regime, "⚪")
    regime_colors = {
        "EXPANSION":   (C["green_bg"],  C["green_dark"]),
        "NEUTRAL":     (C["gray_bg"],   C["black"]),
        "CONTRACTION": (C["red_bg"],    C["red_dark"]),
    }
    r_bg, r_txt = regime_colors.get(regime, (C["gray_bg"], C["black"]))

    fg, bg, status_label, text_color = _apex_status_style(apex)

    n_pos = sum(1 for v in scores.values() if v > 0)
    n_neg = sum(1 for v in scores.values() if v < 0)
    n_neu = sum(1 for v in scores.values() if v == 0)

    rows = []

    # ── Section header ────────────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:linear-gradient(135deg,{C['apex_blue']},{C['apex_med']});
          padding:18px 24px; border-radius:8px 8px 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:17px; font-weight:700; color:#fff;">
                📈 APEX Strategy — TQQQ / VOO
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                Adaptive Position EXecution Protocol v3.0
              </span>
            </td>
            <td style="text-align:right;white-space:nowrap;">
              <span style="{FONT} font-size:22px; font-weight:800; color:#fff;">
                {int(tqqq*100)}% TQQQ
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                {int((1-tqqq)*100)}% VOO
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Status + regime row ───────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:{bg}; padding:12px 24px;
          border-left:4px solid {fg};">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:15px; font-weight:700; color:{text_color};">
                {status_label}
              </span>
              &nbsp;&nbsp;
              <span style="{FONT} font-size:13px; color:{C['gray_text']};">
                Score {score:+d} &nbsp;|&nbsp; {reason}
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:12px; padding:3px 8px;
                     background:{r_bg}; color:{r_txt}; border-radius:4px; font-weight:600;">
                {regime_icon} {regime}
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Vol targeting info ────────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:{C['gray_light']}; padding:8px 24px;
          border-bottom:1px solid {C['gray_border']};">
        <span style="{FONT} font-size:11px; color:{C['gray_text']};">
          TQQQ realized vol: <strong>{tqqq_vol:.0%}</strong>
          &nbsp;·&nbsp; vol-cap: <strong>{vol_cap:.0%}</strong>
          &nbsp;·&nbsp; pre-cap base: <strong>{base:.0%}</strong>
        </span>
      </td>
    </tr>""")

    # ── Emergency CB conditions ───────────────────────────────────────────
    if (circuit or trail) and cb_conds:
        cond_html = "".join(f"<li style='margin:2px 0;'>{c}</li>" for c in cb_conds)
        rows.append(f"""
    <tr>
      <td colspan="3" style="background:{C['red_bg']}; padding:10px 24px;
          border-left:4px solid {C['red_dark']};">
        <span style="{FONT} font-size:11px; font-weight:700; color:{C['red_dark']};">
          TRIGGERED CONDITIONS:
        </span>
        <ul style="{FONT} font-size:11px; color:{C['red_dark']}; margin:4px 0 0;">
          {cond_html}
        </ul>
      </td>
    </tr>""")

    # ── Signal table header ───────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="padding:14px 24px 6px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:11px; font-weight:700; color:{C['gray_text']};
                    text-transform:uppercase; letter-spacing:0.5px;">
                Layer 2 — Signal Scoring (10 dimensions)
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:11px; color:{C['green_dark']};">✓ {n_pos} bullish</span>
              &nbsp;
              <span style="{FONT} font-size:11px; color:{C['red_dark']};">✗ {n_neg} bearish</span>
              &nbsp;
              <span style="{FONT} font-size:11px; color:{C['gray_text']};">— {n_neu} neutral</span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Signal rows ───────────────────────────────────────────────────────
    for key, label, max_s in APEX_DIM_ORDER:
        sc   = scores.get(key, 0)
        note = notes.get(key, "") if notes else ""
        row_bg = (C["green_bg"] if sc > 0
                  else C["red_bg"] if sc < 0
                  else C["white"])
        rows.append(f"""
    <tr style="background:{row_bg};">
      <td style="padding:5px 24px 5px 28px; {FONT} font-size:12px;
                 color:{C['black']}; width:150px; white-space:nowrap;">
        {label}
      </td>
      <td style="padding:5px 10px; white-space:nowrap;">
        {_score_bar(sc, max_s)}
      </td>
      <td style="padding:5px 14px 5px 4px; {FONT} font-size:11px;
                 color:{C['gray_text']}; width:100%;">
        {note}
      </td>
    </tr>""")

    # ── Score total ───────────────────────────────────────────────────────
    rows.append(_section_divider(3))
    score_color = (C["green_dark"] if score > 5 else
                   C["red_dark"]   if score < -3 else C["apex_blue"])
    rows.append(f"""
    <tr style="background:{C['gray_bg']};">
      <td style="padding:9px 24px 9px 28px; {FONT} font-size:12px;
                 font-weight:700; color:{C['black']}; width:150px;">
        Total Score
      </td>
      <td style="padding:9px 10px; {FONT} font-size:16px; font-weight:800;
                 color:{score_color}; white-space:nowrap;">
        {score:+d}
      </td>
      <td style="padding:9px 14px; {FONT} font-size:12px; color:{C['gray_text']};">
        → base allocation <strong>{base:.0%} TQQQ</strong>
        {f'→ vol-capped to <strong>{tqqq:.0%}</strong>' if abs(tqqq - base) > 0.01 else ''}
      </td>
    </tr>""")

    # ── Allocation bar ────────────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="padding:14px 24px 18px;">
        {_alloc_bar(tqqq, "TQQQ", C["apex_blue"], "VOO", C["green_voo"], height=24)}
        <div style="{FONT} font-size:10px; color:{C['gray_text']}; margin-top:8px;
                    border-top:1px solid {C['gray_border']}; padding-top:7px;">
          ⚠&nbsp; Confirm same signal for 2 consecutive days before acting
          &nbsp;·&nbsp; Execute T+1 (10 AM ET)
          &nbsp;·&nbsp; Roth IRA only
        </div>
      </td>
    </tr>""")

    return "\n".join(rows)


# ── NOVA HTML builder ──────────────────────────────────────────────────────

NOVA_DIM_ORDER = [
    ("mom60",    "SOXX 60d Momentum",  3),
    ("ema_major","EMA50/200 Trend",    2),
    ("rel_str",  "SOXX vs SPY 60d",   2),
    ("vix",      "VIX Level",         3),
    ("vix_5d",   "VIX 5-day Change",  2),
    ("dd126",    "SOXX 6m Drawdown",  3),
    ("nvda60",   "NVDA 60d Momentum", 2),
]

_NOVA_SEVERITY = {
    0: (C["green_dark"],  C["green_bg"],  "🟢 Level 0 — ALL CLEAR"),
    1: (C["yellow_dark"], C["yellow_bg"], "🟡 Level 1 — WATCH"),
    2: (C["orange_dark"], C["orange_bg"], "🟠 Level 2 — CAUTION"),
    3: (C["red_dark"],    C["red_bg"],    "🔴 Level 3 — BEAR ALERT"),
    4: (C["black"],       "#E0E0E0",      "⚫ Level 4 — EXTREME"),
}

_NOVA_HEADER_COLOR = {
    0: C["nova_purple"],
    1: C["yellow_dark"],
    2: C["orange_dark"],
    3: C["red_dark"],
    4: C["black"],
}


def build_nova_html(nova: dict) -> str:
    level   = nova.get("confirmed_level", 0)
    label   = nova.get("confirmed_label", "All Clear")
    score   = nova.get("today_score", 0)
    regime  = nova.get("regime", "?")
    days    = nova.get("days_at_level", 0)
    soxx_p  = nova.get("soxx_price", 0)
    soxx_dd = nova.get("soxx_dd_ath", 0)
    raw_lv  = nova.get("raw_level", 0)
    recovery= nova.get("recovery", [])
    signals = nova.get("signal_scores", {})
    notes   = nova.get("signal_notes", {}) or {}

    fg, bg, status_label = _NOVA_SEVERITY.get(level, _NOVA_SEVERITY[0])
    hdr_color = _NOVA_HEADER_COLOR.get(level, C["nova_purple"])

    n_risk  = sum(1 for v in signals.values() if v > 0)
    n_safe  = sum(1 for v in signals.values() if v < 0)
    n_neu   = sum(1 for v in signals.values() if v == 0)

    rows = []

    # ── Section header ────────────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:{hdr_color}; padding:18px 24px;
          border-radius:8px 8px 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:17px; font-weight:700; color:#fff;">
                🔬 NOVA Bear Monitor — Semiconductor
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                Sector bear severity alert · you decide the action
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:22px; font-weight:800; color:#fff;">
                Level {level}
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                {label}
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Severity status ───────────────────────────────────────────────────
    days_note = f"  ·  at this level {days}/7 days" if days else ""
    raw_note  = (f"  ·  raw today: Level {raw_lv}"
                 if raw_lv != level else "")
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:{bg}; padding:12px 24px;
          border-left:4px solid {fg};">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:15px; font-weight:700; color:{fg};">
                {status_label}
              </span>
              <br>
              <span style="{FONT} font-size:11px; color:{C['gray_text']};">
                Risk score <strong>{score:+d}</strong>
                &nbsp;·&nbsp; Regime: <strong>{regime}</strong>
                &nbsp;·&nbsp; SOXX <strong>${soxx_p:.2f}</strong>
                  ({soxx_dd:+.1f}% ATH)
                {days_note}{raw_note}
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap; padding-left:12px;">
              <span style="{FONT} font-size:10px; color:{C['gray_text']};">
                3-day confirm<br>(L4: 5-day)
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Signal table header ───────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="padding:14px 24px 6px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:11px; font-weight:700; color:{C['gray_text']};
                    text-transform:uppercase; letter-spacing:0.5px;">
                7-Signal Bear Risk Breakdown &nbsp;
                <span style="font-weight:400;">(positive risk score = more bearish)</span>
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:11px; color:{C['red_dark']};">▲ {n_risk} risk</span>
              &nbsp;
              <span style="{FONT} font-size:11px; color:{C['green_dark']};">▼ {n_safe} safe</span>
              &nbsp;
              <span style="{FONT} font-size:11px; color:{C['gray_text']};">— {n_neu} neutral</span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    # ── Signal rows ───────────────────────────────────────────────────────
    for key, label_s, max_s in NOVA_DIM_ORDER:
        sc   = signals.get(key, 0)
        note = notes.get(key, "")
        row_bg = (C["red_bg"]   if sc > 0
                  else C["green_bg"] if sc < 0
                  else C["white"])
        rows.append(f"""
    <tr style="background:{row_bg};">
      <td style="padding:5px 24px 5px 28px; {FONT} font-size:12px;
                 color:{C['black']}; width:165px; white-space:nowrap;">
        {label_s}
      </td>
      <td style="padding:5px 10px; white-space:nowrap;">
        {_risk_bar(sc, max_s)}
      </td>
      <td style="padding:5px 14px 5px 4px; {FONT} font-size:11px;
                 color:{C['gray_text']}; width:100%;">
        {note}
      </td>
    </tr>""")

    # ── Risk total ────────────────────────────────────────────────────────
    rows.append(_section_divider(3))
    risk_color = (fg if level > 0 else C["green_dark"])
    rows.append(f"""
    <tr style="background:{C['gray_bg']};">
      <td style="padding:9px 24px 9px 28px; {FONT} font-size:12px;
                 font-weight:700; color:{C['black']}; width:165px;">
        Total Risk Score
      </td>
      <td style="padding:9px 10px; {FONT} font-size:16px; font-weight:800;
                 color:{risk_color}; white-space:nowrap;">
        {score:+d}
      </td>
      <td style="padding:9px 14px; {FONT} font-size:12px; color:{C['gray_text']};">
        → Confirmed <strong>Level {level} ({label})</strong>
        &nbsp;·&nbsp; Primary action: <strong>Level 2</strong> (82% accuracy)
      </td>
    </tr>""")

    # ── Recovery conditions ───────────────────────────────────────────────
    if level > 0 and recovery:
        black = C["black"]
        rec_items = "".join(
            f"<li style='{FONT} font-size:11px; color:{black}; margin:3px 0;'>{r}</li>"
            for r in recovery
        )
        rows.append(_section_divider(3))
        rows.append(f"""
    <tr>
      <td colspan="3" style="background:{C['gray_light']}; padding:10px 24px;">
        <span style="{FONT} font-size:11px; font-weight:700; color:{C['gray_text']};
              text-transform:uppercase; letter-spacing:0.5px;">
          Recovery conditions (to clear to Level {max(0, level-1)}):
        </span>
        <ul style="margin:6px 0 2px; padding-left:18px;">
          {rec_items}
        </ul>
      </td>
    </tr>""")

    # ── Action guidance ───────────────────────────────────────────────────
    action_map = {
        0: ("No action needed.", C["green_bg"], C["green_dark"]),
        1: ("Monitor positions. Note which signals are weakening — no urgent action yet.",
            C["yellow_bg"], C["yellow_dark"]),
        2: ("Consider reducing SOXL/USD exposure. "
            "Best accuracy tier (82% in 5y backtest) — highest signal confidence.",
            C["orange_bg"], C["orange_dark"]),
        3: ("Strong case for rotating to SOXX or SGOV. "
            "May fire at market bottoms — act if you haven't reduced at Level 2.",
            C["red_bg"], C["red_dark"]),
        4: ("Maximum risk conditions — capital preservation priority. "
            "Verify 5-day confirmation before acting (high false-alarm rate at bottoms).",
            "#E8E8E8", C["black"]),
    }
    act_text, act_bg, act_color = action_map.get(level, action_map[0])
    rows.append(_section_divider(3))
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:{act_bg}; padding:12px 24px 16px;
          border-left:4px solid {act_color};">
        <span style="{FONT} font-size:11px; font-weight:700; color:{act_color};
              text-transform:uppercase; letter-spacing:0.5px;">
          Your Decision:
        </span>
        <span style="{FONT} font-size:12px; color:{C['black']};">
          &nbsp;{act_text}
        </span>
        <br>
        <span style="{FONT} font-size:10px; color:{C['gray_text']}; margin-top:5px;
              display:block;">
          ⚠&nbsp; Alert signal only — you decide the action. Not financial advice.
        </span>
      </td>
    </tr>""")

    return "\n".join(rows)


# ── Portfolio HTML builder ─────────────────────────────────────────────────

def _quarter_label(report_date: str) -> str:
    """Convert '2025-12-31' → 'Q4 2025'."""
    try:
        d = datetime.strptime(report_date, "%Y-%m-%d")
        q = (d.month - 1) // 3 + 1
        return f"Q{q} {d.year}"
    except (ValueError, TypeError):
        return report_date or ""


def _fmt_m(value_m: float) -> str:
    if value_m >= 1000:
        return f"${value_m/1000:.1f}B"
    return f"${value_m:.0f}M"


def build_filings_digest_html(portfolio: dict) -> str:
    """Compact one-row-per-filer summary card. Placed above the full diff."""
    filers = portfolio.get("filers", [])
    if not filers:
        return ""

    rows = []

    rows.append(f"""
    <tr>
      <td colspan="5" style="background:{C['teal_dark']}; padding:12px 24px 10px;
          border-radius:8px 8px 0 0;">
        <span style="{FONT} font-size:14px; font-weight:700; color:#fff;">
          📬 New 13F Filings
        </span>
        <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.70); margin-left:10px;">
          {len(filers)} filer{'s' if len(filers) != 1 else ''} filed within 60 days
          &nbsp;·&nbsp; quarterly SEC disclosures
        </span>
      </td>
    </tr>
    <tr style="background:{C['teal_light']};">
      <td style="{FONT} font-size:10px; font-weight:700; color:{C['teal_dark']};
                 padding:5px 8px 5px 24px; text-transform:uppercase; letter-spacing:0.5px;">
        Filer
      </td>
      <td style="{FONT} font-size:10px; font-weight:700; color:{C['teal_dark']};
                 padding:5px 8px; text-transform:uppercase; letter-spacing:0.5px; white-space:nowrap;">
        Period · Filed
      </td>
      <td style="{FONT} font-size:10px; font-weight:700; color:{C['teal_dark']};
                 padding:5px 8px; text-transform:uppercase; letter-spacing:0.5px; text-align:right;">
        AUM
      </td>
      <td style="{FONT} font-size:10px; font-weight:700; color:{C['teal_dark']};
                 padding:5px 8px; text-transform:uppercase; letter-spacing:0.5px;">
        Key Moves (vs prior quarter)
      </td>
      <td style="{FONT} font-size:10px; font-weight:700; color:{C['teal_dark']};
                 padding:5px 14px 5px 8px; text-transform:uppercase; letter-spacing:0.5px; text-align:right;">
        Data
      </td>
    </tr>""")

    for i, f in enumerate(filers):
        chg      = f.get("changes", {})
        period   = _quarter_label(f.get("report_date", ""))
        filed    = f.get("filed_date", "")
        days_ago = f.get("days_ago", 0)
        total_m  = f.get("total_value_m", 0)

        # Pick up to 3 highlights: opened → closed → increased → decreased
        highlights = []
        for cat, color, symbol in [
            ("opened",    C["green_dark"],  "+"),
            ("closed",    C["red_dark"],    "−"),
            ("increased", C["apex_blue"],   "↑"),
            ("decreased", C["orange_dark"], "↓"),
        ]:
            pos = chg.get(cat, [])
            if pos and len(highlights) < 3:
                p  = pos[0]
                nm = p["name"][:22]
                pct_s = f"&thinsp;{p['pct']:+.0%}" if "pct" in p else ""
                highlights.append(
                    f'<span style="color:{color}; white-space:nowrap;">'
                    f'{symbol}{nm}&thinsp;{_fmt_m(p["value_m"])}{pct_s}</span>'
                )
        if not highlights:
            highlights = [f'<span style="color:{C["gray_text"]};">no changes</span>']

        ok       = f.get("integrity_ok")
        int_icon = "✓" if ok else ("⚠" if ok is False else "–")
        int_col  = C["green_dark"] if ok else (C["orange_dark"] if ok is False else C["gray_text"])
        row_bg   = C["white"] if i % 2 == 0 else C["gray_light"]

        rows.append(f"""
    <tr style="background:{row_bg}; border-top:1px solid {C['gray_border']};">
      <td style="{FONT} font-size:12px; font-weight:700; color:{C['teal_dark']};
                 padding:7px 8px 7px 24px; white-space:nowrap;">
        {f['label']}
        <br>
        <span style="font-size:10px; font-weight:400; color:{C['gray_text']};">
          {f['theme'].split('·')[0].strip()}
        </span>
      </td>
      <td style="{FONT} font-size:11px; color:{C['black']}; padding:7px 8px; white-space:nowrap;">
        <strong>{period}</strong><br>
        <span style="color:{C['gray_text']};">filed&nbsp;{filed}&nbsp;({days_ago}d ago)</span>
      </td>
      <td style="{FONT} font-size:13px; font-weight:700; color:{C['teal_dark']};
                 padding:7px 8px; text-align:right; white-space:nowrap;">
        {_fmt_m(total_m)}
      </td>
      <td style="{FONT} font-size:11px; padding:7px 8px; line-height:1.7;">
        {"&nbsp; &nbsp;".join(highlights)}
      </td>
      <td style="{FONT} font-size:11px; color:{int_col}; padding:7px 14px 7px 8px;
                 text-align:right; white-space:nowrap; font-weight:700;">
        {int_icon}&nbsp;SEC
      </td>
    </tr>""")

    rows.append(f"""
    <tr>
      <td colspan="5" style="padding:6px 24px 10px; background:{C['gray_light']};
          border-top:1px solid {C['gray_border']}; border-radius:0 0 8px 8px;">
        <span style="{FONT} font-size:10px; color:{C['gray_text']};">
          ↓ Full position diff below &nbsp;·&nbsp;
          ✓ SEC = parsed total verified against SEC cover-page tableValueTotal &nbsp;·&nbsp;
          cross-check at 13f.info
        </span>
      </td>
    </tr>""")

    return "\n".join(rows)


def build_portfolio_html(portfolio: dict) -> str:
    filers = portfolio.get("filers", [])
    if not filers:
        return ""

    rows = []

    # ── Section header ─────────────────────────────────────────────────────
    rows.append(f"""
    <tr>
      <td colspan="3" style="background:linear-gradient(135deg,{C['teal_dark']},{C['teal_med']});
          padding:18px 24px; border-radius:8px 8px 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:17px; font-weight:700; color:#fff;">
                📊 Portfolio Tracker — 13F Filings
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                SEC EDGAR · quarterly disclosures · new filing within 60 days
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:18px; font-weight:800; color:#fff;">
                {len(filers)} filer{'s' if len(filers) != 1 else ''}
              </span><br>
              <span style="{FONT} font-size:11px; color:rgba(255,255,255,0.75);">
                with recent filing
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

    for i, f in enumerate(filers):
        chg      = f.get("changes", {})
        period   = _quarter_label(f.get("report_date", ""))
        prev_per = _quarter_label(f.get("prev_report", ""))
        filed    = f.get("filed_date", "")
        days_ago = f.get("days_ago", 0)
        total_m  = f.get("total_value_m", 0)
        n_hold   = f.get("n_holdings", 0)

        n_new  = len(chg.get("opened",    []))
        n_cls  = len(chg.get("closed",    []))
        n_inc  = len(chg.get("increased", []))
        n_dec  = len(chg.get("decreased", []))
        has_chg = n_new + n_cls + n_inc + n_dec > 0

        integrity_ok   = f.get("integrity_ok")
        integrity_note = f.get("integrity_note", "")
        rep_total_m    = f.get("reported_total_m")

        if integrity_ok is True:
            integrity_badge = _badge("✓ SEC verified", C["green_med"])
        elif integrity_ok is False:
            integrity_badge = _badge(f"⚠ {integrity_note}", C["orange_dark"])
        else:
            integrity_badge = _badge("– not checked", C["gray_bar"], "#fff")

        # Filer sub-header
        rows.append(f"""
    <tr>
      <td colspan="3" style="background:{C['teal_light']}; padding:10px 24px 8px;
          border-top:{'2px solid ' + C['teal_med'] if i > 0 else 'none'};">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <span style="{FONT} font-size:13px; font-weight:700; color:{C['teal_dark']};">
                {f['label']}
              </span>
              &nbsp;
              <span style="{FONT} font-size:11px; color:{C['gray_text']};">
                {f['name']}
              </span>
              <br>
              <span style="{FONT} font-size:10px; color:{C['teal_med']}; font-weight:600;">
                {f['theme']}
              </span>
            </td>
            <td style="text-align:right; white-space:nowrap;">
              <span style="{FONT} font-size:13px; font-weight:700; color:{C['teal_dark']};">
                {_fmt_m(total_m)} &nbsp;·&nbsp; {n_hold} positions
              </span>
              <br>
              <span style="{FONT} font-size:10px; color:{C['gray_text']};">
                {period} &nbsp;·&nbsp; filed&nbsp;<strong>{filed}</strong>&nbsp;({days_ago}d ago)
              </span>
            </td>
          </tr>
        </table>
      </td>
    </tr>""")

        # Integrity + change summary row
        badge_parts = [integrity_badge]
        if n_new:  badge_parts.append(_badge(f"+{n_new} new",      C["green_med"]))
        if n_cls:  badge_parts.append(_badge(f"−{n_cls} closed",   C["red_dark"]))
        if n_inc:  badge_parts.append(_badge(f"↑{n_inc} increased",C["apex_blue"]))
        if n_dec:  badge_parts.append(_badge(f"↓{n_dec} decreased",C["orange_dark"]))
        if not has_chg:
            badge_parts.append(_badge("no position changes", C["gray_bar"], "#fff"))

        prev_label = (f"{prev_per} (filed&nbsp;{f['prev_date']})" if prev_per and f.get("prev_date")
                      else "no prior filing")
        rows.append(f"""
    <tr>
      <td colspan="3" style="padding:5px 24px 8px; background:{C['white']};">
        <span style="{FONT} font-size:10px; color:{C['gray_text']};">
          vs {prev_label}:&nbsp;&nbsp;
        </span>
        {"&nbsp; ".join(badge_parts)}
      </td>
    </tr>""")

        if not has_chg:
            continue

        # Change detail rows
        change_defs = [
            ("opened",    "NEW",       C["green_bg"],  C["green_dark"],  "+"),
            ("closed",    "CLOSED",    C["red_bg"],    C["red_dark"],    "−"),
            ("increased", "INCREASED", C["apex_light"],C["apex_blue"],   "↑"),
            ("decreased", "DECREASED", C["orange_bg"], C["orange_dark"], "↓"),
        ]
        for cat, cat_label, row_bg, row_fg, arrow in change_defs:
            positions = chg.get(cat, [])
            if not positions:
                continue
            shown  = positions[:_TOP_N]
            more   = len(positions) - _TOP_N

            rows.append(f"""
    <tr>
      <td colspan="3" style="padding:3px 24px 1px; background:{C['gray_light']};">
        <span style="{FONT} font-size:10px; font-weight:700; color:{row_fg};
              text-transform:uppercase; letter-spacing:0.5px;">
          {arrow} {cat_label}
          {f'<span style="font-weight:400; color:{C["gray_text"]};">(top {_TOP_N} of {len(positions)})</span>' if more > 0 else ''}
        </span>
      </td>
    </tr>""")

            for pos in shown:
                pct_s = f"&nbsp;&nbsp;{pos['pct']:+.0%} shares" if "pct" in pos else ""
                prev_s = (f"&nbsp;·&nbsp; prev {_fmt_m(pos['prev_value_m'])}"
                          if "prev_value_m" in pos else "")
                rows.append(f"""
    <tr style="background:{row_bg};">
      <td style="padding:4px 24px 4px 28px; {FONT} font-size:12px;
                 color:{C['black']}; width:55%;">
        {pos['name']}
      </td>
      <td style="padding:4px 8px; {FONT} font-size:12px;
                 font-weight:600; color:{row_fg}; white-space:nowrap;">
        {_fmt_m(pos['value_m'])}
      </td>
      <td style="padding:4px 14px 4px 4px; {FONT} font-size:11px;
                 color:{C['gray_text']}; white-space:nowrap;">
        {pct_s}{prev_s}
      </td>
    </tr>""")

    # Footer note
    rows.append(f"""
    <tr>
      <td colspan="3" style="padding:10px 24px 14px; border-top:1px solid {C['gray_border']};">
        <span style="{FONT} font-size:10px; color:{C['gray_text']};">
          ⚠&nbsp; 13F filings are quarterly (45-day lag after quarter end) ·
          long equity only — options &amp; short positions not shown ·
          cross-check at 13f.info · not financial advice
        </span>
      </td>
    </tr>""")

    return "\n".join(rows)


# ── Full HTML email ────────────────────────────────────────────────────────

def build_html_email(apex: dict, nova: dict, context: str,
                     portfolio: Optional[dict] = None) -> str:
    now_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    apex_html  = build_apex_html(apex)
    nova_html  = build_nova_html(nova)
    nova_level = nova.get("confirmed_level", 0)

    digest_inner    = build_filings_digest_html(portfolio or {})
    portfolio_inner = build_portfolio_html(portfolio or {})

    def _card(inner: str) -> str:
        return f"""<tr>
    <td style="background:{C['white']}; border-radius:8px;
               box-shadow:0 2px 6px rgba(0,0,0,0.08); overflow:hidden;">
      <table width="100%" cellpadding="0" cellspacing="0">
        {inner}
      </table>
    </td>
  </tr>"""

    spacer          = '<tr><td style="height:16px;"></td></tr>'
    digest_card     = (_card(digest_inner)    if digest_inner    else "")
    digest_spacer   = (spacer                 if digest_inner    else "")
    portfolio_card  = (_card(portfolio_inner) if portfolio_inner else "")
    portfolio_spacer= (spacer                 if portfolio_inner else "")

    apex_circuit = apex.get("circuit_triggered", False)
    apex_trail   = apex.get("trail_stop_fired",  False)

    emergency_html = ""
    if apex_circuit or apex_trail:
        emergency_html = f"""
  <tr>
    <td style="background:{C['red_dark']}; color:#fff; padding:16px 24px;
               text-align:center; {FONT} font-size:15px; font-weight:700;
               letter-spacing:0.5px; border-radius:8px; margin-bottom:16px;">
      🚨🚨🚨&nbsp; APEX EMERGENCY — LOG INTO FIDELITY NOW AND EXIT TQQQ → VOO &nbsp;🚨🚨🚨
      {"<br><span style='font-size:12px;font-weight:normal;'>Circuit breaker triggered.</span>" if apex_circuit else ""}
      {"<br><span style='font-size:12px;font-weight:normal;'>Trailing stop triggered.</span>" if apex_trail else ""}
    </td>
  </tr>
  <tr><td style="height:12px;"></td></tr>"""

    nova_alert_html = ""
    if nova_level >= 3:
        nova_fg, _, nova_status = _NOVA_SEVERITY[nova_level]
        nova_alert_html = f"""
  <tr>
    <td style="background:{nova_fg}; color:#fff; padding:10px 24px;
               text-align:center; {FONT} font-size:13px; font-weight:700;
               border-radius:8px; margin-bottom:8px;">
      {nova_status} — Review semiconductor exposure
    </td>
  </tr>
  <tr><td style="height:8px;"></td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:20px 12px; background:{C['gray_bg']}; {FONT}">

<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="max-width:680px; margin:0 auto;">

  <!-- Top bar -->
  <tr>
    <td style="padding-bottom:10px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="{FONT} font-size:11px; color:{C['gray_text']};">
            Investment Signals
          </td>
          <td style="{FONT} font-size:11px; color:{C['gray_text']}; text-align:right;">
            {context} &nbsp;·&nbsp; {now_str}
          </td>
        </tr>
      </table>
    </td>
  </tr>

  {emergency_html}
  {nova_alert_html}

  <!-- ── APEX card ── -->
  <tr>
    <td style="background:{C['white']}; border-radius:8px;
               box-shadow:0 2px 6px rgba(0,0,0,0.08); overflow:hidden;">
      <table width="100%" cellpadding="0" cellspacing="0">
        {apex_html}
      </table>
    </td>
  </tr>

  <tr><td style="height:16px;"></td></tr>

  <!-- ── NOVA card ── -->
  <tr>
    <td style="background:{C['white']}; border-radius:8px;
               box-shadow:0 2px 6px rgba(0,0,0,0.08); overflow:hidden;">
      <table width="100%" cellpadding="0" cellspacing="0">
        {nova_html}
      </table>
    </td>
  </tr>

  {digest_spacer}

  <!-- ── New 13F Filings digest (compact, one row per filer) ── -->
  {digest_card}

  {portfolio_spacer}

  <!-- ── Portfolio card — full position diff ── -->
  {portfolio_card}

  <!-- Footer -->
  <tr>
    <td style="padding:14px 0 6px; text-align:center;
               {FONT} font-size:10px; color:{C['gray_text']}; line-height:1.6;">
      APEX v3.0 &nbsp;·&nbsp; NOVA Bear Monitor v1.0
      &nbsp;·&nbsp; Roth IRA only &nbsp;·&nbsp; Not financial advice
    </td>
  </tr>

</table>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    import warnings, contextlib
    warnings.filterwarnings("ignore")

    context = run_context()

    with contextlib.redirect_stdout(sys.stderr):
        apex      = run_apex(verbose=False)
        nova      = run_nova_bear_monitor(verbose=False)
        try:
            portfolio = run_portfolio_tracker(verbose=True)
        except Exception as e:
            print(f"Portfolio tracker failed: {e}", file=sys.stderr)
            portfolio = {"filers": []}

    subject = build_subject(apex, nova, context)
    body    = build_html_email(apex, nova, context, portfolio)

    print(json.dumps({"subject": subject, "body": body}))


if __name__ == "__main__":
    main()
