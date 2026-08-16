"""
indianapi_ingester.py — accurate Indian fundamentals from indianapi.in.

Pulls EVERYTHING from the single reliable /stock endpoint (the /historical_stats
endpoint is flaky/504s, so we don't use it). Per company, one call gives:
  - 7 annual years of Income Statement / Balance Sheet / Cash Flow  → HistoricalFinancial
  - latest-year facts (revenue, PAT, net worth, net debt)           → FinancialFact
  - corrected shares outstanding (PAT / EPS)                        → Company.shares_outstanding
  - current NSE price                                                → MarketSnapshot

Auth: set env var INDIANAPI_KEY to your x-api-key (never hard-code it).

Run:
  export INDIANAPI_KEY=...
  python -m app.ingest.indianapi_ingester                  # all companies
  python -m app.ingest.indianapi_ingester --ticker TCS     # one company
  python -m app.ingest.indianapi_ingester --limit 50       # first 50
  python -m app.ingest.indianapi_ingester --price-only     # just prices
"""
import logging as _logging
_log = _logging.getLogger("indianapi_ingester")
import os, sys, time, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import requests
from app.database import SessionLocal, engine, Base
from app import models, concepts as K
from app import templates as T, sector_params as SP

Base.metadata.create_all(bind=engine)

# Developer plan (v2) base. The old "stock." base mis-resolves some tickers
# (e.g. BAJAJ-AUTO → Bajaj Finance, LT/M&M → nothing); "dev." resolves them
# correctly. Override with INDIANAPI_BASE if needed.
BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
RATE_SLEEP = 1.1

# ── API-call budget guard ────────────────────────────────────────────────────
# IndianAPI is quota-limited (~10k calls/month on the dev plan) with NO
# server-side meter. We count every outbound request in-process and expose the
# tally so bulk jobs can pre-flight; the scheduler persists a durable monthly
# total (models.ApiUsage). The in-process counter resets on restart — it guards
# a single runaway run; the DB total guards the calendar month.
import datetime as _dt
MONTHLY_BUDGET = int(os.getenv("INDIANAPI_MONTHLY_BUDGET", "10000"))
_CALL_COUNT = 0
_CALL_MONTH = _dt.date.today().strftime("%Y-%m")


def get_call_count() -> int:
    """API calls this process has made in the current calendar month."""
    return _CALL_COUNT


def reset_call_count() -> None:
    global _CALL_COUNT
    _CALL_COUNT = 0


def _get(path, params, retries=4):
    if not KEY or KEY.lower().startswith(("paste", "your")):
        raise RuntimeError("INDIANAPI_KEY is not set to your real key.")
    global _CALL_COUNT, _CALL_MONTH
    m = _dt.date.today().strftime("%Y-%m")
    if m != _CALL_MONTH:                       # month rollover → fresh in-process tally
        _CALL_MONTH, _CALL_COUNT = m, 0
    last = "unknown error"
    for attempt in range(retries):
        _CALL_COUNT += 1                       # every attempt hits the API → counts
        from app import vendor_meter; vendor_meter.tick()   # FIX-07 durable meter
        try:
            r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                             params=params, timeout=90)
            if r.status_code == 200:
                data = r.json()
                # OUTCOME per attempt, matching tick() above: each attempt is
                # one call the vendor either answered or did not. /stock is the
                # only path this helper serves and it never legitimately answers
                # a name we track with an empty body, so empty_ok stays False.
                # The ring is per-process — the scheduler's does not reach
                # /api/health — but this same code runs in the web process under
                # /api/admin/run-backfill, where it does.
                vendor_meter.record(vendor_meter.payload_ok(data))
                return data
            vendor_meter.record(False)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code} — {r.text[:120]}"
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {r.status_code} — {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            # Transport failures AND a 200 whose body will not parse (requests
            # raises its JSONDecodeError as a RequestException) both land here.
            vendor_meter.record(False)
            last = f"{type(e).__name__}: {e}"
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{last} (after {retries} retries)")


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Reuters-style displayName (normalised) → our canonical line item.
INC_MAP = {
    "totalrevenue": "revenue", "revenue": "revenue",
    "costofrevenuetotal": "raw_material",
    "grossprofit": "gross_profit",
    "operatingincome": "ebit",
    "depreciationamortization": "depreciation",
    "netincomebeforetaxes": "pbt",
    "provisionforincometaxes": "tax",
    "netincome": "pat", "netincomeaftertaxes": "pat",
    "interestincexpnetnonoptotal": "interest_expense",
    "dilutednormalizedeps": "_eps", "dilutedepsexcludingextraorditems": "_eps",
    "dilutedweightedaverageshares": "_shares",
    # Forensic inputs (Beneish SGAI; Reuters "Selling/General/Admin. Expenses, Total").
    "sellinggeneraladminexpensestotal": "sga",
    "sellinggenadminexpensestotal": "sga", "sgaexpensetotal": "sga",
}
BAL_MAP = {
    "totalequity": "net_worth",
    "commonstocktotal": "equity",
    "retainedearningsaccumulateddeficit": "reserves",
    "totalassets": "total_assets",
    "totaldebt": "borrowings",
    "totallongtermdebt": "lt_debt", "longtermdebt": "lt_debt",
    "cash": "cash", "cashandshortterminvestments": "cash",
    "totalliabilities": "total_liabilities",
    "propertyplantequipmenttotalnet": "fixed_assets",
    "longterminvestments": "investments",
    "totalcommonsharesoutstanding": "_shares_bal",
    # Forensic inputs: working capital (Altman X1) + receivables (Beneish DSRI).
    "totalcurrentassets": "current_assets",
    "totalcurrentliabilities": "current_liabilities",
    "totalreceivablesnet": "receivables",
    "accountsreceivabletradenet": "receivables",
    "totalinventory": "inventory",
    "accountspayable": "payables", "payableaccrued": "payables",
    "totalnoncurrentassets": "noncurrent_assets",
    # Beneish DEPI (depreciation index) + AQI (asset quality) inputs, and the
    # current-asset/liability COMPONENTS — confirmed present via FORENSIC_DEBUG_KEYS
    # — so working capital can be reconstructed if the aggregate is ever missing.
    "accumulateddepreciationtotal": "accumulated_depreciation",
    "propertyplantequipmenttotalgross": "ppe_gross",
    "cashequivalents": "cash_equivalents",
    "shortterminvestments": "short_term_investments",
    "othercurrentassetstotal": "other_current_assets",
    "prepaidexpenses": "prepaid_expenses",
    "accruedexpenses": "accrued_expenses",
    "othercurrentliabilitiestotal": "other_current_liabilities",
    "notespayableshorttermdebt": "notes_payable_st",
    "currentportofltdebtcapitalleases": "current_lt_debt",
    "goodwillnet": "goodwill", "intangiblesnet": "intangibles",
}
CAS_MAP = {
    "cashfromoperatingactivities": "operating_cf",
    "cashfrominvestingactivities": "investing_cf",
    "cashfromfinancingactivities": "financing_cf",
    "capitalexpenditures": "capex",
    "totalcashdividendspaid": "dividends",
    "netchangeincash": "net_change_cash",
}
SECTIONS = {"INC": ("PL", INC_MAP), "BAL": ("BS", BAL_MAP), "CAS": ("CF", CAS_MAP)}

PL_ITEMS = {"revenue", "gross_profit", "ebitda", "ebit", "depreciation", "interest_expense", "pbt", "tax", "pat", "sga"}
BS_ITEMS = {"net_worth", "equity", "reserves", "total_assets", "borrowings", "lt_debt", "cash", "total_liabilities", "fixed_assets", "investments",
            "current_assets", "current_liabilities", "receivables", "inventory", "payables", "noncurrent_assets",
            "accumulated_depreciation", "ppe_gross", "cash_equivalents", "short_term_investments", "other_current_assets",
            "prepaid_expenses", "accrued_expenses", "other_current_liabilities", "notes_payable_st", "current_lt_debt",
            "goodwill", "intangibles"}
CF_ITEMS = {"operating_cf", "investing_cf", "financing_cf", "capex", "dividends", "net_change_cash"}


def _upsert_hist(s, cid, year, stmt, item, value):
    row = (s.query(models.HistoricalFinancial)
             .filter_by(company_id=cid, fiscal_year=year, statement_type=stmt, line_item=item).first())
    if row:
        row.value = value
    else:
        s.add(models.HistoricalFinancial(company_id=cid, fiscal_year=year, statement_type=stmt,
                                         line_item=item, value=value, source="indianapi"))


def _upsert_fact(s, cid, year, concept, value):
    row = (s.query(models.FinancialFact)
             .filter_by(company_id=cid, fiscal_year=year, period="FY", concept=concept).first())
    if row:
        row.value = value
    else:
        s.add(models.FinancialFact(company_id=cid, fiscal_year=year, period="FY",
                                   concept=concept, value=value, unit="INR_CR", source="indianapi"))


def _magnitude_warnings(yd: dict) -> list:
    """DATA-08: implausible ingested magnitudes for a LISTED company — surfaced,
    never dropped (the raw statement still stores/displays; downstream gates
    protect the verdicts). Reuses the BSE-PDF path's validate_pl for P&L
    consistency and adds a balance-sheet floor: VAML/VISL stored total_assets =
    0.02 cr (₹2 lakh) and no floor fired at ingest, so it read as silent data
    loss on the statements tab."""
    from app.ingest.pl_parser import validate_pl
    _pl_keys = ("revenue", "interest_income", "total_income", "pat", "pbt", "nii", "raw_material")
    _, pl_warns = validate_pl({k: yd[k] for k in _pl_keys if k in yd})
    # Keep only IMPLAUSIBILITY warnings — a partial payload legitimately missing a
    # top/profit line is normal at ingest (handled elsewhere), not a magnitude flag.
    warns = [w for w in pl_warns if "no top line" not in w and "no profit line" not in w]
    for k in ("total_assets", "net_worth", "equity"):
        v = yd.get(k)
        if v is not None and 0 < abs(v) < 1.0:      # a listed co's crore figure isn't < 1
            warns.append(f"{k}={v} implausibly small (<1 cr)")
    return warns


def _parse_financials(s, cid, co, stock):
    """Read the embedded /stock financials (7 annual years of INC/BAL/CAS) into
    HistoricalFinancial + latest-year FinancialFact + corrected share count."""
    fin = stock.get("financials") or stock.get("stockFinancialData") or []
    by_year = {}
    debug_keys = os.environ.get("FORENSIC_DEBUG_KEYS")   # set to log unmapped line items
    unmapped = {"INC": {}, "BAL": {}, "CAS": {}}          # section -> {normName: rawDisplayName}
    for entry in fin:
        if entry.get("Type") != "Annual":
            continue
        try:
            year = int(entry.get("FiscalYear"))
        except (TypeError, ValueError):
            continue
        smap = entry.get("stockFinancialMap") or {}
        yd = by_year.setdefault(year, {})
        for sec, (stmt, keymap) in SECTIONS.items():
            for it in (smap.get(sec) or []):
                norm = _norm(it.get("displayName"))
                canon = keymap.get(norm)
                if not canon:
                    if debug_keys and norm:
                        unmapped[sec].setdefault(norm, it.get("displayName"))
                    continue
                try:
                    yd[canon] = float(it.get("value"))
                except (TypeError, ValueError):
                    continue
    if debug_keys:
        for sec in ("INC", "BAL", "CAS"):
            if unmapped[sec]:
                print(f"  [forensic-debug] {co.ticker} {sec} UNMAPPED line items "
                      f"(normName → displayName): " +
                      ", ".join(f"{k}={v!r}" for k, v in sorted(unmapped[sec].items())))

    for year, yd in by_year.items():
        # EBITDA (Operating Profit) = EBIT + Depreciation
        if "ebit" in yd and "depreciation" in yd:
            yd["ebitda"] = yd["ebit"] + abs(yd["depreciation"])
        # Free cash flow = Operating CF + Capex (capex is negative)
        if "operating_cf" in yd and "capex" in yd:
            yd["fcf"] = yd["operating_cf"] + yd["capex"]
        for item, v in yd.items():
            if item.startswith("_"):
                continue
            stmt = "PL" if item in PL_ITEMS else "BS" if item in BS_ITEMS else "CF" if item in (CF_ITEMS | {"fcf"}) else None
            if stmt:
                _upsert_hist(s, cid, year, stmt, item, v)

    years = sorted(by_year)
    if years:
        ly = years[-1]
        yd = by_year[ly]
        _w = _magnitude_warnings(yd)
        if _w:
            _log.warning("ingest %s FY%s magnitude sanity: %s",
                         (co.ticker or "").upper(), ly, "; ".join(_w))
        if "revenue" in yd:
            _upsert_fact(s, cid, ly, K.REVENUE, yd["revenue"])
        if "pat" in yd:
            _upsert_fact(s, cid, ly, K.NET_PROFIT, yd["pat"])
        nw = yd.get("net_worth") or yd.get("equity")
        if nw:
            _upsert_fact(s, cid, ly, K.NET_WORTH, nw)
        if yd.get("borrowings") is not None:
            _upsert_fact(s, cid, ly, K.NET_DEBT, yd["borrowings"] - (yd.get("cash") or 0))
        # Corrected shares: PAT / EPS (gives crore shares), else balance-sheet count.
        sh = None
        if yd.get("_eps") and yd.get("pat"):
            sh = yd["pat"] / yd["_eps"]
        elif yd.get("_shares_bal"):
            sh = yd["_shares_bal"]
            if sh > 1e7:        # absolute share count → crore
                sh = sh / 1e7
        if sh and 0.1 < sh < 100000:
            co.shares_outstanding = round(sh, 2)
    return len(years)


def _price_from_stock(s, co, stock):
    cp = (stock or {}).get("currentPrice") or {}
    price = cp.get("NSE") or cp.get("BSE")
    if price:
        price = round(float(price), 2)
        # Stamp as_of on EVERY update. It used to be set only on insert (the
        # server_default), so snapshots carried their creation time forever —
        # which made freshness unknowable (the cross-check flagged all 101
        # names) and would let the Dhan snapshot-sync overwrite prices
        # IndianAPI had just refreshed.
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        if co.market:
            co.market.price = price
            co.market.as_of = now
        else:
            s.add(models.MarketSnapshot(company_id=co.id, price=price, as_of=now))
        return price
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Insight layer: analyst consensus, peers, target, forecasts, ratios, growth,
# latest quarter — plus an insurer fallback for companies /stock can't cover.
# Everything here is best-effort: a failure never breaks the core statements.
# ─────────────────────────────────────────────────────────────────────────────

# ── Endpoints the vendor took OFF-PLAN on 24 Jul 2026 (DATA-12) ──────────────
# /documents answers 404 "Endpoint not allowed"; /historical_stats answers HTTP
# 200 with the body {"info": "Not a valid script_code"} for every name. Neither
# can return data, yet every refresh kept asking: 3 /historical_stats calls per
# name (_ratios, _growth, _results_snapshot), a 4th for lenders
# (_financial_pl_supplement), plus 1 /documents call — ~4 of the ~10 vendor
# calls we spend per name bought nothing. Over a ~1,000-name refresh that is
# ~4,000 of a 10,000-call monthly budget, enough for api_budget's pre-flight to
# refuse a run over spend that could never have returned anything.
#
# These flags gate the REQUEST only. Every parser, call site and merge below is
# untouched, so re-enabling is a one-line flip once a manual probe (one curl,
# not a full-universe run) shows the endpoint answering again. Same shape as
# documents_routes._LIVE_BSE_OVERLAY, which parked the dead BSE overlay.
#
# Not asking also makes health MORE truthful, not less: /documents was feeding
# vendor_meter a guaranteed failure and /historical_stats three guaranteed
# successes into every name's ~10 outcomes, diluting the 80%-of-20 failure floor
# in both directions.
_HISTORICAL_STATS_ON_PLAN = False
_DOCUMENTS_ON_PLAN = False

# A body whose ONLY keys are these is the vendor reporting a failure, not data.
# Deliberately WIDER than vendor_meter._ENVELOPE_KEYS, which excludes "info" on
# purpose so an off-plan endpoint does not read as a dead vendor on /api/health.
# That is right for a health gauge and wrong for a writer: to a caller that
# STORES the result, {"info": "Not a valid script_code"} must mean NO DATA.
_ERROR_ENVELOPE_KEYS = frozenset({"error", "info", "message", "detail"})


def _endpoint_on_plan(path) -> bool:
    """False for a vendor endpoint we know cannot answer (flags above)."""
    if path == "/historical_stats":
        return _HISTORICAL_STATS_ON_PLAN
    if path == "/documents":
        return _DOCUMENTS_ON_PLAN
    return True


def _get_safe(path, params):
    """Fast, single-shot GET for OPTIONAL insight sections — no retries/backoff
    so a flaky endpoint fails instantly instead of stalling the whole run.
    Returns None on any failure, and treats IndianAPI's [{'error': ...}, <code>]
    payload (HTTP 200 wrapping an internal error) as a failure too.

    Every call also reports its OUTCOME to vendor_meter.record (spend was
    already tick()ed). These are per-name lookups that may legitimately answer
    empty (a name with no forecasts, no peers), so empty is not a failure; a
    non-200, a transport error or an error envelope is.

    An endpoint the vendor has taken off-plan is short-circuited BEFORE the tick
    (see _endpoint_on_plan), so a call that cannot answer costs neither quota nor
    an outcome. For one that IS made, the health verdict and the caller's verdict
    are deliberately different: a 200 {"info": ...} is recorded as upstream
    ANSWERING (payload_ok's rule — off-plan is not a dead vendor) but returned to
    the caller as None, because it is not data and callers STORE what they get."""
    if not _endpoint_on_plan(path):
        return None
    if not KEY:
        return None
    global _CALL_COUNT
    _CALL_COUNT += 1                            # FIX-07: insight calls count too
    from app import vendor_meter; vendor_meter.tick()
    try:
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params, timeout=30)
        if r.status_code != 200:
            vendor_meter.record(False)
            return None
        data = r.json()
    except Exception:
        vendor_meter.record(False)
        return None
    vendor_meter.record(vendor_meter.payload_ok(data, empty_ok=True))
    # DATA-12, second half: an ERROR-SHAPED dict is not data. Since 24 Jul 2026
    # /historical_stats answers 200 {"info": "Not a valid script_code"} for every
    # name — a NON-EMPTY dict, so _ratios/_growth returned it as a RESULT,
    # _build_insight's `if v` filter kept it, and the DATA-12 merge (which only
    # protects keys ABSENT from the fresh blob) wrote it OVER the stored
    # last-good ratios/growth on every single refresh. /api/operations then
    # dropped the name entirely (no metric is findable inside {"info": ...})
    # while coverage's has_ratios still answered True. Returning None makes the
    # key absent — exactly the case the merge already handles correctly: the
    # stored value survives untouched.
    if isinstance(data, dict) and data and set(data) <= _ERROR_ENVELOPE_KEYS:
        return None
    if isinstance(data, list) and data and (
        (isinstance(data[-1], int) and data[-1] >= 400) or
        (isinstance(data[0], dict) and "error" in data[0])
    ):
        return None
    return data


def _num(x):
    """Clean an IndianAPI value (HTML whitespace, commas, %, ₹) → float|None."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s2 = re.sub(r"[,\s₹%]", "", str(x))
    if s2 in ("", "-", "—", "NA", "N/A"):
        return None
    try:
        return float(s2)
    except ValueError:
        return None


def _ticker_id(stock):
    """Company's own IndianAPI id (S00xxxxx) — reliably under corporate actions."""
    ca = (stock or {}).get("stockCorporateActionData") or {}
    for sub in ("boardMeetings", "dividend", "annualGeneralMeeting", "splits", "bonus", "rights"):
        for row in (ca.get(sub) or []):
            tid = (row or {}).get("tickerId")
            if tid:
                return str(tid)
    return None


def _bonus_ratio_factor(remarks):
    """Bonus 'in the ratio of A:B' → PRE-event price multiplier B/(A+B).
    A new shares issued for every B held ⇒ 1 old share → (A+B)/B shares, so an
    old close must be multiplied by B/(A+B) to sit on the post-bonus basis.
    Returns None when no A:B ratio can be parsed (so the event is skipped, never
    mis-scaled)."""
    if not remarks:
        return None
    m = re.search(r"ratio\s+of\s+(\d+)\s*:\s*(\d+)", remarks, re.I) \
        or re.search(r"(\d+)\s*:\s*(\d+)", remarks)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a + b <= 0:
        return None
    return b / (a + b)


def _corporate_actions(stock):
    """Normalize stockCorporateActionData into
    [{action_type, ex_date, record_date, value, ratio, raw_remarks}].

    dividend → per-share `value`, ratio None.
    split    → ratio = newFaceValue/oldFaceValue (pre-event price multiplier).
    bonus    → ratio parsed from `remarks` (see _bonus_ratio_factor).
    Rows lacking an ex-date or an interpretable magnitude are dropped."""
    ca = (stock or {}).get("stockCorporateActionData") or {}
    out = []
    for d in (ca.get("dividend") or []):
        v = _num(d.get("value"))
        ex = (d.get("xdDate") or d.get("recordDate") or "")[:10]
        if v and v > 0 and ex:
            out.append({"action_type": "dividend", "ex_date": ex,
                        "record_date": (d.get("recordDate") or "")[:10] or None,
                        "value": float(v), "ratio": None,
                        "raw_remarks": d.get("remarks")})
    for sp in (ca.get("splits") or []):
        old, new = _num(sp.get("oldFaceValue")), _num(sp.get("newFaceValue"))
        ex = (sp.get("xsDate") or sp.get("recordDate") or "")[:10]
        if old and new and old > 0 and new > 0 and ex:
            out.append({"action_type": "split", "ex_date": ex,
                        "record_date": (sp.get("recordDate") or "")[:10] or None,
                        "value": None, "ratio": float(new) / float(old),
                        "raw_remarks": sp.get("remarks")})
    for bo in (ca.get("bonus") or []):
        ex = (bo.get("xbDate") or bo.get("recordDate") or "")[:10]
        factor = _bonus_ratio_factor(bo.get("remarks"))
        if ex and factor:
            out.append({"action_type": "bonus", "ex_date": ex,
                        "record_date": (bo.get("recordDate") or "")[:10] or None,
                        "value": None, "ratio": factor,
                        "raw_remarks": bo.get("remarks")})
    return out


def _save_corporate_actions(s, co, stock):
    """Purge + re-insert this company's corporate actions (idempotent). Returns
    the number of events written. Unparseable bonus remarks are logged so a
    silent mis-scale can never hide."""
    rows = _corporate_actions(stock)
    # Surface bonus rows we could not parse — better a visible gap than a wrong factor.
    ca = (stock or {}).get("stockCorporateActionData") or {}
    for bo in (ca.get("bonus") or []):
        if (bo.get("xbDate") or bo.get("recordDate")) and _bonus_ratio_factor(bo.get("remarks")) is None:
            print(f"    ⚠ unparseable bonus ratio for {co.ticker}: {bo.get('remarks')!r}")
    # An ABSENT payload is "we learned nothing", never "there are no corporate
    # actions". Deleting first and finding nothing to re-insert wipes this
    # company's whole split/bonus history — and app/corporate_actions.price_factor
    # reads that history to adjust prices, so the loss is silent and then wrong
    # rather than merely empty.
    #
    # This is the DATA-12 shape exactly: on 24 Jul 2026 a vendor endpoint started
    # returning 404 and a blind replace wiped ~378 of ~736 names before anyone
    # noticed. That was fixed by merging instead of replacing; this call site
    # kept the old pattern. Purge only when the vendor actually gave us rows to
    # put back.
    if not rows:
        if (stock or {}).get("stockCorporateActionData"):
            print(f"    ⚠ {co.ticker}: corporate-action block present but no rows parsed — "
                  f"keeping the {s.query(models.CorporateAction).filter_by(company_id=co.id).count()} "
                  f"stored event(s) rather than blind-purging")
        return 0
    s.query(models.CorporateAction).filter_by(company_id=co.id).delete(synchronize_session=False)
    seen = set()
    for a in rows:
        key = (a["action_type"], a["ex_date"], a["value"])
        if key in seen:
            continue
        seen.add(key)
        s.add(models.CorporateAction(company_id=co.id, source="indianapi", **a))
    return len(seen)


def _analyst(stock):
    """Consensus rating + distribution from analystView / recosBar."""
    bar = (stock or {}).get("recosBar") or {}
    details = (stock or {}).get("stockDetailsReusableData") or {}
    view = (stock or {}).get("analystView") or []
    dist = [
        {"rating": v.get("ratingName"), "count": _num(v.get("numberOfAnalystsLatest"))}
        for v in view if v.get("ratingName") and v.get("ratingName") != "Total"
    ]
    if not dist and not bar:
        return None
    return {
        "rating": details.get("averageRating") or None,
        "mean_value": _num(bar.get("meanValue")),
        "num_analysts": _num(bar.get("noOfRecommendations")),
        "bullish_pct": _num(bar.get("tickerPercentage")),
        "distribution": dist,
    }


def _peers(stock):
    """Peer comp table from companyProfile.peerCompanyList."""
    prof = (stock or {}).get("companyProfile") or {}
    out = []
    for p in (prof.get("peerCompanyList") or []):
        out.append({
            "name": p.get("companyName"),
            "ticker_id": p.get("tickerId"),
            "price": _num(p.get("price")),
            "pe": _num(p.get("priceToEarningsValueRatio")),
            "pb": _num(p.get("priceToBookValueRatio")),
            "roe_ttm": _num(p.get("returnOnAverageEquityTrailing12Month")),
            "npm_ttm": _num(p.get("netProfitMarginPercentTrailing12Month")),
            "div_yield": _num(p.get("dividendYieldIndicatedAnnualDividend")),
            "mcap": _num(p.get("marketCap")),
            "rating": p.get("overallRating"),
        })
    return out or None


def _ratios(ticker):
    """Screener-style ROCE%, Debtor Days, etc. (12-yr series)."""
    r = _get_safe("/historical_stats", {"stock_name": ticker, "stats": "ratios"})
    return r if isinstance(r, dict) and r else None


def _growth(ticker):
    """Compounded sales/profit growth + ROE over 10/5/3yr/TTM."""
    r = _get_safe("/historical_stats", {"stock_name": ticker, "stats": "profit_loss_stats"})
    return r if isinstance(r, dict) and r else None


def _parse_hist_series(r):
    """Pull [{date, value}, …] from the IndianAPI/Screener historical shapes.

    The P/E response bundles SEVERAL datasets (the price-derived P/E line, an EPS
    line, a flat median marker). They must NOT be merged — that contaminated the
    band (EPS pushed the max to absurd levels). We pick the SINGLE dataset with
    the most points: the actual P/E line is daily/weekly (hundreds of points)
    while EPS/median are quarterly or 2-point lines."""
    if not r:
        return None
    if isinstance(r, dict):
        ds = r.get("datasets") or (r.get("body") or {}).get("datasets")
        if isinstance(ds, list) and ds:
            best = None
            for d in ds:
                vals = []
                for pair in (d.get("values") or []):
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                        v = _num(pair[1])
                        if v is not None and 0 < v < 300:   # drop loss-year / junk multiples
                            vals.append({"date": str(pair[0]), "value": v})
                if vals and (best is None or len(vals) > len(best)):
                    best = vals
            if best:
                return best
        body = r.get("body") if isinstance(r.get("body"), dict) else r
        vals = [{"date": str(k), "value": _num(v)} for k, v in body.items() if _num(v) is not None]
        if vals:
            return vals
    return None


def _pe_history(ticker, debug=False):
    """Multi-year P/E series for a valuation band ('cheap/expensive vs its own
    history'). IndianAPI's /historical_data with filter=pe mirrors Screener's
    P/E chart, so no price×EPS reconstruction is needed."""
    for params in ({"stock_name": ticker, "period": "10yr", "filter": "pe"},
                   {"stock_name": ticker, "period": "max", "filter": "pe"}):
        r = _get_safe("/historical_data", params)
        if debug:
            import json as _j
            print(f"    [pe_history probe] {params} -> {_j.dumps(r)[:300] if r else r}")
        series = _parse_hist_series(r)
        if series:
            return series
    return None


def _latest_quarter(ticker):
    """Latest quarter + TTM snapshot (sales, net profit, EPS, OPM…)."""
    out = {}
    for stat, label in (("quarter_results", "quarter"), ("ttm_results", "ttm")):
        r = _get_safe("/statement", {"stock_name": ticker, "stats": stat})
        if isinstance(r, dict):
            out[label] = {k: _num(v) for k, v in r.items() if _num(v) is not None}
    return out or None


_QMON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
         "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _qkey(p):
    parts = str(p).split()
    if len(parts) == 2:
        try:
            return (int(parts[1]), _QMON.get(parts[0][:3].lower(), 0))
        except (ValueError, TypeError):
            return (0, 0)
    try:
        return (int(parts[0]), 0)
    except (ValueError, TypeError, IndexError):
        return (0, 0)


def _results_snapshot(ticker):
    """Compact latest-quarter results + YoY (vs the same quarter a year ago),
    from /historical_stats quarter_results — so the cross-company earnings
    scoreboard reads stored data instead of 50 live calls. Sector-agnostic:
    we name-match Sales / Net Profit / EPS / OPM rather than assume a template."""
    r = _get_safe("/historical_stats", {"stock_name": ticker, "stats": "quarter_results"})
    if not isinstance(r, dict) or not r:
        return None
    labels = set()
    for series in r.values():
        if isinstance(series, dict):
            labels.update(series.keys())
    periods = sorted(labels, key=_qkey)
    if not periods:
        return None
    last = periods[-1]
    yago = periods[-5] if len(periods) >= 5 else None

    def pick(names):
        for n in names:
            for mname, series in r.items():
                if isinstance(series, dict) and n in mname.lower():
                    return mname
        return None

    def val(metric, period):
        s = r.get(metric)
        return _num(s.get(period)) if (isinstance(s, dict) and period) else None

    def yoy(metric):
        if not metric or not yago:
            return None
        a, b = val(metric, last), val(metric, yago)
        if a is None or not b:
            return None
        try:
            return a / b - 1.0
        except ZeroDivisionError:
            return None

    sales_m = pick(["sales", "revenue", "total income", "interest earned", "income"])
    pat_m   = pick(["net profit", "profit after tax", "pat", "profit for", "net income"])
    eps_m   = pick(["eps"])
    opm_m   = pick(["opm", "operating margin", "financing margin", "npm"])
    return {
        "quarter": last,
        "sales": val(sales_m, last), "pat": val(pat_m, last),
        "eps": val(eps_m, last), "opm": val(opm_m, last),
        "sales_yoy": yoy(sales_m), "pat_yoy": yoy(pat_m),
    }


def _target(ticker):
    """Analyst consensus target price. NB: /stock_target_price's `stock_id`
    param actually wants the TICKER NAME (confirmed), not the S00xxxxx id."""
    r = _get_safe("/stock_target_price", {"stock_id": ticker})
    if not isinstance(r, dict):
        return None
    pt = r.get("priceTarget") or {}
    mean = _num(pt.get("Mean")) or _num(pt.get("UnverifiedMean")) or _num(pt.get("PreliminaryMean"))
    if mean is None:
        return None
    # Consensus target as it stood at earlier points in time (Age = OneWeekAgo,
    # OneMonthAgo, …) — lets the Analyst tab show how the Street's target has
    # trended. This is still AGGREGATE consensus; IndianAPI does not expose
    # individual broker names/targets.
    snaps = []
    for sn in ((r.get("priceTargetSnapshots") or {}).get("PriceTargetSnapshot") or []):
        m = _num(sn.get("Mean"))
        if m is None:
            continue
        snaps.append({"age": sn.get("Age"), "mean": m,
                      "high": _num(sn.get("High")), "low": _num(sn.get("Low")),
                      "n_estimates": _num(sn.get("NumberOfEstimates"))})
    return {
        "mean": mean,
        "median": _num(pt.get("Median")),
        "high": _num(pt.get("High")),
        "low": _num(pt.get("Low")),
        "n_estimates": _num(pt.get("NumberOfEstimates")),
        "std": _num(pt.get("StandardDeviation")),
        "currency": pt.get("CurrencyCode") or "INR",
        "snapshots": snaps,
    }


def _forecasts(ticker):
    """Forward EPS + revenue estimates. Same misnamed param → use ticker name.
    Stores raw responses; the precise shape is parsed by the /insights route."""
    out = {}
    for code, label in (("EPS", "eps"), ("SAL", "revenue")):
        r = _get_safe("/stock_forecasts", {
            "stock_id": ticker, "measure_code": code, "period_type": "Annual",
            "data_type": "Estimates", "age": "Current"})
        if r is not None and not (isinstance(r, list) and r and isinstance(r[0], dict) and "error" in r[0]):
            out[label] = r
    return out or None


def _pick_key(d, *names):
    """Case/punctuation-insensitive key lookup: _pick_key(r, 'annual_reports')
    matches 'Annual_Reports', 'annualReports', 'Annual Reports', …"""
    if not isinstance(d, dict):
        return None
    wanted = {_norm(n) for n in names}
    for k, v in d.items():
        if _norm(k) in wanted:
            return v
    return None


def _entry_field(e, *names):
    """First matching field of a raw entry dict (case-insensitive), else None."""
    if not isinstance(e, dict):
        return None
    v = _pick_key(e, *names)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _documents(ticker):
    """Company documents from /documents, normalised to a stable shape:
      {"concalls":       [{date, transcript, ppt, summary}],   ≤ 12
       "annual_reports": [{year, url}],                        ≤ 10
       "credit_ratings": [{date, title, url, source}],         ≤ 6
       "announcements":  [{title, date, url}]}                 ≤ 5
    Tolerates missing keys / case variants (raw shape uses keys like Concalls,
    Annual_Reports, Credit_Ratings, Recent_Announcements). Returns None when
    nothing useful came back, so the insight blob stays clean."""
    r = _get_safe("/documents", {"stock_name": ticker})
    if isinstance(r, list) and r and isinstance(r[0], dict):
        r = r[0]
    if not isinstance(r, dict):
        return None
    body = _pick_key(r, "data", "body")
    if isinstance(body, dict):
        r = {**r, **body}

    def _list(*names):
        v = _pick_key(r, *names)
        return v if isinstance(v, list) else []

    out = {}
    concalls = []
    for e in _list("concalls", "con_calls", "earnings_calls")[:12]:
        concalls.append({
            "date":       _entry_field(e, "date", "concall_date", "time"),
            "transcript": _entry_field(e, "transcript", "transcript_link", "transcript_url"),
            "ppt":        _entry_field(e, "ppt", "ppt_link", "presentation", "presentation_link"),
            "summary":    _entry_field(e, "summary", "summary_link", "notes"),
        })
    if concalls:
        out["concalls"] = concalls

    reports = []
    for e in _list("annual_reports", "annualreport", "annual_report")[:10]:
        reports.append({
            "year": _entry_field(e, "year", "financial_year", "fy", "date"),
            "url":  _entry_field(e, "url", "link", "report_link", "pdf"),
        })
    if reports:
        out["annual_reports"] = reports

    ratings = []
    for e in _list("credit_ratings", "creditrating", "ratings")[:6]:
        ratings.append({
            "date":   _entry_field(e, "date", "rating_date"),
            "title":  _entry_field(e, "title", "rating", "name", "description"),
            "url":    _entry_field(e, "url", "link", "report_link"),
            "source": _entry_field(e, "source", "agency", "rating_agency"),
        })
    if ratings:
        out["credit_ratings"] = ratings

    anns = []
    for e in _list("recent_announcements", "announcements", "recent_announcement")[:5]:
        anns.append({
            "title": _entry_field(e, "title", "subject", "headline", "description"),
            "date":  _entry_field(e, "date", "announcement_date", "time"),
            "url":   _entry_field(e, "url", "link", "attachment"),
        })
    if anns:
        out["announcements"] = anns

    return out or None


def _build_insight(s, co, stock, debug=False):
    """Assemble + upsert a CompanyInsight row. Returns a short status string."""
    ticker = (co.ticker or "").upper()
    tid = _ticker_id(stock)
    data = {}

    # ARC-03: each of these parse steps used to swallow its exception to `pass`
    # with no trace, so a persistently-failing vendor field became invisible
    # data loss ("no data" not "broken parse"). `_field` runs the parser and
    # logs a DEBUG line on failure — enough to diagnose an ingestion gap without
    # crashing the per-company ingest (any one field is optional).
    def _field(key, fn):
        try:
            data[key] = fn()
        except Exception as e:  # noqa: BLE001 — one bad field must not fail the row
            # DATA-08: WARNING, not DEBUG — both processes run at INFO, so a
            # persistently-failing vendor field was silent data loss before.
            _log.warning("ingest %s: %s field failed: %s: %s", ticker, key,
                         type(e).__name__, e)

    _field("analyst",  lambda: _analyst(stock))
    _field("peers",    lambda: _peers(stock))
    _field("ratios",   lambda: _ratios(ticker))
    _field("growth",   lambda: _growth(ticker))
    _field("latest_q", lambda: _latest_quarter(ticker))
    _field("results",  lambda: _results_snapshot(ticker))

    def _own():
        from app.ownership_logic import ownership_snapshot
        return ownership_snapshot(stock)   # reuses the in-hand /stock payload
    _field("ownership", _own)
    # Segment probe: discover whether the /stock payload carries business-segment
    # revenue/EBIT (for a future data-driven SOTP). Off unless SEGMENT_DEBUG_KEYS set.
    if os.environ.get("SEGMENT_DEBUG_KEYS") and isinstance(stock, dict):
        try:
            seg_keys = [k for k in stock.keys()
                        if any(w in str(k).lower() for w in ("segment", "geograph", "business", "division", "product"))]
            top = sorted(str(k) for k in stock.keys())
            print(f"  [segment-debug] {ticker} segment-like keys={seg_keys or 'NONE'} ; all top-level keys={top}")
        except Exception:
            pass
    _field("target",     lambda: _target(ticker))
    _field("forecasts",  lambda: _forecasts(ticker))
    _field("pe_history", lambda: _pe_history(ticker, debug=debug))
    _field("documents",  lambda: _documents(ticker))
    data = {k: v for k, v in data.items() if v}

    if debug and data.get("forecasts"):
        import json as _j
        for label, raw in data["forecasts"].items():
            preview = _j.dumps(raw)[:400]
            print(f"    ── forecast[{label}] shape: {preview}")

    row = s.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    if row:
        # DATA-12: MERGE the fresh blob over the stored one — never blind-replace.
        # `data` above drops any field that came back empty, so a vendor endpoint
        # that goes away silently produced a blob MISSING that key, and the old
        # `row.data = data` then DELETED the last-good value. Observed live on
        # 24 Jul 2026: the vendor began answering /documents with 404 "Endpoint
        # not allowed" (plan change) and /historical_stats with "Not a valid
        # script_code", so a full-universe refresh wiped stored documents for
        # ~378 names and quarterly results for ~736 — the same purge-before-
        # validate class as DATA-02 (statements), which _swap_statements already
        # guards. Fresh keys win; keys absent this run keep their last-good value
        # (stale-but-real beats deleted, and staleness is visible via as_of).
        merged = dict(row.data or {})
        merged.update(data)
        row.ticker_id, row.data = tid, merged
        # DATA-11: JSON column — force the UPDATE even if SQLAlchemy's identity
        # comparison thinks nothing changed.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(row, "data")
    else:
        s.add(models.CompanyInsight(company_id=co.id, ticker_id=tid, data=data))

    tags = []
    if data.get("analyst"):   tags.append(f"analyst({int(data['analyst'].get('num_analysts') or 0)})")
    if data.get("peers"):     tags.append(f"peers({len(data['peers'])})")
    if data.get("ratios"):    tags.append("ratios")
    if data.get("growth"):    tags.append("growth")
    if data.get("latest_q"):  tags.append("Q")
    if data.get("target", {}).get("mean"): tags.append("target")
    if data.get("forecasts"): tags.append("fwd")
    if data.get("documents"): tags.append("docs")
    return "id=" + (tid or "?") + " · " + (", ".join(tags) if tags else "no extras")


# Insurers / some banks have no INC/BAL/CAS block in /stock → 0 years.
# Pull a single latest period from /statement so the tabs aren't empty.
_STMT_FACT_MAP = {
    "sales": ("PL", "revenue"), "net_profit": ("PL", "pat"),
    "operating_profit": ("PL", "ebitda"), "profit_before_tax": ("PL", "pbt"),
    "depreciation": ("PL", "depreciation"), "interest": ("PL", "interest_expense"),
    "total_assets": ("BS", "total_assets"), "reserves": ("BS", "reserves"),
    "borrowings": ("BS", "borrowings"), "investments": ("BS", "investments"),
    "fixed_assets": ("BS", "fixed_assets"), "share_capital": ("BS", "equity"),
}


# Bank/NBFC P&L line items that the Reuters INC block in /stock does NOT carry
# (it only yields pbt/tax/pat for lenders). We pull them from /statement, where
# IndianAPI exposes the Screener-style bank P&L: interest earned, interest
# expended, operating expenses, provisions, other income.
# ONLY lender-specific lines that /stock does NOT carry. We deliberately do NOT
# include pbt/tax/pat here — /stock already provides those (at the correct annual
# scale); re-adding them from /statement would both collide on the unique key and
# risk mixing a quarterly figure with annual ones.
_FIN_PL_KEYS = {
    # PREFER the bank-specific interest-earned line over the generic revenue/sales
    # (total operating income). First-match wins, so revenue/sales first set
    # interest_income = TOTAL income → NII (= interest_income − interest_expense)
    # was overstated and total_income double-counted other income.
    "interest_income":   ("interest_earned", "interest_income", "revenue", "sales", "total_revenue"),
    "interest_expense":  ("interest", "interest_expended", "interest_expense", "finance_cost"),
    "other_income":      ("other_income",),
    "opex":              ("expenses", "operating_expenses", "operating_cost", "other_expenses"),
    "provisions":        ("provisions", "provisions_and_contingencies", "provisioning"),
}


# yoy_results (Screener annual P&L) line item → canonical lender line. For a
# lender, Revenue is gross interest earned, Interest is interest expended,
# Expenses is operating cost; NII is derived. Provisions are NOT a separate yoy
# line (Screener folds them into Expenses for banks), so they stay unpopulated.
_YOY_LENDER_MAP = {
    "Revenue": "interest_income",
    "Interest": "interest_expense",
    "Other Income": "other_income",
    "Expenses": "opex",
}


def _fy_from_period(period) -> int | None:
    """'Mar 2026' → 2026 (March-end FY). None for TTM / unparseable periods."""
    m = re.search(r"(19|20)\d\d", str(period or ""))
    return int(m.group(0)) if m else None


def _financial_pl_supplement(s, co, year=None):
    """FIX-08/DATA-03: enrich EVERY stored fiscal year's P&L for BANK/NBFC lenders
    with the interest-income/expense/NII/opex lines that /stock omits, read from
    the vendor's annual `yoy_results` table (Screener-style multi-year columns).
    STRICTLY ADDITIVE — only writes a line absent for that year.

    Was broken since inception: it hit /statement with the INVALID stat
    'profit_loss' (the vendor serves cashflow/yoy_results/quarter_results/… — no
    'profit_loss'), so every lender errored, and when the annual-scale guard
    tripped it `continue`d SILENTLY — HDFCBANK/ICICIBANK/SBIN carried nothing.
    Insurers are handled by _insurer_statements, not here. `year` is ignored
    (kept for call-site back-compat); all years are written."""
    ticker = (co.ticker or "").upper()
    if co.template_code == "INSURANCE":
        return 0
    # DATA-10: /historical_stats is a FUZZY NAME search with no exchange-code in
    # its payload, so it can silently resolve to a stranger (stock_name
    # "J&KBANK" returned a non-financial's Sales-shaped table, whose Interest/
    # Expenses lines were stored as J&K Bank's own — 19 lenders incl.
    # BAJFINANCE/RECLTD/HUDCO were contaminated this way). Three guards now:
    #   1. query by the identity-verified stored NAME (from /stock), falling
    #      back to the ticker only when no name is stored;
    #   2. the payload must be LENDER-shaped ("Revenue" present, no "OPM %" /
    #      "Sales" — the non-financial Screener shape means wrong entity or
    #      wrong template, either way not writable under lender line items);
    #   3. anchor identity on PBT: at least one overlapping year's
    #      "Profit before tax" must match the /stock-derived stored pbt within
    #      2% — /stock rows ARE exchange-code-verified, so agreement proves
    #      the stats payload describes the same company.
    query = (co.name or "").strip() or ticker
    r = _get_safe("/historical_stats", {"stock_name": query, "stats": "yoy_results"})
    if not isinstance(r, dict) or "error" in r:
        detail = r.get("error") if isinstance(r, dict) else type(r).__name__
        _log.warning("FIX-08 lender supplement: yoy_results unavailable for %s (%s)", ticker, detail)
        return 0
    if "Revenue" not in r or "OPM %" in r or "Sales" in r:
        _log.warning("DATA-10: %s yoy_results is not lender-shaped (keys %s) — "
                     "wrong entity or non-lender; nothing written", ticker, list(r)[:6])
        return 0
    pbt_series = r.get("Profit before tax")
    if isinstance(pbt_series, dict):
        stored_pbt = {h.fiscal_year: h.value for h in
                      s.query(models.HistoricalFinancial)
                       .filter_by(company_id=co.id, statement_type="PL",
                                  line_item="pbt").all()}
        anchored = disagreed = 0
        for period, raw in pbt_series.items():
            fy, v = _fy_from_period(period), _num(raw)
            if fy in stored_pbt and v is not None and stored_pbt[fy]:
                if abs(v - stored_pbt[fy]) <= 0.02 * abs(stored_pbt[fy]) + 1.0:
                    anchored += 1
                else:
                    disagreed += 1
        if anchored == 0 and disagreed > 0:
            _log.warning("DATA-10: %s yoy_results PBT disagrees with verified "
                         "/stock PBT on all %d overlapping years — identity "
                         "unproven, nothing written", ticker, disagreed)
            return 0
    by_year: dict[int, dict] = {}
    for yoy_line, canon in _YOY_LENDER_MAP.items():
        series = r.get(yoy_line)
        if not isinstance(series, dict):
            continue
        for period, raw in series.items():
            fy, v = _fy_from_period(period), _num(raw)
            if fy is not None and v is not None:
                by_year.setdefault(fy, {})[canon] = v
    wrote = 0
    for fy, vals in sorted(by_year.items()):
        ii, ie = vals.get("interest_income"), vals.get("interest_expense")
        # A lender's gross interest earned must exceed its interest bill; a year
        # that fails is mis-scaled/mislabelled — skip it VISIBLY, never silently.
        if ii is not None and ie is not None:
            if ii <= ie:
                _log.warning("FIX-08: %s FY%s interest_income %.0f <= expense %.0f — skipped", ticker, fy, ii, ie)
                continue
            vals["nii"] = ii - ie
        for canon, v in vals.items():
            existing = (s.query(models.HistoricalFinancial)
                          .filter_by(company_id=co.id, fiscal_year=fy,
                                     statement_type="PL", line_item=canon).first())
            if existing is None:
                _upsert_hist(s, co.id, fy, "PL", canon, v)
                wrote += 1
    return wrote


def _should_refresh_identity(stock, ticker, stored_name) -> bool:
    """Identity-refresh trigger (DATA-01 follow-up): the vendor CONFIRMS this
    ticker (its NSE code == ticker) but the stored name no longer matches the
    vendor's, and it isn't a mere Ltd/suffix variant (neither name contains the
    other) — i.e. a curated row still carrying a PAST mis-resolution's label."""
    prof = (stock or {}).get("companyProfile") or {}
    nse = _norm_sym(prof.get("exchangeCodeNse"))
    vn = _norm_sym((stock or {}).get("companyName"))
    sn = _norm_sym(stored_name)
    return bool(nse and nse == _norm_sym(ticker) and vn and sn
                and vn not in sn and sn not in vn)


def _fy_label(today=None) -> int:
    """DATA-05: the latest COMPLETED Indian fiscal year (FY ends 31 Mar, labelled
    by its ending year). It is the current calendar year only from April onward;
    a Jan–Mar ingest belongs to the PRIOR FY. The old `date.today().year` stamped
    Jan–Mar data one year forward, minting phantom future "years" on re-ingest."""
    import datetime
    t = today or datetime.date.today()
    return t.year if t.month >= 4 else t.year - 1


def _insurer_statements(s, co):
    """Fallback statements via /statement for companies /stock can't cover."""
    ticker = (co.ticker or "").upper()
    year = _fy_label()
    wrote = 0
    # profit_loss is the annual Screener-style P&L; quarter_results is a single
    # QUARTER. Writing quarterly P&L lines under a fiscal-year column is exactly
    # the LICHSGFIN class of inaccuracy, so quarter_results may only contribute
    # point-in-time BALANCE-SHEET lines, never P&L.
    for stat in ("profit_loss", "quarter_results", "balancesheet"):
        r = _get_safe("/statement", {"stock_name": ticker, "stats": stat})
        if not isinstance(r, dict):
            continue
        for raw_key, (stmt, canon) in _STMT_FACT_MAP.items():
            if stat == "quarter_results" and stmt == "PL":
                continue
            v = _num(r.get(raw_key))
            if v is not None:
                _upsert_hist(s, co.id, year, stmt, canon, v)
                wrote += 1
        if stat != "quarter_results":
            rev, pat = _num(r.get("sales")), _num(r.get("net_profit"))
            if rev is not None: _upsert_fact(s, co.id, year, K.REVENUE, rev)
            if pat is not None: _upsert_fact(s, co.id, year, K.NET_PROFIT, pat)
    return wrote


def _resolve_onboarding(ticker, stock, cur_sector, cur_name):
    """Resolve (name, sector, template_code, type, is_fallback) for a freshly
    onboarded company row from its /stock payload.

    Fixes the latent long-tail bug: an auto-created row has a placeholder name
    and no sector/template, so WITHOUT this every new non-financial name was
    silently valued as a generic manufacturer (rich multiples, absurd upside).
    `is_fallback` is True when the sector could not be classified and we defaulted
    to MANUFACTURING — the caller logs it so a wrong-multiple verdict on the long
    tail is visible, not silent."""
    prof = (stock or {}).get("companyProfile") or {}
    name = (stock or {}).get("companyName") or cur_name or ticker
    sector = (stock or {}).get("industry") or prof.get("mgIndustry") or cur_sector
    tmpl = T.classify_company(name, sector)
    typ = "financial" if T.is_financial(tmpl) else "nonfinancial"
    # is_fallback keys off the VALUATION sector (which drives the multiple), not
    # the coarser P&L template — steel legitimately uses a manufacturing-shaped
    # statement but a METAL multiple, so that is NOT a fallback. A true fallback
    # is when the sector string can't be classified and the multiple defaults.
    vs = SP.TICKER_OVERRIDES.get((ticker or "").upper()) or SP.classify_valuation_sector(sector, tmpl)
    is_fallback = (vs == SP.DEFAULT_SECTOR) and \
                  (not sector or SP.classify_valuation_sector(sector, None) == SP.DEFAULT_SECTOR)
    return name, sector, tmpl, typ, is_fallback


# Task #123: the vendor /stock lookup is NAME-based; for these tickers the bare
# symbol (or the junk auto-seeded name, e.g. "Tatamotors") resolves to nothing,
# so the row stayed a NO-DATA stub forever. Aliases map ticker → the query
# string the vendor actually recognises. Unknown-to-us names fall back to the
# stored company name (cleaned) as a second attempt.
VENDOR_QUERY_ALIAS: dict[str, str] = {
    "TATAMOTORS": "Tata Motors",
    "EQUITASBNK": "Equitas Small Finance Bank",
    # GSPC group consolidation: GSPC+GSPL+GSPC Energy merged INTO Gujarat Gas
    # (eff. 1 May 2026) → renamed "Gujarat Energy" (14 May) → NSE SYMBOL
    # renamed GUJGASLTD→GUJENERGY (1 Jul 2026). Vendor resolves only the new
    # name; its exchangeCodeNse is GUJENERGY (identity guard verified).
    "GUJENERGY":  "Gujarat Energy",
    "AGARWALEYE": "Dr Agarwals Health Care",
    "RAIN":       "Rain Industries",
    "TI":         "Tilaknagar Industries",     # NSE symbol renamed to TI (2025)
    "CCAVENUE":   "Infibeam Avenues",          # CCAvenue is Infibeam's brand/symbol
    "SKFINDUS":   "SKF India Industrial",      # SKF demerger entity
    "KISSHT":     "OnEMI Technology Solutions",
    "KSHINTL":    "KSH International",
    "VEDPOWER":   "Vedanta Power",
    "AGL":        "Allcargo Global",
    "APOLLO":     "Apollo Micro Systems",   # NSE symbol APOLLO = Apollo Micro
                                             # Systems (defence electronics);
                                             # Apollo HOSPITALS is APOLLOHOSP.
                                             # Bare "APOLLO" fuzzy-resolved to
                                             # Apollo Hospitals (DATA-01).
    "ANUP":       "Anup Engineering",      # DATA-01: bare ANUP fuzzy-hit Anupam Rasayan (ANURAS)
    "CERA":       "Cera Sanitaryware",     # DATA-01: bare CERA fuzzy-hit Kajaria Ceramics (KAJARIACER)
    "PTC":        "PTC India",             # DATA-01: bare PTC fuzzy-hit PTC Industries (PTCIL)
    "TRIVENI":    "Triveni Engineering",   # DATA-01: bare TRIVENI fuzzy-hit Triveni Turbine (TRITURBINE)
}


class VendorIdentityMismatch(Exception):
    """DATA-01/FIX-03: /stock is a fuzzy NAME search and resolved to a company
    whose exchange symbol is NOT the requested ticker (the vendor's closest hit,
    not our security). Raised so the caller stores NOTHING rather than another
    company's numbers — VAML and VISL both resolved to a single micro Vedanta-
    segment entity and were stored as byte-identical clones before this guard."""

    def __init__(self, ticker: str, vendor_symbol, vendor_name):
        self.ticker = ticker
        self.vendor_symbol = vendor_symbol
        self.vendor_name = vendor_name
        super().__init__(f"/stock for {ticker} resolved to {vendor_name!r} "
                         f"(NSE {vendor_symbol!r}) — identity mismatch, not stored")


def _norm_sym(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def _identity_ok(stock, ticker: str, co_bse: str | None = None) -> bool:
    """True when the payload is provably the requested company, or when identity
    cannot be DISPROVEN. False only on a positive contradiction.

    Anchored on the immutable exchange symbol, never the name: a prior mis-onboard
    may already have overwritten `co.name` with the wrong entity's name, so a
    name comparison would rubber-stamp the very contamination we're catching.
    The vendor exposes `companyProfile.exchangeCodeNse` (== our ticker for a
    correct hit — verified: TCS→'TCS') and `exchangeCodeBse`. When the NSE code
    is present it MUST equal the ticker; else fall back to the BSE code vs our
    stored `bse_scrip_code`; when the payload exposes no exchange symbol at all
    we cannot disprove identity and keep today's behaviour (no regression)."""
    prof = (stock or {}).get("companyProfile") or {}
    tk = _norm_sym(ticker)
    if not tk:
        return True
    nse = _norm_sym(prof.get("exchangeCodeNse"))
    if nse:
        if nse == tk:
            return True
        # Corporate-action allow-list: an ISIN-CONTINUOUS rename can present a
        # different NSE symbol than our ticker. Accept ONLY the explicitly
        # verified alias for this ticker — never a general fuzzy match — so the
        # contamination guard stays fully closed for every other name. A
        # new-ISIN demerger (e.g. TATAMOTORS→TMCV) carries no alias and stays
        # blocked by design.
        from app.corporate_events import verified_vendor_alias
        alias = verified_vendor_alias(ticker)
        return bool(alias) and nse == _norm_sym(alias)
    bse_ret, bse_have = _norm_sym(prof.get("exchangeCodeBse")), _norm_sym(co_bse)
    if bse_ret and bse_have:
        return bse_ret == bse_have
    return True


def _fetch_stock(ticker: str, co_name: str | None = None, co_bse: str | None = None):
    """/stock with alias + stored-name fallback: try the alias (or bare ticker),
    then the cleaned company name when the first attempt fails/returns junk.

    FIX-03: PREFER a query form whose payload identity-matches the ticker; only
    when every usable form resolves to the WRONG company do we raise
    VendorIdentityMismatch (so the caller stores nothing instead of a clone)."""
    queries = [VENDOR_QUERY_ALIAS.get(ticker, ticker)]
    if co_name:
        clean = re.sub(r"\b(ltd|limited)\.?$", "", co_name.strip(), flags=re.I).strip(" .,")
        if clean and clean.upper() != ticker and clean not in queries:
            queries.append(clean)
    last_err = None
    mismatch = None
    for q in queries:
        try:
            stock = _get("/stock", {"name": q})
            if isinstance(stock, dict) and (stock.get("companyName") or stock.get("currentPrice")
                                            or stock.get("stockDetailsReusableData")):
                if _identity_ok(stock, ticker, co_bse):
                    return stock
                # usable but the WRONG company — remember it, try the next form
                prof = stock.get("companyProfile") or {}
                mismatch = (prof.get("exchangeCodeNse"), stock.get("companyName"))
        except Exception as e:  # noqa: BLE001 — try the next query form
            last_err = e
    if mismatch is not None:
        raise VendorIdentityMismatch(ticker, mismatch[0], mismatch[1])
    if last_err:
        raise last_err
    raise RuntimeError(f"/stock returned no usable payload for {ticker} (tried {queries})")


def _swap_statements(s, co, stock) -> int:
    """DATA-02/FIX-04: replace a company's HistoricalFinancial rows atomically.

    The old code purged ALL prior statements and only THEN parsed the fresh
    payload — so a vendor response that carried a price but an empty/absent
    financials block deleted a good 7-year history and left zero rows (SBILIFE
    observed wiped live, has_data:false). Here the purge + parse + insurer
    fallback run inside a SAVEPOINT; if the fresh payload yields nothing usable
    and a prior history existed, the savepoint is rolled back and the old rows
    stay intact. Returns the number of usable statement-years now stored.

    Safe because neither _parse_financials nor _insurer_statements commits — they
    only add/flush — so the savepoint fully owns their writes."""
    prior = (s.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id).count())
    sp = s.begin_nested()
    try:
        s.query(models.HistoricalFinancial).filter_by(
            company_id=co.id).delete(synchronize_session=False)
        s.flush()
        n = _parse_financials(s, co.id, co, stock)
        if n == 0:                       # insurer / 0-year fallback (SBILIFE, HDFCLIFE, …)
            try:
                w = _insurer_statements(s, co)
                if w:
                    n = w
                    print(f"    insurer fallback → {w} line items via /statement")
            except Exception as e:  # noqa: BLE001 — fallback is best-effort
                print(f"    insurer fallback failed — {e}")
        if n == 0 and prior > 0:
            sp.rollback()                # keep the good history — never wipe to zero
            print(f"    ⚠ fresh payload yielded 0 statements — kept {prior} prior "
                  f"rows intact (DATA-02 guard); nothing overwritten")
            return 0
        sp.commit()
        return n
    except Exception:
        sp.rollback()
        raise


def ingest_company(s, co, dump=False, insights=True):
    import json
    ticker = (co.ticker or "").upper()
    print(f"  {ticker}:")
    try:
        stock = _fetch_stock(ticker, co.name, co.bse_scrip_code)
    except VendorIdentityMismatch as e:
        # DATA-01: the vendor gave us a DIFFERENT security. Store nothing (this
        # returns BEFORE the pre-parse purge, so an existing good history is also
        # never wiped by a bad re-resolution) and mark the name unresolved so the
        # daily backfill stops retrying a query that will never converge.
        print(f"    IDENTITY MISMATCH — {e}")
        try:
            from app.coverage import mark_vendor_unresolved
            mark_vendor_unresolved(s, ticker, e.vendor_symbol)
        except Exception as me:  # noqa: BLE001 — marking is best-effort
            print(f"    (could not mark unresolved: {me})")
        return False
    except Exception as e:
        print(f"    /stock FAILED — {e}")
        return False

    price = _price_from_stock(s, co, stock)

    # Onboarding backfill — ONLY for freshly auto-created rows (placeholder
    # sector). Never touches curated names that already carry a real sector, so
    # the existing universe's classification is untouched. Sets a real
    # name/sector/template/type from the /stock profile BEFORE _parse_financials
    # and the lender-supplement branch (both key off co.type/template_code).
    if (co.sector or "").strip().lower() in ("", "unknown", "none"):
        name, sector, tmpl, typ, is_fallback = _resolve_onboarding(ticker, stock, co.sector, co.name)
        if name:
            co.name = name
        if sector:
            co.sector = sector
        co.template_code, co.type = tmpl, typ
        s.commit()
        note = " ⚠ unclassified sector → MANUFACTURING default (verify multiple)" if is_fallback else ""
        print(f"    onboarded: {co.name} · {sector} → {tmpl}/{typ}{note}")
    elif _should_refresh_identity(stock, ticker, co.name):
        # A curated row whose name was corrupted by a PAST mis-resolution keeps
        # the wrong name/sector even once the alias makes /stock resolve right
        # (the financials are already correct). Re-resolve the label — retires the
        # manual sector-reset used for APOLLO/ANUP/CERA/PTC/TRIVENI.
        name, sector, tmpl, typ, _fb = _resolve_onboarding(ticker, stock, None, co.name)
        if name and _norm_sym(name) != _norm_sym(co.name):
            _old = co.name
            co.name = name
            if sector:
                co.sector = sector
            co.template_code, co.type = tmpl, typ
            s.commit()
            print(f"    identity refresh: {_old!r} → {co.name} · {sector} → {tmpl}/{typ}")

    if dump:
        fin = stock.get("financials") or stock.get("stockFinancialData") or []
        print(f"    ── financials: {len(fin)} entries; first sections="
              f"{list((fin[0].get('stockFinancialMap') or {}).keys()) if fin else []}")

    # Replace the statements ATOMICALLY (DATA-02/FIX-04): the purge + fresh
    # parse run in a SAVEPOINT and are kept ONLY if the payload actually yielded
    # statements. An empty/partial vendor response no longer wipes a good
    # multi-year history to zero (SBILIFE was destroyed exactly this way).
    n = _swap_statements(s, co, stock)

    # Commit the CORE statements + facts + price FIRST, in their own transaction.
    # This way a later optional stage (lender supplement, insights) that errors
    # can be rolled back WITHOUT losing the authoritative /stock data.
    s.commit()

    # Corporate actions (dividends / splits / bonuses) → total-return math and
    # the price back-adjustment engine. Isolated commit so a parse failure never
    # touches the core statements above.
    try:
        nca = _save_corporate_actions(s, co, stock)
        s.commit()
        if nca:
            print(f"    corporate actions → {nca} events")
    except Exception as e:
        s.rollback()
        print(f"    corporate actions failed — {type(e).__name__}: {e}")

    # Banks/NBFCs: supplement the lender P&L (interest income/expense, NII,
    # provisions, opex) that /stock doesn't carry. STRICTLY ADDITIVE; written to
    # the SAME latest fiscal year as /stock. Isolated commit so a failure here
    # never touches the core statements above.
    if (co.type == "financial") or (co.template_code in ("BANK", "NBFC", "INSURANCE")):
        try:
            latest_year = (s.query(models.HistoricalFinancial.fiscal_year)
                             .filter_by(company_id=co.id, statement_type="PL")
                             .order_by(models.HistoricalFinancial.fiscal_year.desc())
                             .first())
            latest_year = latest_year[0] if latest_year else None
            w = _financial_pl_supplement(s, co)   # FIX-08: writes ALL years now
            if w:
                s.commit()
                print(f"    financial P&L supplement → {w} lender line items (all years)")
            else:
                s.rollback()
        except Exception as e:
            s.rollback()
            print(f"    financial P&L supplement failed — {e}")

    status = ""
    if insights:
        try:
            status = "  ·  " + _build_insight(s, co, stock, debug=dump)
            s.commit()
        except Exception as e:
            s.rollback()
            status = f"  ·  insight skipped ({type(e).__name__})"

    print(f"    price ✓ ₹{price} · {n} fiscal years · shares {co.shares_outstanding}{status}")
    return True


def refresh_price(s, co):
    try:
        stock = _fetch_stock((co.ticker or '').upper(), co.name, co.bse_scrip_code)
        p = _price_from_stock(s, co, stock)
        s.commit()
        if p:
            print(f"  {co.ticker}: ₹{p}")
            return True
    except Exception as e:
        s.rollback()
        print(f"  {co.ticker}: price ERROR — {e}")
    return False


# OFFICIAL Nifty 50 membership (niftyindices.com ind_nifty50list.csv,
# 2026-07-04). Post-demerger: TMPV here / TMCV in the Next 50; TATAMOTORS is
# retired from every index and from Dhan's scrip master (its DB history and
# track-record entries remain, they just stop refreshing).
NIFTY_50 = {
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO","BAJAJFINSV",
    "BAJFINANCE","BEL","BHARTIARTL","CIPLA","COALINDIA","DRREDDY","EICHERMOT","ETERNAL",
    "GRASIM","HCLTECH","HDFCBANK","HDFCLIFE","HINDALCO","HINDUNILVR","ICICIBANK","INDIGO",
    "INFY","ITC","JIOFIN","JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI","MAXHEALTH","NESTLEIND",
    "NTPC","ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA","TATACONSUM",
    "TATASTEEL","TCS","TECHM","TITAN","TMPV","TRENT","ULTRACEMCO","WIPRO",
}

# Held back at ANY tier when there's no defensible sector model — better hidden
# than mis-priced. INDIGO is now covered: the AVIATION valuation sector + its
# TICKER_OVERRIDE (sector arrives blank) price it on airline economics, so it no
# longer needs to be excluded.
EXCLUDED_TICKERS: set[str] = set()

# Names we actively cover BEYOND the Nifty 50. Every refresh path (intraday,
# daily EOD, weekly full, valuation precompute, screener scope) uses UNIVERSE,
# not bare NIFTY_50 — so adding a ticker here is the only step needed to
# onboard it (the scheduler auto-creates + ingests missing members on boot).
# Owner-portfolio names outside the Nifty 500 — onboarded so the book is
# fully covered (boot auto-classifies sector/template and ingests).
EXTRA_TICKERS = {"FEDFINA", "RATEGAIN", "IDEAFORGE"}
UNIVERSE = (NIFTY_50 | EXTRA_TICKERS) - EXCLUDED_TICKERS

# ── Nifty Next 50 — the second visibility tranche ────────────────────────────
# OFFICIAL membership (niftyindices.com ind_niftynext50list.csv, 2026-07-04).
# Exposing these is a VISIBILITY decision, not an ingest: the frontend reads
# the union from /api/universe, so there is no hand-maintained mirror to drift.
# INDIGO (now officially in the Nifty 50) is stripped by EXCLUDED_TICKERS.
NIFTY_NEXT_50 = {
    "ABB","ADANIENSOL","ADANIGREEN","ADANIPOWER","AMBUJACEM","BAJAJHLDNG","BANKBARODA",
    "BOSCHLTD","BPCL","BRITANNIA","CANBK","CGPOWER","CHOLAFIN","CUMMINSIND","DIVISLAB","DLF",
    "DMART","ENRIN","GAIL","GODREJCP","HAL","HDFCAMC","HINDZINC","HYUNDAI","INDHOTEL","IOC",
    "IRFC","JINDALSTEL","LODHA","LTM","MAZDOCK","MOTHERSON","MUTHOOTFIN","PFC","PIDILITIND",
    "PNB","RECLTD","SHREECEM","SIEMENS","SOLARINDS","TATACAP","TATAPOWER","TMCV","TORNTPHARM",
    "TVSMOTOR","UNIONBANK","UNITDSPR","VBL","VEDL","ZYDUSLIFE",
}

# The CORE universe (Nifty 100): the set IndianAPI keeps fresh with daily EOD
# prices — quota-priced, so it does NOT grow when the visible tier widens.
# Intraday (90-min) and the weekly full-fundamentals refresh stay scoped to
# UNIVERSE (Nifty 50); Next-50 prices refresh daily, fundamentals ride the
# weekly full only for the core 50 (they change quarterly, so this is fine).
CORE_UNIVERSE = NIFTY_50 | NIFTY_NEXT_50 | EXTRA_TICKERS

# ── Nifty Midcap 150 / rest-of-Nifty-500 — visibility tranches 3 and 4 ──────
# OFFICIAL membership, fetched 2026-07-04 from niftyindices.com IndexConstituent
# CSVs (ind_niftymidcap150list.csv / ind_nifty500list.csv), minus names already
# in the tranches above. Indices rebalance ~half-yearly — re-pull the CSVs
# before enabling a wider tier. Drift is real: the official Nifty 500 no longer
# lists TATAMOTORS (demerged into TMPV/TMCV — both appear below).
NIFTY_MIDCAP_150 = {
    "360ONE","3MINDIA","ABBOTINDIA","ABCAPITAL","ACC","AIAENG","AIIL","AJANTPHARM","ALKEM",
    "ANTHEM","APARINDS","APLAPOLLO","APOLLOTYRE","ASHOKLEY","ASTRAL","ATGL","AUBANK",
    "AUROPHARMA","AWL","BAJAJHFL","BALKRISIND","BANKINDIA","BDL","BERGEPAINT","BHARATFORG",
    "BHARTIHEXA","BHEL","BIOCON","BLUESTARCO","BSE","COCHINSHIP","COFORGE","COLPAL","CONCOR",
    "COROMANDEL","CRISIL","DABUR","DALBHARAT","DIXON","ENDURANCE","ESCORTS","EXIDEIND",
    "FEDERALBNK","FLUOROCHEM","FORTIS","GICRE","GLAXO","GLENMARK","GMRAIRPORT","GODFRYPHLP",
    "GODREJIND","GODREJPROP","GROWW","GVT&D","HAVELLS","HDBFS","HEROMOTOCO","HEXT","HINDPETRO",
    "HONAUT","HUDCO","ICICIAMC","ICICIGI","ICICIPRULI","IDEA","IDFCFIRSTB","INDIANB",
    "INDUSINDBK","INDUSTOWER","IPCALAB","IRCTC","IREDA","ITCHOTELS","JKCEMENT","JSL",
    "JSWENERGY","JSWINFRA","JUBLFOOD","KALYANKJIL","KEI","KPITTECH","KPRMILL","LAURUSLABS",
    "LENSKART","LGEINDIA","LICHSGFIN","LICI","LINDEINDIA","LLOYDSME","LTF","LTTS","LUPIN",
    "M&MFIN","MAHABANK","MANKIND","MARICO","MCX","MEDANTA","MFSL","MOTILALOFS","MPHASIS","MRF",
    "NAM-INDIA","NATIONALUM","NAUKRI","NHPC","NIACL","NLCINDIA","NMDC","NTPCGREEN","NYKAA",
    "OBEROIRLTY","OFSS","OIL","PAGEIND","PATANJALI","PAYTM","PERSISTENT","PETRONET",
    "PHOENIXLTD","PIIND","POLICYBZR","POLYCAB","POWERINDIA","PREMIERENE","PRESTIGE","RADICO",
    "RVNL","SAIL","SBICARD","SCHAEFFLER","SJVN","SRF","SUNDARMFIN","SUPREMEIND","SUZLON",
    "SWIGGY","TATACOMM","TATAELXSI","TATAINVEST","THERMAX","TIINDIA","TORNTPOWER","UBL",
    "UNOMINDA","UPL","VMM","VOLTAS","WAAREEENER","YESBANK",
}
NIFTY_500_REST = {
    "AADHARHFC","AARTIIND","AAVAS","ABDL","ABFRL","ABLBL","ABREL","ABSLAMC","ACE","ACMESOLAR",
    "ACUTAAS","AEGISLOG","AEGISVOPAK","AFCONS","AFFLE","AMBER","ANANDRATHI","ANANTRAJ",
    "ANGELONE","ANURAS","APTUS","ARE&M","ASAHIINDIA","ASTERDM","ATHERENERG","ATUL","BALRAMCHIN",
    "BANDHANBNK","BATAINDIA","BAYERCROP","BBTC","BELRISE","BEML","BIKAJI","BLS","BLUEDART",
    "BLUEJET","BRIGADE","BSOFT","CAMS","CANFINHOME","CANHLIFE","CAPLIPOINT","CARBORUNIV",
    "CARTRADE","CASTROLIND","CCL","CDSL","CEATLTD","CEMPRO","CENTRALBK","CESC","CGCL","CHALET",
    "CHAMBLFERT","CHENNPETRO","CHOICEIN","CHOLAHLDNG","CIEINDIA","CLEAN","COHANCE","CONCORDBIO",
    "CPPLUS","CRAFTSMAN","CREDITACC","CROMPTON","CUB","CYIENT","DATAPATTNS","DCMSHRIRAM",
    "DEEPAKFERT","DEEPAKNTR","DELHIVERY","DEVYANI","DOMS","ECLERX","EIDPARRY","EIHOTEL",
    "ELECON","ELGIEQUIP","EMAMILTD","EMCURE","EMMVEE","ENGINERSIN","ERIS","FACT","FINCABLES",
    "FIRSTCRY","FIVESTAR","FORCEMOT","FSL","GABRIEL","GALLANTT","GESHIP","GILLETTE","GLAND",
    "GMDCLTD","GODIGIT","GPIL","GRANULES","GRAPHITE","GRAVITA","GRSE","HBLENGINE","HEG","HFCL",
    "HINDCOPPER","HOMEFIRST","HONASA","HSCL","IDBI","IEX","IFCI","IGIL","IGL","IIFL","IKS",
    "INDGN","INDIACEM","INDIAMART","INOXWIND","INTELLECT","IOB","IRB","IRCON","ITI","J&KBANK",
    "JAINREC","JBCHEPHARM","JBMA","JINDALSAW","JKTYRE","JMFINANCIL","JPPOWER","JSWCEMENT",
    "JSWDULUX","JUBLINGREA","JUBLPHARMA","JWL","JYOTICNC","KAJARIACER","KARURVYSYA","KAYNES",
    "KEC","KFINTECH","KIMS","KIRLOSENG","KPIL","LALPATHLAB","LATENTVIEW","LEMONTREE","LTFOODS",
    "MANAPPURAM","MAPMYINDIA","MEESHO","MGL","MINDACORP","MMTC","MRPL","MSUMI","NATCOPHARM",
    "NAVA","NAVINFLUOR","NBCC","NCC","NETWEB","NEULANDLAB","NEWGEN","NH","NIVABUPA","NSLNISP",
    "NUVAMA","NUVOCO","OLAELEC","OLECTRA","ONESOURCE","PARADEEP","PCBL","PFIZER","PGEL",
    "PINELABS","PIRAMALFIN","PNBHOUSING","POLYMED","POONAWALLA","PPLPHARMA","PTCIL","PVRINOX",
    "PWL","RAILTEL","RAINBOW","RAMCOCEM","RBLBANK","REDINGTON","RHIM","RITES","RKFORGE",
    "RPOWER","RRKABEL","SAGILITY","SAILIFE","SAMMAANCAP","SAPPHIRE","SARDAEN","SAREGAMA","SBFC",
    "SCHNEIDER","SCI","SHYAMMETL","SIGNATURE","SOBHA","SONACOMS","SONATSOFTW","SPLPETRO",
    "STARHEALTH","SUMICHEM","SUNTV","SWANCORP","SYNGENE","SYRMA","TARIL","TATACHEM","TATATECH",
    "TBOTEK","TECHNOE","TEGA","TEJASNET","TENNIND","THELEELA","TIMKEN","TITAGARH","TRAVELFOOD",
    "TRIDENT","TRITURBINE","TTML","UCOBANK","URBANCO","USHAMART","UTIAMC","VIJAYA","VTL",
    "WELCORP","WELSPUNLIV","WHIRLPOOL","WOCKPHARMA","ZEEL","ZENSARTECH","ZENTEC","ZFCVINDIA",
    "ZYDUSWELL",
}


# The VISIBLE universe: what the terminal exposes + recomputes daily, selected
# by the UNIVERSE_TIER env var (nifty100 | nifty250 | nifty500 | totalmarket | top1000). Names beyond
# CORE_UNIVERSE get their prices from the daily Dhan top-up + snapshot sync
# (100k calls/day) — NOT IndianAPI — so widening the tier costs no IndianAPI
# quota. Fundamentals for wider tiers refresh on bootstrap/weekly-full only;
# the data-quality gate keeps thin names LOW CONF rather than confidently wrong.


# OFFICIAL Nifty Microcap 250 membership (niftyindices.com
# ind_niftymicrocap250_list.csv, fetched 2026-07-13). Together with the
# Nifty 500 this completes the NIFTY TOTAL MARKET index (750 names).
# Built minus prior tiers so index migrations never duplicate a row.
_MICROCAP_250_RAW = {
    "AARTIDRUGS", "AARTIPHARM", "ACI", "ADVENZYMES", "AEQUS", "AETHER",
    "AGARWALEYE", "AGL", "AHLUCONT", "AKUMS", "ALIVUS", "ALKYLAMINE", "ALOKINDS",
    "ANUP", "APLLTD", "APOLLO", "ARVIND", "ARVINDFASN", "ASHAPURMIN", "ASHOKA",
    "ASKAUTOLTD", "ASTRAMICRO", "ATLANTAELE", "AURIONPRO", "AVALON", "AVANTIFEED",
    "AVL", "AWFIS", "AXISCADES", "AZAD", "BAJAJELEC", "BALAMINES", "BALUFORGE",
    "BANCOINDIA", "BBOX", "BECTORFOOD", "BIRLACORPN", "BLACKBUCK", "BLUESTONE",
    "BORORENEW", "CAMPUS", "CAPILLARY", "CCAVENUE", "CELLO", "CENTURYPLY", "CERA",
    "CMSINFO", "CORONA", "CRAMC", "CRIZAC", "CSBBANK", "CUPID", "DATAMATICS",
    "DBL", "DBREALTY", "DCBBANK", "DIACABS", "DYNAMATECH", "EDELWEISS", "EIEL",
    "ELECTCAST", "ELLEN", "EMBDL", "EMIL", "ENTERO", "EPL", "EQUITASBNK",
    "ETHOSLTD", "EUREKAFORB", "FEDFINA", "FIEMIND", "FINPIPE", "GAEL", "GHCL",
    "GMMPFAUDLR", "GMRP&UI", "GNFC", "GODREJAGRO", "GOKEX", "GOKULAGRO", "GPPL",
    "GREAVESCOT", "GRWRHITECH", "GSFC", "HAPPSTMNDS", "HCC", "HCG", "HEMIPROP",
    "HERITGFOOD", "HGINFRA", "ICIL", "IFBIND", "IIFLCAPS", "IMFA", "INDIAGLYCO",
    "INDIASHLTR", "INDIGOPNTS", "INOXGREEN", "INOXINDIA", "IONEXCHANG", "IXIGO",
    "JAIBALAJI", "JAMNAAUTO", "JAYNECOIND", "JKLAKSHMI", "JKPAPER", "JLHL", "JSFB",
    "JSLL", "JUSTDIAL", "JYOTHYLAB", "KANSAINER", "KIRLOSBROS", "KIRLPNU", "KITEX",
    "KNRCON", "KPIGREEN", "KRBL", "KRN", "KSB", "KSCL", "KTKBANK", "LLOYDSENGG",
    "LLOYDSENT", "LOTUSDEV", "LUMAXTECH", "LXCHEM", "MAHSCOOTER", "MAHSEAMLES",
    "MANORAMA", "MANYAVAR", "MARKSANS", "MASTEK", "MEDPLUS", "METROPOLIS",
    "MIDHANI", "MOIL", "MSTCLTD", "MTARTECH", "NAZARA", "NEOGEN", "NESCO",
    "NETWORK18", "NFL", "OPTIEMUS", "ORIENTCEM", "ORKLAINDIA", "OSWALPUMPS",
    "PARAS", "PARKHOSPS", "PCJEWELLER", "PFOCUS", "PGIL", "PICCADIL", "PNCINFRA",
    "PNGJL", "POWERMECH", "PRAJIND", "PRICOLLTD", "PRIVISCL", "PRSMJOHNSN",
    "PRUDENT", "PTC", "PURVA", "QPOWER", "QUESS", "RAIN", "RALLIS", "RATEGAIN",
    "RATNAMANI", "RAYMONDLSL", "RBA", "RCF", "REDTAPE", "REFEX", "RELAXO",
    "RELIGARE", "RENUKA", "ROUTE", "RTNINDIA", "RTNPOWER", "RUBICON", "SAATVIKGL",
    "SAFARI", "SAMHI", "SANDUMA", "SANOFICONR", "SANSERA", "SENCO", "SFL",
    "SHAILY", "SHAKTIPUMP", "SHARDACROP", "SHAREINDIA", "SHILPAMED", "SHRIPISTON",
    "SKFINDIA", "SKFINDUS", "SKIPPER", "SKYGOLD", "SMARTWORKS", "SMLMAH",
    "SOUTHBANK", "SPARC", "STAR", "STARCEMENT", "STLTECH", "STYL", "STYRENIX",
    "SUBROS", "SUDARSCHEM", "SUDEEPPHRM", "SUNTECK", "SUPRIYA", "SURYAROSNI",
    "SWSOLAR", "TANLA", "TARC", "TDPOWERSYS", "TEXRAIL", "THANGAMAYL",
    "THOMASCOOK", "THYROCARE", "TI", "TIMETECHNO", "TIPSMUSIC", "TMB",
    "TRANSRAILL", "TRIVENI", "TSFINV", "TVSSCS", "UJJIVANSFB", "UTLSOLAR",
    "V2RETAIL", "VAIBHAVGBL", "VARROC", "VGUARD", "VIKRAMSOLR", "VIPIND", "VIYASH",
    "VMART", "VOLTAMP", "WAAREERTL", "WABAG", "WAKEFIT", "WEBELSOLAR", "WELENT",
    "WESTLIFE", "WEWORK", "YATHARTH", "ZAGGLE"
}
NIFTY_MICROCAP_250 = (_MICROCAP_250_RAW - CORE_UNIVERSE - NIFTY_MIDCAP_150
                      - NIFTY_500_REST)



# Ranks beyond the Nifty Total Market, by the OFFICIAL AMFI half-yearly
# average-market-cap ranking (AverageMarketCapitalization30Jun2026.xlsx,
# portal.amfiindia.com, fetched 2026-07-13): the next 250 NSE-listed names
# not already covered — includes real large businesses the NSE indices
# skip on free-float/liquidity rules (e.g. P&G Hygiene). Floor ≈ ₹2,631cr.
AMFI_NEXT_250 = {
    "63MOONS", "AEROFLEX", "AGI", "AGIIL", "AJAXENGG", "AJMERA", "AMAGI", "ARSSBL",
    "ARTEMISMED", "ASHIANA", "ASTRAZEN", "AUTOAXLES", "AVANTEL", "AYE", "BAGMANE",
    "BAJAJCON", "BAJAJHIND", "BALMLAWRIE", "BANARISUG", "BANSALWIRE", "BASF",
    "BBL", "BHAGCHEM", "BHARATCOAL", "BIRET", "BLISSGVS", "BOROLTD",
    "CARERATING", "CARRARO", "CARYSIL", "CEIGALL", "CENTUM", "CHEMPLASTS",
    "CLEANMAX", "CMPDI", "CMRGREEN", "CYIENTDLM", "DALMIASUG", "DBCORP", "DCAL",
    "DDEVPLSTIK", "DEEPINDS", "DHANUKA", "DODLA", "DREDGECORP", "E2E",
    "EASEMYTRIP", "EBGNG", "EFCIL", "EMBASSY", "EMUDHRA", "EPIGRAL", "ESABINDIA",
    "FCL", "FDC", "FINEORG", "FLAIR", "FOSECOIND", "FRACTAL", "FUSION",
    "GALAXYSURF", "GANESHHOU", "GARFIBRES", "GATEWAY", "GENUSPOWER", "GLOBUSSPR",
    "GOLDIAM", "GOODLUCK", "GOPAL", "GREENLAM", "GREENPLY", "GRINDWELL", "GRINFRA",
    "GRMOVER", "GUFICBIO", "GUJALKALI", "GUJENERGY", "GUJTHEM", "GULFOILLUB",
    "GVPIL", "HAPPYFORGE", "HARSHA", "HATSUN", "HEIDELBERG", "HIRECT", "HNDFDS",
    "HUBTOWN", "IBULLSLTD", "ICRA", "INDIQUBE", "INDOSTAR", "INDOTHAI",
    "INDRAMEDCO", "INGERRAND", "INNOVACAP", "INTERARCH", "IOLCP", "ISGEC", "ITDC",
    "JINDALPOLY", "JKIL", "JSWHL", "JTEKTINDIA", "JUBLCPL", "JUNIPER", "KALPATARU",
    "KDDL", "KINGFA", "KIOCL", "KIRLOSIND", "KISSHT", "KKCL", "KMEW", "KOLTEPATIL",
    "KRISHANA", "KRT", "KSHINTL", "KSL", "KWIL", "LGBBROSLTD", "LMW", "LUMAXIND",
    "LUXIND", "MAHLIFE", "MAHLOG", "MAITHANALL", "MANINDS", "MANINFRA", "MARATHON",
    "MASFIN", "MAXESTATES", "MBAPL", "MEDIASSIST", "METROBRAND", "MHRIL",
    "MIDWESTLTD", "MINDSPACE", "MOSCHIP", "MPSLTD", "MUTHOOTMF", "NACLIND",
    "NAVNETEDUL", "NEPHROPLUS", "NIITMTS", "NORTHARC", "NRBBEARING", "NSIL",
    "NXST", "OMNI", "ORCHPHARMA", "ORIENTELEC", "PACEDIGITK", "PAISALO",
    "PARAGMILK", "PATELENG", "PDSL", "PGHH", "PGHL", "PILANIINVS", "PITTIENG",
    "POCL", "POKARNA", "POLYPLEX", "POWERICA", "PRECWIRE", "PREMEXPLN",
    "PRINCEPIPE", "PSB", "PSPPROJECT", "RAJESHEXPO", "RAMKY", "RAMRAT", "RAYMOND",
    "RAYMONDREL", "RELINFRA", "RESPONIND", "ROLEXRINGS", "ROSSARI", "ROSSTECH",
    "RPEL", "RPGLIFE", "RPSGVENT", "RPTECH", "RSYSTEMS", "RUSTOMJEE", "SAMBHV",
    "SANATHAN", "SANDHAR", "SANGHVIMOV", "SANOFI", "SBCL", "SEAMECLTD", "SEDEMAC",
    "SENORES", "SETL", "SGFIN", "SGMART", "SHADOWFAX", "SHANTIGEAR", "SHARDAMOTR",
    "SHILCTECH", "SHOPERSTOP", "SHREEJISPG", "SIGMAADV", "SINDHUTRAD", "SIS",
    "SJS", "SMSPHARMA", "SOTL", "SSWL", "STYLAMIND", "SUNCLAY", "SUNDRMFAST",
    "SUNFLAG", "SUPRAJIT", "SUVEN", "SWANDEF", "SWARAJENG", "SYMPHONY", "TATVA",
    "TCI", "TFCILTD", "TIIL", "TRUALT", "TTKPRESTIG", "TURTLEMINT", "TVSHLTD",
    "TVSSRICHAK", "UEL", "UFLEX", "UNIMECH", "UNIVCABLES", "VADILALIND",
    "VEDPOWER", "VENTIVE", "VESUVIUS", "VINATIORGA", "VISHNU", "VOGL",
    # VAML / VISL removed (DATA-01): not separately-listed NSE tickers — the
    # vendor name-search resolved both to one micro Vedanta-segment entity and
    # stored them as byte-identical clones. Dropped from the universe so the boot
    # auto-create never re-seeds them; existing rows deleted as a one-off DB step.
    # Batch 2 (clone audit): BOSCH-HCIL (=Bosch/BOSCHLTD), MARINE (=KMEW),
    # WHEELS (=SSWL) removed for the same reason.
    "VRLLOG", "VSTIND", "VSTTILLERS", "WONDERLA", "WSTCSTPAPR", "ZOTA"
}

_TIERS = {
    "nifty100": CORE_UNIVERSE,
    "nifty250": CORE_UNIVERSE | NIFTY_MIDCAP_150,
    "nifty500": CORE_UNIVERSE | NIFTY_MIDCAP_150 | NIFTY_500_REST,
    # Nifty Total Market = Nifty 500 + Microcap 250 (official membership only)
    "totalmarket": CORE_UNIVERSE | NIFTY_MIDCAP_150 | NIFTY_500_REST | NIFTY_MICROCAP_250,
    # ~1000 names: Total Market + the next 250 by official AMFI mcap ranking
    "top1000": (CORE_UNIVERSE | NIFTY_MIDCAP_150 | NIFTY_500_REST
                | NIFTY_MICROCAP_250 | AMFI_NEXT_250),
}
UNIVERSE_TIER = (os.getenv("UNIVERSE_TIER", "nifty100") or "").strip().lower()
if UNIVERSE_TIER not in _TIERS:
    UNIVERSE_TIER = "nifty100"
VISIBLE_UNIVERSE = _TIERS[UNIVERSE_TIER] - EXCLUDED_TICKERS


# ── Intraday spot-price refresh (yfinance — all 50 in ONE batched call) ──────
# The batched-Yahoo live-price path (_yf_live_prices) lived here and was removed
# as dead code: it had zero call sites, because run_intraday below already
# documents why it stopped being usable — Yahoo blocks datacenter IPs, so it
# could not work from the EC2 box regardless. Prices come from IndianAPI /stock.
#
# NOTE: this does NOT free the yfinance pin in requirements.txt. One other file
# still imports it — app/ingest/fix_roe_sweep.py, a one-off from the 2026-06
# migration that re-derived ROE assumptions stuck at 0.155. Its work is long
# done and it is referenced only by audit documents, but deleting a script is a
# judgement call rather than a cleanup, so the dependency stays until that call
# is made.

def run_intraday(debug=False):
    """Refresh Nifty 50 spot prices via IndianAPI (one /stock call per name).

    NOTE: this used to use a single batched Yahoo/yfinance call, but Yahoo blocks
    datacenter IPs — from Railway every request comes back empty
    (JSONDecodeError on all 50), so the batch path is dead in production. IndianAPI
    is the only source that works server-side (it's what the daily EOD refresh
    already uses successfully). The trade-off: ~50 IndianAPI calls per run instead
    of one free Yahoo call, so the scheduler runs this every 90 min during market
    hours (not every 15 min) to stay inside the monthly quota.

    Pure price update — no statements/insights. Returns the number of prices
    updated. refresh_price() commits and handles its own rollback per name."""
    s = SessionLocal()
    try:
        companies = [c for c in s.query(models.Company).all()
                     if (c.ticker or "").upper() in UNIVERSE]
        updated = 0
        for co in companies:
            try:
                if refresh_price(s, co):
                    updated += 1
            except Exception as e:
                try: s.rollback()
                except Exception: pass
                if debug:
                    print(f"  [intraday] {co.ticker}: {type(e).__name__}: {e}")
            time.sleep(RATE_SLEEP)
        if debug:
            print(f"  [intraday] IndianAPI updated {updated}/{len(companies)} prices")
        return updated
    finally:
        s.close()


def run(limit=None, ticker=None, price_only=False, nifty50=False, insights=True, visible=False,
        tickers=None):
    from app import api_budget as B
    s = SessionLocal()
    q = s.query(models.Company)
    if ticker:
        q = q.filter(models.Company.ticker == ticker.upper())
    companies = q.all()
    # `tickers` (explicit set) wins — the rolling-cohort refresh uses it. Then
    # `visible` scopes to the CORE Nifty 100 (IndianAPI daily EOD — deliberately
    # NOT the tier-widened VISIBLE_UNIVERSE, whose extra names are priced by the
    # Dhan top-up instead); `nifty50` to the tight Nifty 50 (intraday + weekly
    # full). `visible` takes precedence over `nifty50` when both set.
    if tickers is not None:
        scope = {(t or "").upper() for t in tickers}
    else:
        scope = CORE_UNIVERSE if visible else (UNIVERSE if nifty50 else None)
    if scope is not None:
        companies = [c for c in companies if (c.ticker or "").upper() in scope]
    if limit:
        companies = companies[:limit]
    mode = ("prices only" if price_only
            else "statements + facts + price + insights" if insights
            else "statements + facts + price")
    print(f"IndianAPI ingest — {len(companies)} companies ({mode})")

    # Budget pre-flight: project this batch's cost and refuse to start a run that
    # would breach INDIANAPI_MONTHLY_BUDGET (override with INDIANAPI_ALLOW_OVER=1).
    projected = len(companies) * (1 if price_only else B.CALLS_PER_FULL_INGEST)
    print(f"  budget: ~{projected} calls projected · {B.month_usage(s)}/{B.budget()} used this month "
          f"· {B.remaining(s)} remaining")
    if B.would_exceed(s, projected) and os.getenv("INDIANAPI_ALLOW_OVER", "").strip().lower() not in ("1", "true", "yes"):
        print(f"  ⛔ projected spend would exceed the monthly budget ({B.budget()}). "
              f"Set INDIANAPI_ALLOW_OVER=1 to override. Aborting run.")
        s.close()
        return

    start_calls = get_call_count()
    ok = 0
    for co in companies:
        try:
            if price_only:
                refresh_price(s, co)
            else:
                ingest_company(s, co, dump=(ticker is not None), insights=insights)
            ok += 1
        except Exception as e:
            s.rollback()
            print(f"  {co.ticker}: FAILED ({type(e).__name__}: {e})")
        time.sleep(RATE_SLEEP)

    spent = get_call_count() - start_calls
    try:
        from app import vendor_meter
        vendor_meter.flush(s)                   # FIX-07: durable, drain-based (no double-count)
        total = B.month_usage(s)
    except Exception:
        s.rollback(); total = None
    s.close()
    print(f"Done. {ok}/{len(companies)} processed. ({spent} API calls"
          + (f"; {total}/{B.budget()} this month)" if total is not None else ")"))


if __name__ == "__main__":
    args = sys.argv[1:]
    run(
        limit=int(args[args.index("--limit") + 1]) if "--limit" in args else None,
        ticker=args[args.index("--ticker") + 1] if "--ticker" in args else None,
        price_only="--price-only" in args,
        nifty50="--nifty50" in args,
        insights="--no-insights" not in args,
    )
