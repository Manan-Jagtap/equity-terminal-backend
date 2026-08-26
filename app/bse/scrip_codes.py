"""
app/bse/scrip_codes.py — NSE ticker → BSE numeric scrip code lookup.

The hardcoded table covers the NBFCs and lenders Manan tracks. For tickers
not in the table, get_scrip_code() falls back to BSE's quote-search endpoint.

What that fallback actually does today (checked 16 Aug 2026): BSE answers
getQouteSearchW.aspx with a 302 to error_Bse.html, r.json() raises on the
HTML, and the lookup returns None after one WARNING — it resolves nothing for
any ticker outside the table. Its downstream is BSE-shaped only: the BSE
announcements API has been anti-bot blocked since mid-Jul 2026 and still is, so
a scrip code buys no live BSE filings. (This paragraph also said IndianAPI had
revoked /documents on 24 Jul 2026 — corrected 25 Aug 2026: that was the wrong
host answering; /documents is on-plan on dev.indianapi.in and company documents
reach us through the ingester, needing no scrip code.) The fallback is kept
(not deleted) for the offline
tooling — scripts/backfill_nbfc_docs.py, scripts/test_bse_fetch.py,
app/bse/fetcher.py — and for the day BSE answers again; the request-time
caller, documents_routes._merge_live_bse, no longer reaches it (short-circuited
by _LIVE_BSE_OVERLAY). Do not add a request-path caller: the fallback is an
uncached 15 s-timeout HTTP round-trip in the calling thread.

Verified scrip codes against bseindia.com as of May 2026. If BSE delists
or renames anything, update here.
"""
from __future__ import annotations
import logging
from typing import Optional
import requests

log = logging.getLogger(__name__)


# Hardcoded ticker → scrip code map.
# Covers ~60 names across NBFCs, banks, AMCs.
BSE_SCRIP_CODES: dict[str, str] = {
    # --- Gold loan NBFCs ---
    "MUTHOOTFIN":   "533398",
    "MANAPPURAM":   "531213",
    "IIFL":         "532636",
    "IIFLFIN":      "532636",   # alias

    # --- Diversified NBFCs ---
    "BAJFINANCE":   "500034",
    "BAJAJFINSV":   "532978",
    "CHOLAFIN":     "511243",
    "SHRIRAMFIN":   "511218",
    "SUNDARMFIN":   "590071",
    "MMFIN":        "532720",   # M&M Financial Services
    "L&TFH":        "533519",
    "LTFH":         "533519",   # alias (post-rename to LTF)
    "LTF":          "533519",
    "PFC":          "532810",
    "POWERFIN":     "532810",   # alias
    "RECLTD":       "532955",
    "IRFC":         "543257",

    # --- Housing finance ---
    "LICHSGFIN":    "500253",
    "PNBHOUSING":   "540173",
    "AAVAS":        "541988",
    "APTUS":        "543335",
    "HOMEFIRST":    "543259",
    "AADHARHFC":    "544306",
    "CANFINHOME":   "511196",
    "REPCOHOME":    "535322",
    "GICHSGFIN":    "511676",
    "INDOSTAR":     "541336",

    # --- Affordable / vehicle / MSME ---
    "FEDFINA":      "544027",
    "FUSION":       "543652",
    "SBFC":         "543889",
    "FIVESTAR":     "543663",
    "UJJIVANSFB":   "542904",
    "EQUITASBNK":   "543243",
    "CREDITACC":    "541770",
    "SPANDANA":     "542759",
    "ARMANFIN":     "531179",
    "CAPITALSFB":   "544305",
    "JIOFIN":       "543940",
    "TATAINVEST":   "501301",
    "TATATECH":     "544028",   # not lender but in tracking list
    "POONAWALLA":   "524000",

    # --- AMCs / brokers ---
    "HDFCAMC":      "541729",
    "NAM-INDIA":    "540975",   # Nippon AMC
    "NAMINDIA":     "540975",
    "UTIAMC":       "543238",
    "ABCAPITAL":    "540691",
    "ICICIGI":      "540716",   # general insurance
    "ICICIPRULI":   "540133",
    "HDFCLIFE":     "540777",
    "SBILIFE":      "540719",
    "LICI":         "543526",
    "MOTILALOFS":   "532892",
    "ANGELONE":     "543235",
    "ANANDRATHI":   "543415",
    "5PAISA":       "540776",

    # --- Banks (large; for future template coverage) ---
    "HDFCBANK":     "500180",
    "ICICIBANK":    "532174",
    "KOTAKBANK":    "500247",
    "SBIN":         "500112",
    "AXISBANK":     "532215",
    "INDUSINDBK":   "532187",
    "IDFCFIRSTB":   "539437",
    "FEDERALBNK":   "500469",
    "RBLBANK":      "540065",
    "BANDHANBNK":   "541153",
    "AUBANK":       "540611",
    "YESBANK":      "532648",
    "PNB":          "532461",
    "BANKBARODA":   "532134",
    "CANBK":        "532483",
    "UNIONBANK":    "532477",
    "IOB":          "532388",
    "CENTRALBK":    "532885",
    "BANKINDIA":    "532149",
    "INDIANB":      "532814",
    "UCOBANK":      "532505",

    # TATACAPITAL was mapped to "544350" with the comment "⚠ verify if/when
    # listed" — a GUESSED code, never confirmed against BSE.
    #
    # A wrong scrip code is not a blank: get_scrip_code() feeds documents_routes,
    # so it would attach another listed company's filings to Tata Capital under a
    # confident-looking UI. That is the DATA-10 contamination shape. When this
    # was written both verification routes looked closed; corrected 25 Aug 2026,
    # IndianAPI /documents was never revoked (wrong host). It does not help here
    # anyway: only BSE's own listing can confirm a scrip CODE, and BSE still
    # blocks us.
    #
    # Absent beats wrong: with no entry, get_scrip_code returns None and the
    # caller degrades to "no filings" honestly. Restore it with a code read off
    # the BSE listing itself, not inferred from the numbering sequence.
}


def get_scrip_code(ticker: str) -> Optional[str]:
    """
    Resolve an NSE ticker to its BSE scrip code.
    Looks up the hardcoded table first; falls back to BSE search — a live,
    uncached, 15 s-timeout HTTP call that currently (Aug 2026) always ends in
    None because BSE redirects the search endpoint to an error page. Offline
    tooling only; never call this from a request path (see module docstring).
    Returns None if not found.
    """
    t = ticker.upper().strip()
    if t in BSE_SCRIP_CODES:
        return BSE_SCRIP_CODES[t]

    # Fallback: BSE search API
    log.info("scrip code for %s not in table; trying BSE search", t)
    try:
        return _bse_search_lookup(t)
    except Exception as e:
        log.warning("BSE search failed for %s: %s", t, e)
        return None


def _bse_search_lookup(ticker: str) -> Optional[str]:
    """BSE has an autocomplete-style search API. Returns first match scrip code."""
    url = "https://api.bseindia.com/Msource/1D/getQouteSearchW.aspx"
    headers = {
        "Origin": "https://www.bseindia.com",
        "Referer": "https://www.bseindia.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    params = {"Type": "EQ", "text": ticker}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    # Format varies; commonly a list of {"scrip_cd": "...", "scrip_id": "..."}
    if isinstance(data, list) and data:
        first = data[0]
        return first.get("scrip_cd") or first.get("SCRIP_CD") or first.get("Scripcode")
    return None
