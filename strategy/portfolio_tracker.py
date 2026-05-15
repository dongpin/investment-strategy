"""
Portfolio 13F Tracker
=====================
Monitors SEC EDGAR 13F-HR filings for specific institutional filers.
Fetches the two most recent quarterly filings on-the-fly and diffs them.
Included in the daily email only when a filer has filed within the last 60 days.

Tracked filers:
  · Situational Awareness LP  (Leopold Aschenbrenner) — AI infrastructure
  · NVIDIA Corporation                                 — AI semiconductor
  · Coatue Management LLC     (Philippe Laffont)       — AI / cloud / tech
  · Duquesne Family Office    (Stanley Druckenmiller)  — Macro + AI megatrends
  · Tiger Global Management   (Chase Coleman)          — Growth tech / AI

Data source: SEC EDGAR data.sec.gov (free, no auth, 10 req/sec hard limit)

Usage:
    python portfolio_tracker.py           → print summary
    python portfolio_tracker.py --json    → JSON output
"""

import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

warnings.filterwarnings("ignore")

try:
    import requests
except ImportError:
    print("pip install requests")
    raise

# ── Configuration ──────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "InvestmentStrategy thequansheng@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_RECENT_DAYS = 60    # Include a filer in the email if filed within this window
_SLEEP       = 0.15  # Seconds between requests (keep well below 10 req/sec)
_TOP_N       = 6     # Max rows per change category shown in the email

FILERS = [
    {
        "name":  "Situational Awareness LP",
        "cik":   "0002045724",
        "label": "Aschenbrenner",
        "theme": "AI infrastructure · Bitcoin mining",
    },
    {
        "name":  "NVIDIA Corporation",
        "cik":   "0001045810",
        "label": "Nvidia",
        "theme": "AI semiconductor",
    },
    {
        "name":  "Coatue Management LLC",
        "cik":   "0001135730",
        "label": "Coatue / Laffont",
        "theme": "AI · cloud · consumer internet",
    },
    {
        "name":  "Duquesne Family Office LLC",
        "cik":   "0001536411",
        "label": "Druckenmiller",
        "theme": "Macro · AI megatrends",
    },
    {
        "name":  "Tiger Global Management LLC",
        "cik":   "0001167483",
        "label": "Tiger Global / Coleman",
        "theme": "Growth tech · AI",
    },
]


# ── SEC EDGAR helpers ──────────────────────────────────────────────────────

def _get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=20)
        time.sleep(_SLEEP)
        return r if r.status_code == 200 else None
    except Exception:
        time.sleep(_SLEEP)
        return None


def _pad_cik(cik: str) -> str:
    return cik.lstrip("0").zfill(10)


def _get_submissions(cik: str) -> Optional[dict]:
    r = _get(f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json")
    return r.json() if r else None


def _get_recent_13f(subs: dict, n: int = 2) -> list:
    recent  = subs.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accs    = recent.get("accessionNumber", [])
    dates   = recent.get("filingDate", [])
    reports = recent.get("reportDate", [])
    pdocs   = recent.get("primaryDocument", [])
    out = []
    for form, acc, date, rep, pdoc in zip(forms, accs, dates, reports, pdocs):
        if form == "13F-HR":
            out.append({"accession": acc, "date": date, "report_date": rep,
                        "primary_doc": pdoc.split("/")[-1] if pdoc else "primary_doc.xml"})
        if len(out) >= n:
            break
    return out


def _fetch_cover_totals(cik: str, accession: str, primary_doc: str = "primary_doc.xml") -> tuple:
    """
    Fetch the 13F cover XML and return (reported_entry_count, reported_value_dollars).
    The SEC's tableValueTotal is always in actual US dollars (not thousands).
    Returns (None, None) on failure.
    """
    cik_num = str(int(cik.lstrip("0") or "0"))
    acc_nd  = accession.replace("-", "")
    base    = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_nd}"

    for fname in (primary_doc, "primary_doc.xml"):
        r = _get(f"{base}/{fname}")
        if r and "tableValueTotal" in r.text:
            try:
                em = re.search(r"<[^>]*tableEntryTotal[^>]*>(\d+)<", r.text, re.IGNORECASE)
                vm = re.search(r"<[^>]*tableValueTotal[^>]*>(\d+)<", r.text, re.IGNORECASE)
                return (int(em.group(1)) if em else None,
                        int(vm.group(1)) if vm else None)
            except (AttributeError, ValueError):
                return (None, None)
    return (None, None)


def _is_infotable(text: str) -> bool:
    t = text.lower()
    return "infotable" in t or "informationtable" in t


def _fetch_infotable_xml(cik: str, accession: str) -> Optional[str]:
    """Try common infotable XML filenames, then fall back to index parsing."""
    cik_num = str(int(cik.lstrip("0") or "0"))
    acc_nd  = accession.replace("-", "")
    base    = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_nd}"

    for fname in ("form13fInfoTable.xml", "infotable.xml", "13fInfoTable.xml",
                  "primary_doc.xml", "13f-hr.xml"):
        r = _get(f"{base}/{fname}")
        if r and _is_infotable(r.text):
            return r.text

    # Fall back: scrape the filing index for any XML that looks like an infotable
    r_idx = _get(f"{base}/{accession}-index.htm")
    if r_idx:
        for href in re.findall(r'href="([^"]+\.xml)"', r_idx.text, re.IGNORECASE):
            fname = href.split("/")[-1]
            r2 = _get(f"{base}/{fname}")
            if r2 and _is_infotable(r2.text):
                return r2.text
    return None


# ── Holdings parsing ───────────────────────────────────────────────────────

def _parse_holdings(xml_text: str) -> dict:
    """Return {issuer_name: {value_m, shares, cusip}}. value stored in $M."""
    if not xml_text:
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    # Detect XML namespace (varies across filers and filing periods)
    ns_match = re.search(r'xmlns(?::[^=]+)?="([^"]*thirteenf[^"]*)"', xml_text)
    if ns_match:
        ns = {"ns": ns_match.group(1)}
        entries = root.findall(".//ns:infoTable", ns)
        def _t(el, tag):
            c = el.find(f"ns:{tag}", ns)
            return c.text.strip() if c is not None and c.text else ""
        def _find(el, tag):
            return el.find(f".//ns:{tag}", ns)
    else:
        ns = {}
        entries = root.findall(".//infoTable")
        def _t(el, tag):
            c = el.find(tag)
            return c.text.strip() if c is not None and c.text else ""
        def _find(el, tag):
            return el.find(f".//{tag}")

    # First pass: collect raw integer values
    raw: dict = {}
    for entry in entries:
        try:
            name   = _t(entry, "nameOfIssuer")
            val_s  = _t(entry, "value")
            cusip  = _t(entry, "cusip")
            sh_el  = _find(entry, "sshPrnamt")
            shares = int(sh_el.text) if sh_el is not None and sh_el.text else 0
            rv     = int(val_s or "0")
            if not name:
                continue
            if name in raw:
                raw[name]["rv"]     += rv
                raw[name]["shares"] += shares
            else:
                raw[name] = {"rv": rv, "shares": shares, "cusip": cusip}
        except (ValueError, AttributeError):
            continue

    if not raw:
        return {}

    # Detect value unit: SEC standard is thousands, but some filers use actual dollars.
    # Heuristic: value/shares should ≈ stock price ($1–$10,000).
    # In thousands: value/shares ≈ price/1000 (< 1.0 for most stocks).
    # In dollars:   value/shares ≈ price      (>> 1.0).
    # Threshold of 2.0 cleanly separates the two cases.
    samples = [(h["rv"], h["shares"]) for h in raw.values() if h["shares"] > 0][:20]
    if samples:
        avg_ratio = sum(v / s for v, s in samples) / len(samples)
        divisor = 1_000_000 if avg_ratio > 2.0 else 1_000
    else:
        divisor = 1_000  # default: SEC standard (thousands → millions)

    return {
        name: {
            "value_m": h["rv"] / divisor,
            "shares":  h["shares"],
            "cusip":   h["cusip"],
        }
        for name, h in raw.items()
    }


# ── Position diff ──────────────────────────────────────────────────────────

def _diff_holdings(old: dict, new: dict) -> dict:
    """Diff two holdings snapshots. Uses share count to detect real activity."""
    old_k, new_k = set(old), set(new)
    opened, closed, increased, decreased = [], [], [], []

    for name in sorted(new_k - old_k):
        opened.append({**new[name], "name": name})

    for name in sorted(old_k - new_k):
        closed.append({**old[name], "name": name})

    for name in sorted(old_k & new_k):
        os_, ns_ = old[name]["shares"], new[name]["shares"]
        ov       = old[name]["value_m"]
        pct = (ns_ - os_) / os_ if os_ > 0 else 0
        if pct > 0.10:
            increased.append({**new[name], "name": name, "pct": pct, "prev_value_m": ov})
        elif pct < -0.10:
            decreased.append({**new[name], "name": name, "pct": pct, "prev_value_m": ov})

    by_val = lambda lst: sorted(lst, key=lambda x: x.get("value_m", 0), reverse=True)
    return {
        "opened":    by_val(opened),
        "closed":    by_val(closed),
        "increased": by_val(increased),
        "decreased": by_val(decreased),
    }


# ── Public API ─────────────────────────────────────────────────────────────

def run_portfolio_tracker(verbose: bool = True) -> dict:
    """
    Fetch and diff the two most recent 13F-HR filings for each tracked filer.
    Returns only filers whose latest filing is within _RECENT_DAYS days.
    """
    results = []

    for filer in FILERS:
        try:
            subs = _get_submissions(filer["cik"])
            if not subs:
                if verbose:
                    print(f"  [{filer['label']}] submissions fetch failed", file=sys.stderr)
                continue

            filings = _get_recent_13f(subs, n=2)
            if not filings:
                if verbose:
                    print(f"  [{filer['label']}] no 13F-HR filings found", file=sys.stderr)
                continue

            latest   = filings[0]
            filed_dt = datetime.strptime(latest["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_ago = (datetime.now(timezone.utc) - filed_dt).days

            if days_ago > _RECENT_DAYS:
                if verbose:
                    print(f"  [{filer['label']}] last filed {days_ago}d ago — outside window",
                          file=sys.stderr)
                continue

            if verbose:
                print(f"  [{filer['label']}] filing {latest['date']} ({days_ago}d ago) — fetching XML",
                      file=sys.stderr)

            # Fetch cover XML for authoritative totals (used for integrity check)
            rep_entries, rep_value_dollars = _fetch_cover_totals(
                filer["cik"], latest["accession"],
                latest.get("primary_doc", "primary_doc.xml"))

            curr_xml = _fetch_infotable_xml(filer["cik"], latest["accession"])
            curr     = _parse_holdings(curr_xml)

            changes, prev_date, prev_report = {}, None, None
            if len(filings) >= 2:
                prev_filing = filings[1]
                prev_date   = prev_filing["date"]
                prev_report = prev_filing.get("report_date", "")
                prev_xml    = _fetch_infotable_xml(filer["cik"], prev_filing["accession"])
                prev        = _parse_holdings(prev_xml)
                changes     = _diff_holdings(prev, curr)

            total_m = sum(h["value_m"] for h in curr.values())

            # Integrity: compare parsed total ($M) against cover-reported total.
            # tableValueTotal unit varies by filer (dollars vs. thousands), so try
            # both and pick whichever matches the parsed total more closely.
            integrity_ok   = None
            integrity_note = ""
            rep_total_m    = None
            if rep_value_dollars is not None and total_m > 0:
                as_dollars    = rep_value_dollars / 1_000_000
                as_thousands  = rep_value_dollars / 1_000
                diff_d = abs(total_m - as_dollars)   / total_m
                diff_t = abs(total_m - as_thousands) / total_m
                rep_total_m = as_dollars if diff_d <= diff_t else as_thousands
                pct_diff    = min(diff_d, diff_t)
                integrity_ok = pct_diff < 0.05  # within 5%
                if not integrity_ok:
                    integrity_note = (f"parsed ${total_m:,.0f}M vs "
                                      f"SEC reported ${rep_total_m:,.0f}M "
                                      f"({pct_diff:.1%} diff)")
            if verbose and integrity_ok is False:
                print(f"  [{filer['label']}] ⚠ integrity: {integrity_note}", file=sys.stderr)

            results.append({
                "name":            subs.get("name", filer["name"]),
                "label":           filer["label"],
                "theme":           filer["theme"],
                "filed_date":      latest["date"],
                "report_date":     latest.get("report_date", ""),
                "days_ago":        days_ago,
                "prev_date":       prev_date,
                "prev_report":     prev_report,
                "n_holdings":      len(curr),
                "total_value_m":   round(total_m),
                "reported_total_m": round(rep_total_m) if rep_total_m is not None else None,
                "reported_entries": rep_entries,
                "integrity_ok":    integrity_ok,
                "integrity_note":  integrity_note,
                "changes":         changes,
            })

        except Exception as e:
            if verbose:
                print(f"  [{filer['label']}] error: {e}", file=sys.stderr)
            continue

    return {
        "filers":     results,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json

    use_json = "--json" in sys.argv
    data = run_portfolio_tracker(verbose=not use_json)

    if use_json:
        print(_json.dumps(data, indent=2))
        sys.exit(0)

    filers = data["filers"]
    if not filers:
        print("No recent 13F filings — all tracked filers last filed >60 days ago.")
        sys.exit(0)

    for f in filers:
        chg = f["changes"]
        print(f"\n{'='*65}")
        print(f"{f['label']} — {f['name']}")
        print(f"Theme  : {f['theme']}")
        print(f"Filed  : {f['filed_date']} ({f['days_ago']}d ago)  |  "
              f"Period: {f['report_date']}  |  "
              f"vs prior: {f['prev_date']}")
        print(f"AUM    : ${f['total_value_m']:,}M  |  {f['n_holdings']} holdings")

        for cat, label in [("opened",    "NEW POSITIONS"),
                            ("closed",    "CLOSED POSITIONS"),
                            ("increased", "INCREASED  (shares >+10%)"),
                            ("decreased", "DECREASED  (shares >-10%)")]:
            rows = chg.get(cat, [])[:_TOP_N]
            if not rows:
                continue
            print(f"\n  {label}:")
            for r in rows:
                pct_s = f"  {r['pct']:+.0%}" if "pct" in r else ""
                print(f"    {r['name']:<40} ${r['value_m']:>8,.1f}M{pct_s}")
