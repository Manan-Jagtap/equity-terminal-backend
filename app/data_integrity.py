"""
app/data_integrity.py — the continuous data-integrity sweep.

The one-off enterprise audit (17 Jul 2026) proved these checks find real issues;
this module makes them a STANDING control instead of an event. It validates the
stored universe directly against the DB (no HTTP, no rate limits):

  · duplicate display names (post-demerger collisions)
  · price / shares / P-E / P-B / ROE / MoS sanity bands
  · verdict ↔ MoS coherence (a BUY must not carry negative MoS, etc.)
  · BUY/ACCUMULATE on LOW confidence (the gates must have caught these)
  · stale market snapshots (price older than STALE_PRICE_DAYS)
  · statement identities on a bounded sample (net worth ≤ total assets, …)

Runs weekly from the scheduler (Sun 03:00 UTC, after the full refresh) and on
demand via the admin endpoint. Results land in KVStore `data_integrity_v1` so
the admin surface can show a red/green strip; findings are capped, never raise.
"""
from __future__ import annotations
import datetime as _dt

KEY = "data_integrity_v1"
STALE_PRICE_DAYS = 5
MAX_FINDINGS = 300
STATEMENT_SAMPLE = 40


def _flag(findings, ticker, check, severity, detail):
    if len(findings) < MAX_FINDINGS:
        findings.append({"ticker": ticker, "check": check,
                         "severity": severity, "detail": str(detail)[:200]})


def run_integrity_sweep(db) -> dict:
    """Pure-read sweep over companies + valuations + snapshots (+ a statements
    sample). Returns the summary dict; never raises past its own guards."""
    from app import models
    findings: list[dict] = []
    today = _dt.date.today()

    companies = db.query(models.Company).all()
    val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    snap_by = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}

    seen_names: dict[str, str] = {}
    verdicts: dict[str, int] = {}
    n_no_shares = 0
    for co in companies:
        tk = (co.ticker or "").upper()
        nm = (co.name or "").strip().lower()
        if nm:
            if nm in seen_names and seen_names[nm] != tk:
                _flag(findings, tk, "duplicate_name", "P2", f"same name as {seen_names[nm]}")
            seen_names[nm] = tk
        if not (co.shares_outstanding and co.shares_outstanding > 0):
            n_no_shares += 1

        m = snap_by.get(co.id)
        if m is not None:
            if not (m.price and 0 < m.price <= 200_000):
                # Severity must track USER-VISIBLE harm. A never-onboarded stub
                # (no shares, verdict NO DATA) shows the user nothing, so a 0.0
                # price there is a COVERAGE gap, not data corruption — CCAVENUE
                # sat red as a P1 on exactly this. A name that HAS data but a
                # broken price is the real P1: it can feed a wrong MoS.
                _onboarded = bool(co.shares_outstanding and co.shares_outstanding > 0)
                if _onboarded:
                    _flag(findings, tk, "price_absurd", "P1", m.price)
                else:
                    _flag(findings, tk, "price_absent_not_onboarded", "P2", m.price)
            elif m.as_of is not None:
                age = (today - m.as_of.date()).days
                if age > STALE_PRICE_DAYS:
                    _flag(findings, tk, "price_stale", "P2", f"{age}d old")

        v = val_by.get(co.id)
        if v is None:
            continue
        verdict = v.verdict or "NONE"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        conf = (v.confidence or "").lower()
        for k, val, lo, hi in (("pe", v.pe, 0, 2000), ("pb", v.pb, 0, 500),
                               ("roe", v.roe, -5, 3)):
            if val is not None and not (lo <= val <= hi):
                _flag(findings, tk, f"{k}_out_of_band", "P2", val)
        # An absurd MoS is tolerable ONLY when the name is GATED — and the gate
        # the user actually sees is the VERDICT, not the confidence field. LICI
        # (verdict LOW CONF, confidence "medium") was reported as ungated while
        # the product correctly showed a no-call: a false P1 that turned the
        # whole sweep red. Gate = low confidence OR a non-committal verdict.
        _gated = conf in ("low",) or verdict in ("LOW CONF", "NO CALL", "NO DATA")
        if v.mos is not None and abs(v.mos) > 5 and not _gated:
            _flag(findings, tk, "mos_absurd_ungated", "P1", v.mos)
        # The check above is STRUCTURALLY ONE-SIDED and cannot be fixed by moving
        # its threshold. mos = intrinsic/price - 1 is unbounded above but floored
        # at -1, so `abs(mos) > 5` can only ever fire on an INFLATED fair value.
        # A fair value that COLLAPSES toward zero is just as absurd and was
        # invisible: found live on 2026-07-27, ADANIGREEN published an ungated
        # AVOID with intrinsic 3.1 against a price of 1,380 (mos -0.998) — the
        # model asserting the equity is worth 0.2% of its market price, which is
        # not a bearish call but a broken number. This direction matters more
        # since CORR-1, which moves every unearned-growth name DOWNWARD.
        #
        # Threshold: intrinsic below 10% of price. A genuine "equity is nearly
        # worthless" call is defensible, but it must be GATED (LOW CONF / NO
        # CALL), never published as a bare fair value.
        if v.mos is not None and v.mos <= -0.90 and not _gated:
            _flag(findings, tk, "mos_collapsed_ungated", "P1", v.mos)
        if verdict in ("BUY", "ACCUMULATE"):
            if v.mos is not None and v.mos < 0:
                _flag(findings, tk, "verdict_mos_incoherent", "P1", f"{verdict} @ mos {v.mos:.2f}")
            if conf == "low":
                _flag(findings, tk, "buy_on_low_conf", "P1", f"{verdict}/{v.confidence}")
        if verdict == "AVOID" and v.mos is not None and v.mos > 0.05:
            _flag(findings, tk, "verdict_mos_incoherent", "P2", f"AVOID @ mos {v.mos:+.2f}")

    # Statement identities on a deterministic sample (first N with history).
    sampled = 0
    for co in companies:
        if sampled >= STATEMENT_SAMPLE:
            break
        rows = (db.query(models.HistoricalFinancial)
                  .filter_by(company_id=co.id).all())
        if not rows:
            continue
        sampled += 1
        nested: dict = {}
        for r in rows:
            if r.value is None:
                continue
            nested.setdefault(int(r.fiscal_year), {}).setdefault(r.statement_type, {})[r.line_item] = r.value
        for yr, blocks in nested.items():
            pl = blocks.get("PL") or {}
            rev, ebit = pl.get("revenue"), pl.get("ebit")
            if rev is not None and rev < 0:
                _flag(findings, co.ticker, "revenue_negative", "P1", f"{yr}: {rev}")
            # Misfiled-column detector: POSITIVE EBIT above revenue is impossible
            # bookkeeping (LICHSGFIN-style shifted years). The old abs() variant
            # also condemned legitimate R&D loss-makers whose burn exceeds their
            # small revenue (SPARC: rev ₹72cr, EBIT −₹334cr) — a real business
            # state, not a data defect — and held integrity red on it.
            if rev and ebit is not None and ebit > abs(rev) * 1.5:
                _flag(findings, co.ticker, "ebit_gt_revenue", "P1", f"{yr}: {ebit} vs {rev}")
            bs = blocks.get("BS") or {}
            ta, nw = bs.get("total_assets"), bs.get("net_worth")
            if ta and nw is not None and nw > ta * 1.02:
                _flag(findings, co.ticker, "networth_gt_assets", "P1", f"{yr}: nw {nw} ta {ta}")

    by_check: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for f in findings:
        by_check[f["check"]] = by_check.get(f["check"], 0) + 1
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    return {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "n_companies": len(companies),
        "n_valuations": len(val_by),
        "n_without_shares": n_no_shares,
        "statement_sample": sampled,
        "verdicts": verdicts,
        "n_findings": len(findings),
        "by_severity": by_sev,
        "by_check": by_check,
        "status": "red" if by_sev.get("P1") else ("amber" if findings else "green"),
        "findings": findings,
    }


def store_sweep(db) -> dict:
    """Run the sweep and persist it to KV for the admin surface."""
    from app.manager_engine import _kv_put
    out = run_integrity_sweep(db)
    _kv_put(db, KEY, out)
    return out


def load_sweep(db) -> dict | None:
    from app.manager_engine import _kv_get
    return _kv_get(db, KEY)
