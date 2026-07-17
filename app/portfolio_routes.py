"""
app/portfolio_routes.py — simple holdings portfolio with live P&L.

Mirrors the watchlist's `user_key` scoping: every endpoint REQUIRES
authentication (Authorization: Bearer <token>) and is scoped by
user_key = f"u{user.id}" — unauthenticated requests get a 401:

  GET    /api/portfolio                → holdings enriched with price / value /
                                         P&L / weight / MoS / verdict + totals.
  POST   /api/portfolio                → upsert a holding by ticker (qty, avg_cost).
  DELETE /api/portfolio/{holding_id}   → remove a holding.

The totals math (value, cost, P&L, weights, value-weighted MoS) lives in
`compute_totals` — a pure function so it's unit-testable without a DB.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.corporate_actions import price_factor

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/engine-status")
def engine_status(db: Session = Depends(get_db)):
    """PUBLIC, read-only: Fund Manager v4 engine heartbeat — when evidence was
    last built, how many names it covers, the macro regime, and the calibration
    vintage. No user data; safe to expose (and it lets the frontend show the
    macro strip before any portfolio loads)."""
    try:
        from app.manager_engine import load_evidence, load_macro
        ev, mac = load_evidence(db) or {}, load_macro(db) or {}
        return {"engine": "v4-triangulated",
                "evidence_as_of": ev.get("as_of"),
                "names": len(ev.get("names") or {}),
                "calibration_as_of": ev.get("calibration_as_of"),
                "weights": ev.get("weights"),
                "model_trust_sectors": len(ev.get("model_trust") or {}),
                "macro": {k: mac.get(k) for k in
                          ("regime", "breadth_200dma", "breadth_50dma", "breadth_trend",
                           "vix", "rates", "rs_leaders", "rs_laggards", "as_of")} if mac else None}
    except Exception:
        db.rollback()
        return {"engine": "v4-triangulated", "evidence_as_of": None, "names": 0}


@router.get("/engine-ledger")
def engine_ledger(days: int = 90, db: Session = Depends(get_db)):
    """PUBLIC: the engine's own gradeable track record — every nightly
    top-idea snapshot with its entry price and the realized return to today.
    Same doctrine as the Track Record page: recorded daily, never backfilled,
    graded in the open. Educational, not advice."""
    import datetime as _dt2
    cutoff = (_dt2.date.today() - _dt2.timedelta(days=max(7, min(days, 365)))).isoformat()
    try:
        rows = (db.query(models.EngineCall)
                  .filter(models.EngineCall.date >= cutoff)
                  .order_by(models.EngineCall.date.desc(),
                            models.EngineCall.conviction.desc()).all())
    except Exception:
        db.rollback()
        return {"calls": [], "summary": None}
    cid_by = {c.ticker: c.id for c in db.query(models.Company).all()}
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    calls, rets = [], []
    today = _dt2.date.today()
    for r in rows:
        p_now = price_by.get(cid_by.get(r.ticker))
        ret = (p_now / r.price - 1.0) if (p_now and r.price) else None
        age = (today - _dt2.date.fromisoformat(r.date)).days
        if ret is not None and age >= 7:
            rets.append(ret)
        calls.append({"date": r.date, "ticker": r.ticker, "action": r.action,
                      "conviction": r.conviction, "entry_price": r.price,
                      "price_now": p_now, "return_pct": ret, "age_days": age,
                      "val_blend": r.val_blend, "quality": r.quality})
    summary = None
    if rets:
        summary = {"n_graded": len(rets),
                   "avg_return": round(sum(rets) / len(rets), 4),
                   "hit_rate": round(sum(1 for x in rets if x > 0) / len(rets), 3)}
    return {"calls": calls[:200], "summary": summary,
            "note": ("Calls at least 7 days old are graded against the current "
                     "price. Recorded nightly, never backfilled.")}


class HoldingUpsert(BaseModel):
    ticker: str
    qty: float
    avg_cost: float
    buy_date: str | None = None    # ISO YYYY-MM-DD (optional)


class CashSet(BaseModel):
    amount: float                  # investable cash / dry powder (₹)


@router.get("/cash")
def get_cash(user: models.User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """The investable cash (dry powder) the owner has flagged — the Fund
    Manager sizes and gates its ADD ideas against this."""
    return {"amount": _get_cash(db, f"u{user.id}")}


@router.post("/cash")
def set_cash(body: CashSet, user: models.User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Set the investable cash. Persisted per user so the manager remembers it."""
    key = _CASH_KEY + f"u{user.id}"
    amt = max(0.0, float(body.amount))
    row = db.query(models.KVStore).filter_by(key=key).first()
    if row:
        row.value = {"amount": amt}
    else:
        db.add(models.KVStore(key=key, value={"amount": amt}))
    db.commit()
    return {"amount": amt}


@router.delete("/cash")
def clear_cash(user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Remove the investable-cash setting — the manager stops gating on cash and
    reverts to plain % -of-book tranche sizing."""
    row = db.query(models.KVStore).filter_by(key=_CASH_KEY + f"u{user.id}").first()
    if row:
        db.delete(row)
        db.commit()
    return {"amount": None}


def _parse_date(v):
    import datetime as _dt
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


# India: listed equity turns LONG-TERM at 12 months (LTCG 12.5% vs STCG 20%).
_LT_DAYS = 365


def _term_fields(holding) -> dict:
    """holding_days / term / days_to_lt from the user-supplied buy_date, or
    from added_at as an honest proxy (flagged via date_source)."""
    import datetime as _dt
    today = _dt.date.today()
    bd = holding.buy_date
    src = "buy_date"
    if bd is None:
        src = "added"
        bd = holding.added_at.date() if isinstance(holding.added_at, _dt.datetime) else None
    if bd is None:
        return {"buy_date": None, "date_source": None, "holding_days": None,
                "term": None, "days_to_lt": None}
    days = (today - bd).days
    return {"buy_date": bd.isoformat(), "date_source": src, "holding_days": days,
            "term": "long" if days >= _LT_DAYS else "short",
            "days_to_lt": max(0, _LT_DAYS - days) if days < _LT_DAYS else 0}


def compute_totals(items: list[dict]) -> dict:
    """Pure totals math over already-built item rows.

    Each item carries `value` (qty × price, None when no price), `cost`
    (qty × avg_cost), `mos` (nullable) and optionally `div_income` (dividends
    received since the position was opened). MUTATES each item to set `weight`,
    `pnl`/`pnl_pct` (capital only) and `total_pnl`/`total_pnl_pct` (capital +
    dividends). weighted_mos is the value-weighted average over items with a
    non-null MoS.
    """
    total_value = sum(i["value"] for i in items if i.get("value") is not None)
    total_cost = sum(i["cost"] for i in items if i.get("cost") is not None)
    total_div = sum((i.get("div_income") or 0.0) for i in items)
    pnl = (total_value - total_cost) if items else 0.0
    pnl_pct = (pnl / total_cost) if total_cost else None
    total_pnl = pnl + total_div
    total_pnl_pct = (total_pnl / total_cost) if total_cost else None

    for i in items:
        i["weight"] = (i["value"] / total_value) if (total_value and i.get("value") is not None) else None
        v, c, d = i.get("value"), i.get("cost"), (i.get("div_income") or 0.0)
        i["pnl"] = (v - c) if (v is not None and c is not None) else None
        i["pnl_pct"] = (i["pnl"] / c) if (i["pnl"] is not None and c) else None
        i["total_pnl"] = (i["pnl"] + d) if i["pnl"] is not None else None
        i["total_pnl_pct"] = (i["total_pnl"] / c) if (i["total_pnl"] is not None and c) else None

    mos_pairs = [(i["value"], i["mos"]) for i in items
                 if i.get("mos") is not None and i.get("value")]
    wsum = sum(v for v, _ in mos_pairs)
    weighted_mos = (sum(v * m for v, m in mos_pairs) / wsum) if wsum else None

    return {"value": total_value, "cost": total_cost, "pnl": pnl,
            "pnl_pct": pnl_pct, "div_income": total_div,
            "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
            "weighted_mos": weighted_mos}


_NIFTY_CACHE = {"ts": 0.0, "data": None}


def _nifty_series():
    """(dates, closes) for the NIFTY 50 from Dhan, cached 1h. None if the feed
    is unavailable — the benchmark then just doesn't show."""
    import time as _t
    if _NIFTY_CACHE["data"] is not None and _t.time() - _NIFTY_CACHE["ts"] < 3600:
        return _NIFTY_CACHE["data"]
    out = None
    try:
        import datetime as _dt
        from app.dhan import client, instruments
        if client.configured():
            sid = instruments.index_security_id("NIFTY 50")
            if sid:
                frm = (_dt.date.today() - _dt.timedelta(days=365 * 6)).isoformat()
                rows = client.historical_daily(sid, frm, _dt.date.today().isoformat(),
                                               exchange_segment="IDX_I", instrument="INDEX")
                pts = sorted((r["date"], r["close"]) for r in (rows or [])
                             if r.get("date") and r.get("close"))
                if len(pts) >= 30:
                    out = ([d for d, _ in pts], [c for _, c in pts])
    except Exception:
        out = None
    _NIFTY_CACHE["data"], _NIFTY_CACHE["ts"] = out, _t.time()
    return out


def benchmark_block(items: list[dict]) -> dict | None:
    """Capital-matched NIFTY 50 benchmark: what the SAME rupees invested on the
    SAME buy dates would be worth in the index today, vs the actual book. Only
    covers positions that carry a buy date + cost; alpha is the honest apples-to-
    apples gap. None when the Dhan index feed is unavailable."""
    import bisect
    series = _nifty_series()
    if not series:
        return None
    dates, closes = series
    now = closes[-1]

    def asof(d_iso):
        i = bisect.bisect_right(dates, d_iso) - 1
        return closes[i] if i >= 0 else None

    invested = bench_now = port_now = 0.0
    for i in items:
        c, bd, v = i.get("cost"), i.get("buy_date"), i.get("value")
        if not c or not bd or v is None:
            continue
        lvl = asof(bd)
        if not lvl or lvl <= 0:
            continue
        invested += c
        bench_now += c * (now / lvl)
        port_now += v + (i.get("div_income") or 0.0)
    if invested <= 0:
        return None
    bench_ret = bench_now / invested - 1
    port_ret = port_now / invested - 1
    return {"benchmark": "NIFTY 50", "benchmark_return": bench_ret,
            "portfolio_return": port_ret, "alpha": port_ret - bench_ret,
            "matched_cost": invested, "as_of": dates[-1]}


def _dividend_income(qty: float, added_at, actions: list[dict] | None) -> float:
    """Cash dividends received on this position: qty × Σ per-share dividends with
    ex-date on/after the position was opened, each scaled to the CURRENT per-share
    basis so it lines up with today's qty across any intervening split/bonus.
    `added_at` is the only entry-date proxy the model stores (a v1 approximation:
    a mid-window top-up is treated as held from the original add date)."""
    if not qty or not actions:
        return 0.0
    since = added_at.date().isoformat() if added_at else ""
    per_share = 0.0
    for a in actions:
        if a.get("action_type") != "dividend":
            continue
        ex, v = a.get("ex_date"), a.get("value")
        if v and ex and (not since or ex >= since):
            per_share += v * price_factor(ex, actions)
    return qty * per_share


def _item(holding: models.PortfolioHolding, price, val: models.Valuation | None,
          actions: list[dict] | None = None) -> dict:
    co = holding.company
    qty, avg_cost = holding.qty or 0.0, holding.avg_cost or 0.0
    value = (qty * price) if price is not None else None
    cost = qty * avg_cost
    out = {
        "id": holding.id,
        "ticker": co.ticker, "name": co.name, "sector": co.sector,
        "qty": qty, "avg_cost": avg_cost,
        "price": price, "value": value, "cost": cost,
        "div_income": _dividend_income(qty, holding.added_at, actions),
        "pnl": None, "pnl_pct": None, "weight": None,   # filled by compute_totals
        "total_pnl": None, "total_pnl_pct": None,       # filled by compute_totals
        "mos": (val.mos if val else None),
        "verdict": (val.verdict if val else None),
        "confidence": (val.confidence if val else None),
        "intrinsic": (val.intrinsic if val else None),
        **_term_fields(holding),
    }
    # Per-position XIRR: annualised total return (price + dividends) over the
    # actual holding period. Under 7 days the annualisation is noise → None.
    days, v, c = out.get("holding_days"), out.get("value"), out.get("cost")
    if days and days >= 7 and v and c and c > 0:
        try:
            out["xirr"] = ((v + (out.get("div_income") or 0.0)) / c) ** (365.0 / days) - 1
        except (OverflowError, ZeroDivisionError):
            out["xirr"] = None
    else:
        out["xirr"] = None
    return out


def _build_items(db: Session, uk: str) -> list[dict]:
    holdings = (db.query(models.PortfolioHolding)
                  .filter_by(user_key=uk)
                  .join(models.Company).order_by(models.Company.ticker).all())
    # Scope every lookup to THIS user's holdings — a 3-name book must not load
    # the entire MarketSnapshot / Valuation / CorporateAction tables (audit D8:
    # latency & memory scaled with the universe, not the portfolio).
    cids = [h.company_id for h in holdings]
    if not cids:
        return []
    price_by = {m.company_id: m.price for m in
                db.query(models.MarketSnapshot)
                  .filter(models.MarketSnapshot.company_id.in_(cids)).all()}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in
                  db.query(models.Valuation)
                    .filter(models.Valuation.company_id.in_(cids)).all()}
    except Exception:
        db.rollback()
    actions_by: dict[int, list[dict]] = {}
    for a in (db.query(models.CorporateAction)
                .filter(models.CorporateAction.company_id.in_(cids)).all()):
        actions_by.setdefault(a.company_id, []).append(
            {"action_type": a.action_type, "ex_date": a.ex_date,
             "value": a.value, "ratio": a.ratio})
    return [_item(h, price_by.get(h.company_id), val_by.get(h.company_id),
                  actions_by.get(h.company_id)) for h in holdings]


@router.get("")
def list_portfolio(user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    items = _build_items(db, uk)
    totals = compute_totals(items)
    # Book XIRR from the real purchase dates: one outflow per position at its
    # buy date, one terminal inflow at today's value + dividends received.
    try:
        import datetime as _dt
        from app.portfolio_risk import xirr as _xirr
        flows, terminal = [], 0.0
        for i in items:
            if not (i.get("cost") and i.get("value")):
                continue
            bd = i.get("buy_date")
            d = _dt.date.fromisoformat(bd) if bd else _dt.date.today()
            flows.append((d, -i["cost"]))
            terminal += i["value"] + (i.get("div_income") or 0.0)
        if flows and terminal:
            flows.append((_dt.date.today(), terminal))
            totals["xirr"] = _xirr(flows)
        else:
            totals["xirr"] = None
    except Exception:
        totals["xirr"] = None
    try:
        totals["benchmark"] = benchmark_block(items)
    except Exception:
        totals["benchmark"] = None
    return {"items": items, "totals": totals}


_DIGEST_SNAP_KEY = "portfolio_digest_snap_"


@router.get("/digest")
def portfolio_digest(user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """A glanceable digest of the book — value & P&L, biggest movers, model
    flags on held names, concentration, earnings due, and tax opportunities —
    plus what changed since the last time it was viewed. Educational, not advice."""
    from app.portfolio_tax import tax_block
    from app.portfolio_digest import build_digest, snapshot_from
    uk = f"u{user.id}"
    items = _build_items(db, uk)
    totals = compute_totals(items)
    tax = tax_block(items)
    results_due = _results_due(db, within_days=10)

    key = _DIGEST_SNAP_KEY + uk
    row = db.query(models.KVStore).filter_by(key=key).first()
    prev_snap = row.value if row else None

    digest = build_digest(items, totals, tax, results_due, prev_snap)

    # Persist today's value so the next visit shows a since-last delta. Only
    # refresh once a day so repeated loads don't collapse the comparison window.
    new_snap = snapshot_from(totals)
    if totals.get("value") is not None and (not prev_snap or prev_snap.get("date") != new_snap["date"]):
        if row:
            row.value = new_snap
        else:
            db.add(models.KVStore(key=key, value=new_snap))
        db.commit()
    return digest


@router.get("/xray")
def portfolio_xray_route(user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    """Factor / risk X-ray of the book + inverse-vol position-sizing suggestions.
    Each holding is scored against the WHOLE universe (not just the portfolio),
    so its Alpha/factors are its market-relative percentiles. A sizing aid, not
    investment advice."""
    from app.signals import ranked_visible
    from app.factors import portfolio_xray
    uk = f"u{user.id}"
    holdings = (db.query(models.PortfolioHolding)
                  .filter_by(user_key=uk).join(models.Company)
                  .order_by(models.Company.ticker).all())
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    ranked = {(r["ticker"] or "").upper(): r for r in ranked_visible(db)}
    items = []
    for h in holdings:
        co = h.company
        r = ranked.get((co.ticker or "").upper()) or {}
        price = price_by.get(h.company_id)
        items.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector,
            "qty": h.qty or 0.0, "price": price,
            "value": ((h.qty or 0.0) * price) if price else None,
            "alpha_score": r.get("alpha_score"), "rank": r.get("rank"),
            "factors": r.get("factors"), "volatility": r.get("volatility"),
            "verdict": r.get("verdict"),
        })
    xray = portfolio_xray(items)
    return {"items": items, "xray": xray, "risk": _risk_block(db, holdings)}


def _risk_block(db: Session, holdings) -> dict | None:
    """VaR / max-drawdown / XIRR for the book, fed by the split-adjusted Dhan
    HistoricalPrice series (trailing year). Pure math in app.portfolio_risk;
    every stat degrades to None on thin data rather than fabricating a number."""
    import datetime as _dt
    from app.portfolio_risk import risk_summary
    if not holdings:
        return None
    cids = {h.company_id: h for h in holdings}
    cutoff = (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
    # Dhan's series is already vendor split-adjusted — no ledger re-adjustment.
    closes_by: dict[str, dict] = {}
    price_rows = (db.query(models.HistoricalPrice)
                    .filter(models.HistoricalPrice.company_id.in_(list(cids)),
                            models.HistoricalPrice.date >= cutoff)
                    .order_by(models.HistoricalPrice.date).all())
    from app.price_hygiene import drop_bad_ticks
    raw_by: dict[str, list] = {}
    for hp in price_rows:
        h = cids.get(hp.company_id)
        if not h or hp.close is None:
            continue
        raw_by.setdefault((h.company.ticker or "").upper(), []).append(
            {"date": hp.date, "close": hp.close})
    for tk, series in raw_by.items():
        closes_by[tk] = {r["date"]: r["close"] for r in drop_bad_ticks(series)}

    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    hold_rows, cashflows, total_value = [], [], 0.0
    today = _dt.date.today()
    for h in holdings:
        tk = (h.company.ticker or "").upper()
        hold_rows.append({"ticker": tk, "qty": h.qty or 0.0})
        added = h.added_at.date() if isinstance(h.added_at, _dt.datetime) else today
        cashflows.append((added, -(h.qty or 0.0) * (h.avg_cost or 0.0)))
        price = price_by.get(h.company_id)
        if price:
            total_value += (h.qty or 0.0) * price
    if total_value:
        cashflows.append((today, total_value))
    return risk_summary(hold_rows, closes_by, cashflows, total_value)


@router.post("")
def upsert_holding(body: HoldingUpsert, user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    co = db.query(models.Company).filter_by(ticker=body.ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {body.ticker}")
    holding = (db.query(models.PortfolioHolding)
                 .filter_by(user_key=uk, company_id=co.id).first())
    bd = _parse_date(body.buy_date)
    if not holding:
        holding = models.PortfolioHolding(user_key=uk, company_id=co.id,
                                          qty=body.qty, avg_cost=body.avg_cost,
                                          buy_date=bd)
        db.add(holding)
    else:
        holding.qty = body.qty
        holding.avg_cost = body.avg_cost
        if bd is not None:
            holding.buy_date = bd
    db.commit()
    db.refresh(holding)
    # Return the enriched item with its weight computed across the full portfolio.
    items = _build_items(db, uk)
    compute_totals(items)
    for it in items:
        if it["id"] == holding.id:
            return it
    return _item(holding, (co.market.price if co.market else None),
                 db.query(models.Valuation).filter_by(company_id=co.id).first())


@router.delete("/{holding_id}")
def delete_holding(holding_id: int, user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    holding = (db.query(models.PortfolioHolding)
                 .filter_by(id=holding_id, user_key=uk).first())
    if holding:
        db.delete(holding)
        db.commit()
    return {"ok": True}


# ── Portfolio analysis + strategist ──────────────────────────────────────────

def build_analysis(items: list[dict], universe: list[dict]) -> dict:
    """Pure analysis over enriched holding items (post compute_totals) and the
    universe rows — unit-testable without a DB.

    EDUCATIONAL DECISION SUPPORT, NOT INVESTMENT ADVICE: every suggestion is a
    mechanical restatement of the terminal's own published data (verdicts, MoS,
    weights, holding terms). The user decides."""
    live = [i for i in items if i.get("value")]
    total = sum(i["value"] for i in live) or 0.0

    # Sector allocation
    sec: dict[str, dict] = {}
    for i in live:
        b = sec.setdefault(i.get("sector") or "Other", {"value": 0.0, "n": 0})
        b["value"] += i["value"]; b["n"] += 1
    sectors = sorted(
        [{"sector": k, "value": v["value"], "n": v["n"],
          "weight": (v["value"] / total) if total else None} for k, v in sec.items()],
        key=lambda x: -x["value"])

    # Concentration
    ws = sorted([(i["value"] / total) for i in live], reverse=True) if total else []
    conc = {"n": len(live),
            "top1": ws[0] if ws else None,
            "top3": sum(ws[:3]) if ws else None,
            "top5": sum(ws[:5]) if ws else None,
            "hhi": sum(w * w for w in ws) if ws else None}

    # Term split (LTCG)
    lt = [i for i in live if i.get("term") == "long"]
    st = [i for i in live if i.get("term") == "short"]
    term = {"long":  {"n": len(lt), "value": sum(i["value"] for i in lt),
                      "weight": (sum(i["value"] for i in lt) / total) if total else None},
            "short": {"n": len(st), "value": sum(i["value"] for i in st),
                      "weight": (sum(i["value"] for i in st) / total) if total else None},
            "turning_lt_soon": sorted(
                [{"ticker": i["ticker"], "days_to_lt": i["days_to_lt"]}
                 for i in st if i.get("days_to_lt") is not None and i["days_to_lt"] <= 90],
                key=lambda x: x["days_to_lt"])}

    # Verdict alignment (value-weighted)
    va: dict[str, float] = {}
    for i in live:
        v = i.get("verdict") or "NO CALL"
        v = "REDUCE" if v == "TRIM" else v
        va[v] = va.get(v, 0.0) + i["value"]
    verdicts = {k: (v / total) if total else None for k, v in sorted(va.items(), key=lambda x: -x[1])}

    # ── Strategist: mechanical, reasoned suggestions ─────────────────────────
    recs: list[dict] = []

    def rec(action, ticker, name, reasons, priority, mos=None):
        recs.append({"action": action, "ticker": ticker, "name": name,
                     "reasons": reasons, "priority": priority, "mos": mos})

    for i in sorted(live, key=lambda x: -(x.get("value") or 0)):
        w = (i["value"] / total) if total else 0
        reasons = []
        verdict = "REDUCE" if i.get("verdict") == "TRIM" else i.get("verdict")
        if verdict in ("SELL", "REDUCE"):
            reasons.append(f"model verdict is {verdict}"
                           + (f" (MoS {i['mos']*100:+.0f}%)" if i.get("mos") is not None else ""))
        elif i.get("mos") is not None and i["mos"] < -0.30:
            reasons.append(f"trades {abs(i['mos'])*100:.0f}% ABOVE the model's fair value")
        if w > 0.30:
            reasons.append(f"position is {w*100:.0f}% of the book — single-name concentration")
        if reasons:
            if i.get("term") == "short" and (i.get("days_to_lt") or 0) > 0 and i["days_to_lt"] <= 90:
                reasons.append(f"turns LONG-TERM in {i['days_to_lt']}d "
                               f"(LTCG 12.5% vs STCG 20%) — weigh exit timing against the tax step-down")
            rec("REVIEW EXIT" if verdict in ("SELL", "REDUCE") else "REVIEW TRIM",
                i["ticker"], i["name"], reasons, 1 if verdict == "SELL" else 2,
                mos=i.get("mos"))

    held = {i["ticker"] for i in items}
    port_secs = {s["sector"] for s in sectors}
    adds = [u for u in universe
            if u.get("ticker") not in held
            and u.get("verdict") == "BUY"
            and (u.get("mos") or 0) >= 0.25
            and (u.get("confidence") or "").upper() not in ("LOW",)]
    adds.sort(key=lambda u: -(u.get("mos") or 0))
    diversifiers = [u for u in adds if u.get("sector") not in port_secs][:3]
    core = [u for u in adds if u.get("sector") in port_secs][:3]
    for u in diversifiers + core:
        rec("ADD CANDIDATE", u["ticker"], u.get("name"),
            [f"model verdict BUY at MoS {u['mos']*100:+.0f}%",
             ("outside the book's current sectors — diversifies"
              if u in diversifiers else "within a sector already held")],
            3, mos=u.get("mos"))

    for i in live:
        verdict = "REDUCE" if i.get("verdict") == "TRIM" else i.get("verdict")
        if verdict == "BUY" and (i.get("mos") or 0) >= 0.25 and (i["value"] / total if total else 0) < 0.05:
            rec("TOP-UP CANDIDATE", i["ticker"], i["name"],
                [f"held at only {(i['value']/total)*100:.1f}% weight while the model"
                 f" still sees MoS {i['mos']*100:+.0f}%"], 3, mos=i.get("mos"))

    recs.sort(key=lambda r: r["priority"])
    from app.portfolio_tax import tax_block
    return {
        "sectors": sectors, "concentration": conc, "term": term,
        "tax": tax_block(items),
        "verdict_mix": verdicts, "recommendations": recs[:12],
        "disclaimer": ("Educational decision support derived mechanically from the "
                       "terminal's published verdicts, margins of safety, weights and "
                       "holding periods. Not investment advice; not SEBI-registered "
                       "research or advisory. Tax notes assume Indian listed-equity "
                       "LTCG/STCG rules — verify with your tax adviser."),
    }


@router.get("/analysis")
def portfolio_analysis(user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Allocation, concentration, LTCG term split, verdict alignment, risk and
    the strategist's mechanical suggestions — one payload."""
    uk = f"u{user.id}"
    items = _build_items(db, uk)
    compute_totals(items)
    universe = []
    try:
        price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
        for co, val in (db.query(models.Company, models.Valuation)
                          .join(models.Valuation, models.Valuation.company_id == models.Company.id)
                          .all()):
            universe.append({"ticker": co.ticker, "name": co.name, "sector": co.sector,
                             "price": price_by.get(co.id), "mos": val.mos,
                             "verdict": val.verdict, "confidence": val.confidence})
    except Exception:
        db.rollback()
    analysis = build_analysis(items, universe)
    holdings = (db.query(models.PortfolioHolding).filter_by(user_key=uk)
                  .join(models.Company).all())
    analysis["risk"] = _risk_block(db, holdings)

    # Momentum overlay (price vs 50-DMA) for every ticker the manager touches,
    # and inverse-vol target weights for rupee-sized rebalancing.
    import datetime as _dt
    touch = {r["ticker"] for r in analysis.get("recommendations", [])}
    mom_by: dict[str, str] = {}
    if touch:
        try:
            cid_by = {c.ticker: c.id for c in
                      db.query(models.Company).filter(models.Company.ticker.in_(touch)).all()}
            cutoff = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
            rows = (db.query(models.HistoricalPrice)
                      .filter(models.HistoricalPrice.company_id.in_(list(cid_by.values())),
                              models.HistoricalPrice.date >= cutoff)
                      .order_by(models.HistoricalPrice.date).all())
            closes: dict[int, list] = {}
            for hp in rows:
                if hp.close is not None:
                    closes.setdefault(hp.company_id, []).append(hp.close)
            for tk, cid in cid_by.items():
                ser = closes.get(cid) or []
                if len(ser) >= 50:
                    mom_by[tk] = "above" if ser[-1] >= sum(ser[-50:]) / 50 else "below"
        except Exception:
            db.rollback()
    target_by: dict[str, float] = {}
    try:
        from app.signals import ranked_visible
        from app.factors import portfolio_xray
        ranked = {(r["ticker"] or "").upper(): r for r in ranked_visible(db)}
        xr_items = [{"ticker": i["ticker"], "value": i.get("value"),
                     "volatility": (ranked.get(i["ticker"]) or {}).get("volatility")}
                    for i in items if i.get("value")]
        portfolio_xray(xr_items)
        target_by = {i["ticker"]: i.get("suggested_weight") for i in xr_items
                     if i.get("suggested_weight") is not None}
    except Exception:
        db.rollback()
    # Per-name intelligence for the touched tickers: institutional/promoter
    # flow (quarterly shareholding deltas) + results momentum (PAT YoY, EPS
    # surprise) — the same published data the Ownership and Results pages show.
    intel_by: dict[str, dict] = {}
    quote_by: dict[str, dict] = {}
    if touch:
        try:
            from app.results_logic import eps_surprise
            cos = {c.id: c.ticker for c in
                   db.query(models.Company).filter(models.Company.ticker.in_(touch)).all()}
            for row in (db.query(models.CompanyInsight)
                          .filter(models.CompanyInsight.company_id.in_(list(cos))).all()):
                d = row.data or {}
                own = d.get("ownership") or {}
                res = d.get("results") or {}
                sur = eps_surprise(d.get("forecasts")) or {}
                intel_by[cos[row.company_id]] = {
                    "inst_delta": (own.get("institutional") or {}).get("delta"),
                    "promoter_delta": (own.get("promoter") or {}).get("delta"),
                    "pat_yoy": res.get("pat_yoy"),
                    "surprise_pct": sur.get("surprise_pct"),
                }
            for u in universe:
                if u["ticker"] in touch:
                    quote_by[u["ticker"]] = {"price": u.get("price"), "mos": u.get("mos")}
        except Exception:
            db.rollback()
    # v4 evidence + macro (precomputed nightly; built once on a cold start).
    evidence = macro = None
    try:
        from app.manager_engine import (load_evidence, load_macro,
                                         snapshot_evidence, ENGINE_SCHEMA)
        evidence, macro = load_evidence(db), load_macro(db)
        # Rebuild on a cold start OR when the stored blob predates the current
        # ENGINE_SCHEMA (new fields) — the bump is meant to force this, but nothing
        # was actually checking the version, so a schema change never took effect
        # until the nightly job.
        if not evidence or evidence.get("schema") != ENGINE_SCHEMA:
            snapshot_evidence(db)
            evidence, macro = load_evidence(db), load_macro(db)
    except Exception:
        db.rollback()
    # Results-awareness: names reporting within ~12 days — the manager should
    # say "wait for the print" rather than commit ahead of an earnings event.
    results_due = _results_due(db, within_days=12)
    # Investable cash the owner has flagged (dry powder) — sizes and gates adds.
    cash = _get_cash(db, uk)
    analysis["manager"] = manager_report(items, analysis, mom_by, target_by,
                                         intel_by, quote_by, evidence, macro,
                                         results_due=results_due, cash=cash)
    _apply_construction_checks(db, items, analysis)
    return analysis


_CASH_KEY = "fm_cash_"


def _get_cash(db, user_key: str):
    row = db.query(models.KVStore).filter_by(key=_CASH_KEY + user_key).first()
    return (row.value or {}).get("amount") if row else None


# The results calendar is UNIVERSE-GLOBAL (same for every user) but scanning all
# Company + CompanyInsight rows is expensive — TTL-cache it so a portfolio page
# view doesn't re-scan the whole insight blob table per request (audit D8).
_RESULTS_DUE_CACHE: dict[int, tuple[float, dict]] = {}
_RESULTS_DUE_TTL = 1800.0


def _results_due(db, within_days: int = 12) -> dict:
    """{ticker: {date, days_away}} for names with a RESULTS board-meeting within
    `within_days` (or reported in the last 2), from the results calendar."""
    import datetime as _dt2
    import re as _re2
    import time as _t
    cached = _RESULTS_DUE_CACHE.get(within_days)
    if cached and _t.time() - cached[0] < _RESULTS_DUE_TTL:
        return cached[1]
    today = _dt2.date.today()
    out: dict[str, dict] = {}
    try:
        cos = {c.id: c.ticker for c in db.query(models.Company).all()}
        for r in db.query(models.CompanyInsight).all():
            tk = cos.get(r.company_id)
            if not tk or not r.data:
                continue
            for m in (r.data.get("board_meetings") or []):
                ag = str(m.get("agenda") or "")
                if not _re2.search(r"financial result|quarterly result|audited", ag, _re2.I):
                    continue
                mm = _re2.match(r"(\d{2})-(\d{2})-(\d{4})", str(m.get("date") or ""))
                if not mm:
                    continue
                d = _dt2.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
                days = (d - today).days
                if -2 <= days <= within_days:
                    cur = out.get(tk)
                    if cur is None or abs(days) < abs(cur["days_away"]):
                        out[tk] = {"date": d.isoformat(), "days_away": days}
    except Exception:
        db.rollback()
        return out          # don't cache a partial result from a failed scan
    _RESULTS_DUE_CACHE[within_days] = (_t.time(), out)
    return out


def _apply_construction_checks(db, items, analysis):
    """Portfolio-construction overlay on the manager's ADD ideas: (a) return
    correlation vs the existing book — a high-corr add is factor duplication,
    not diversification; (b) sector cap — adding to a ≥30% sector gets called
    out. Adjusts conviction a notch and says why; never silently reorders."""
    import datetime as _dt3
    mgr = (analysis or {}).get("manager") or {}
    actions = mgr.get("actions") or []
    adds = [a for a in actions if a["action"].startswith(("ADD", "TOP-UP"))][:8]
    if not adds:
        return
    live = [i for i in items if i.get("value")]
    total = sum(i["value"] for i in live) or 0.0
    sec_w = {s["sector"]: (s.get("weight") or 0)
             for s in (analysis.get("sectors") or [])}
    # sector cap first (no data needed)
    for a in adds:
        sec = a.get("sector")
        if sec and sec_w.get(sec, 0) >= 0.30:
            a["conviction"] = max(5, a["conviction"] - 5)
            a["reasons"] = a.get("reasons", []) + [
                f"{sec} is already {sec_w[sec]*100:.0f}% of the book — this add concentrates it further"]
    # correlation vs book (needs ≥3 priced holdings to be meaningful)
    if len(live) >= 3 and total:
        try:
            tks = {a["ticker"] for a in adds} | {i["ticker"] for i in live}
            cid_by = {c.ticker: c.id for c in
                      db.query(models.Company).filter(models.Company.ticker.in_(tks)).all()}
            cutoff = (_dt3.date.today() - _dt3.timedelta(days=380)).isoformat()
            rows = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                             models.HistoricalPrice.close)
                      .filter(models.HistoricalPrice.company_id.in_(list(cid_by.values())),
                              models.HistoricalPrice.date >= cutoff)
                      .order_by(models.HistoricalPrice.date).all())
            px: dict[int, dict[str, float]] = {}
            for cid, d, c in rows:
                if c:
                    px.setdefault(cid, {})[d] = c

            def rets(cid):
                ser = sorted((px.get(cid) or {}).items())
                return {d2: (v2 / v1 - 1) for (d1, v1), (d2, v2) in zip(ser, ser[1:]) if v1}

            def corr(ra, rb):
                common = sorted(set(ra) & set(rb))
                if len(common) < 60:
                    return None
                xa, xb = [ra[d] for d in common], [rb[d] for d in common]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                num = sum((p - ma) * (q - mb) for p, q in zip(xa, xb))
                da = sum((p - ma) ** 2 for p in xa) ** 0.5
                dbm = sum((q - mb) ** 2 for q in xb) ** 0.5
                return num / (da * dbm) if da and dbm else None

            book_rets = {i["ticker"]: rets(cid_by.get(i["ticker"]))
                         for i in live if cid_by.get(i["ticker"])}
            for a in adds:
                cid = cid_by.get(a["ticker"])
                if not cid:
                    continue
                ra = rets(cid)
                num = den = 0.0
                for i in live:
                    rb = book_rets.get(i["ticker"])
                    if not rb:
                        continue
                    c0 = corr(ra, rb)
                    if c0 is not None:
                        num += c0 * i["value"]
                        den += i["value"]
                if den:
                    avg_c = num / den
                    if avg_c >= 0.65:
                        a["conviction"] = max(5, a["conviction"] - 4)
                        a["reasons"] = a.get("reasons", []) + [
                            f"moves {avg_c:.0%} in step with the current book — duplicates existing risk, adds little diversification"]
                    elif avg_c <= 0.35:
                        a["reasons"] = a.get("reasons", []) + [
                            f"low co-movement with the book ({avg_c:.0%}) — genuine diversification"]
        except Exception:
            db.rollback()
    actions.sort(key=lambda x: (-x["conviction"], x.get("priority", 9)))


# ── Fund Manager report ──────────────────────────────────────────────────────

def manager_report(items: list[dict], analysis: dict, mom_by: dict | None = None,
                   target_by: dict | None = None, intel_by: dict | None = None,
                   quote_by: dict | None = None, evidence: dict | None = None,
                   macro: dict | None = None, results_due: dict | None = None,
                   cash: float | None = None) -> dict:
    """The fund-manager brief, v4: conviction comes from TRIANGULATED evidence
    (model × analyst consensus × own valuation band, forensic quality, flow,
    results, momentum, macro regime) — never from the DCF alone. Suspect model
    fair values are set aside and the action says so. Rupee sizing against
    inverse-vol targets and the LTCG notes carry over from v3. Pure —
    unit-testable. Same rule as everything here: mechanical restatement of
    published data, educational, the owner decides."""
    live = [i for i in items if i.get("value")]
    total = sum(i["value"] for i in live) or 0.0
    mom_by = mom_by or {}
    target_by = target_by or {}
    intel_by = intel_by or {}
    quote_by = quote_by or {}
    by_ticker = {i["ticker"]: i for i in live}
    ev_names = (evidence or {}).get("names") or {}
    ev_weights = (evidence or {}).get("weights") or {}
    results_due = results_due or {}

    def _levels(tk, i):
        """Entry band / target / upside from the model's OWN fair value.
        Entry-below = the price at which MoS reaches the BUY gate (25%);
        target = fair value on the model's 12–18 month horizon."""
        price = (i or {}).get("price") or (quote_by.get(tk) or {}).get("price")
        fair = (i or {}).get("intrinsic")
        if fair is None:
            q = quote_by.get(tk) or {}
            if q.get("price") and q.get("mos") is not None:
                fair = q["price"] * (1 + q["mos"])
                price = price or q["price"]
        if not (price and fair and fair > 0):
            return {}
        return {"price": round(price, 1), "target": round(fair, 1),
                "entry_below": round(fair / 1.25, 1),
                "upside_pct": round(fair / price - 1, 4)}

    def _intel_reasons(tk):
        out = []
        iv = intel_by.get(tk) or {}
        d = iv.get("inst_delta")
        if d is not None and abs(d) >= 0.3:
            out.append(f"institutions {'added' if d > 0 else 'cut'} {abs(d):.1f}pp last quarter")
        pd = iv.get("promoter_delta")
        if pd is not None and pd <= -1.0:
            out.append(f"promoter stake fell {abs(pd):.1f}pp — governance check")
        py = iv.get("pat_yoy")
        if py is not None and abs(py) >= 0.15:
            out.append(f"latest quarter PAT {py*100:+.0f}% YoY")
        sp = iv.get("surprise_pct")
        if sp is not None and abs(sp) >= 0.02:
            out.append(f"FY EPS {'beat' if sp > 0 else 'missed'} the Street by {abs(sp)*100:.0f}%")
        return out

    from app.manager_engine import (conviction_add, conviction_trim,
                                    macro_note, what_would_flip, PRIOR_WEIGHTS)

    def _range_from(ev, lv):
        """Attach an honest target range: consensus low/high plus the model FV
        (when it survived cross-examination) — a corridor, not false precision."""
        if not (ev and lv and lv.get("target")):
            return lv
        cons = ev.get("consensus") or {}
        pts = [x for x in (cons.get("low"), cons.get("high"), lv.get("target")) if x]
        if not (ev.get("tri") or {}).get("suspect"):
            fv = (ev.get("model") or {}).get("intrinsic")
            if fv and fv > 0:
                pts.append(fv)
        if len(pts) >= 2:
            lv = dict(lv)
            lv["target_low"], lv["target_high"] = round(min(pts), 1), round(max(pts), 1)
        return lv
    weights = ev_weights or PRIOR_WEIGHTS

    def _tax_notes(reasons):
        return [x for x in reasons if "LTCG" in x or "long-term" in x.lower()]

    def _mom_reason(tk):
        m = mom_by.get(tk)
        return [f"trades {m} its 50-DMA"] if m in ("above", "below") else []

    def _score(action_name: str, tk: str, base_reasons: list[str]):
        """v4 conviction from evidence; falls back to a v3-style score for a
        name the nightly evidence build hasn't covered. The fallback attaches
        the LIVE intel (ownership flow, results momentum, 50-DMA) that the v4
        evidence reasons would otherwise carry — the v4 rework had orphaned
        _intel_reasons/mom_by entirely, so evidence-pending names silently lost
        all flow/momentum context (and the 50-DMA query ran for nothing)."""
        ev = ev_names.get(tk)
        i = by_ticker.get(tk)
        w_in_book = (i["value"] / total) if (i and total) else None
        if ev:
            if action_name.startswith("REVIEW"):
                cv, rs = conviction_trim(ev, w_in_book, weights, macro)
            else:
                cv, rs = conviction_add(ev, weights, macro)
            return cv, rs + _tax_notes(base_reasons), ev
        # fallback (thin): old MoS-based path, marked as such + live intel
        cv = 40
        mos = (i or {}).get("mos")
        if mos is not None:
            cv += min(20, int(abs(mos) * 30))
        reasons = (base_reasons + _intel_reasons(tk) + _mom_reason(tk)
                   + ["evidence pending for this name"])
        return max(10, min(70, cv)), reasons, None

    actions = []
    for r in analysis.get("recommendations", []):
        a = dict(r)
        tk = r["ticker"]
        cv, reasons, ev = _score(r["action"], tk, list(r.get("reasons") or []))
        a["reasons"] = reasons
        a["conviction"] = cv
        i = by_ticker.get(tk)
        if r["action"].startswith("REVIEW"):
            w = (i["value"] / total) if (i and total) else 0
            tgt = target_by.get(tk)
            if i and total and tgt is not None and w > tgt:
                a["size_inr"] = round((w - tgt) * total)
                a["size_note"] = f"to its risk-balanced target of {tgt*100:.0f}%"
        elif r["action"].startswith(("ADD", "TOP-UP")) and total:
            frac = 0.015 if (macro or {}).get("regime") == "risk_off" else 0.03
            a["size_inr"] = round(frac * total)
            a["size_note"] = ("a half tranche (~1.5% of book — defensive tape)"
                              if frac == 0.015 else
                              "a starter tranche (~3% of book; scale on conviction)")
        # Levels: honest about a suspect model — quote the consensus target
        # instead of a fair value the evidence just rejected.
        lv = _levels(tk, by_ticker.get(tk))
        if ev and (ev.get("tri") or {}).get("suspect"):
            ct = (ev.get("consensus") or {}).get("target")
            price = ev.get("price") or (lv or {}).get("price")
            lv = ({"price": round(price, 1), "target": round(ct, 1),
                   "upside_pct": round(ct / price - 1, 4), "basis": "consensus"}
                  if (ct and price) else {})
        a["levels"] = _range_from(ev, lv)
        if ev:
            a["flip"] = what_would_flip(ev, r["action"])
            a["evidence"] = {
                "suspect": (ev.get("tri") or {}).get("suspect"),
                "val_blend": (ev.get("tri") or {}).get("score"),
                "val_sources": (ev.get("tri") or {}).get("used"),
                "quality": (ev.get("quality") or {}).get("composite"),
                "red_flags": ((ev.get("quality") or {}).get("red_flags") or [])
                             + [f"news: {n}" for n in (ev.get("news_flags") or [])],
                "pe_pct_5y": (ev.get("band") or {}).get("combined_pct"),
                "alpha": ev.get("alpha"),
            }
        actions.append(a)

    # Evidence-driven ADD candidates the MoS gate alone would never surface —
    # and, symmetrically, it can no longer surface a red-flagged name.
    held = {i["ticker"] for i in items}
    queued = {a["ticker"] for a in actions}
    port_secs = {s["sector"] for s in (analysis.get("sectors") or [])}
    cands = []
    for tk, ev in ev_names.items():
        if tk in held or tk in queued:
            continue
        tri, q, momo = ev.get("tri") or {}, ev.get("quality") or {}, ev.get("momo") or {}
        if tri.get("score") is None or tri["score"] < 0.15:
            continue
        if (q.get("composite") or 0) < 55 or (q.get("red_flags") or []):
            continue
        if momo.get("above_200dma") is False and (momo.get("mom_pct") or 50) < 50:
            continue
        cv, reasons = conviction_add(ev, weights, macro)
        # Levels straight from the evidence: the model's fair value when it
        # survived cross-examination, else the consensus target, else CMP only.
        price = ev.get("price")
        fair = (ev.get("model") or {}).get("intrinsic") if not tri.get("suspect") else None
        ct = (ev.get("consensus") or {}).get("target")
        if price and fair and fair > 0:
            lv = {"price": round(price, 1), "target": round(fair, 1),
                  "entry_below": round(fair / 1.25, 1),
                  "upside_pct": round(fair / price - 1, 4)}
        elif price and ct:
            lv = {"price": round(price, 1), "target": round(ct, 1),
                  "upside_pct": round(ct / price - 1, 4), "basis": "consensus"}
        else:
            lv = {"price": round(price, 1)} if price else {}
        cands.append({"action": "ADD CANDIDATE", "ticker": tk,
                      "name": ev.get("name") or tk.title(), "reasons": reasons +
                      (["outside the book's current sectors — diversifies"]
                       if ev.get("sector") not in port_secs else
                       ["within a sector already held"]),
                      "priority": 3, "conviction": cv, "sector": ev.get("sector"),
                      "levels": _range_from(ev, lv),
                      "flip": what_would_flip(ev, "ADD CANDIDATE"),
                      "evidence": {
                          "suspect": tri.get("suspect"), "val_blend": tri.get("score"),
                          "val_sources": tri.get("used"),
                          "quality": q.get("composite"),
                          "red_flags": (q.get("red_flags") or [])
                                       + [f"news: {n}" for n in (ev.get("news_flags") or [])],
                          "pe_pct_5y": (ev.get("band") or {}).get("combined_pct"),
                          "alpha": ev.get("alpha")}})
    cands.sort(key=lambda x: -x["conviction"])
    for c in cands[:4]:
        if total:
            frac = 0.015 if (macro or {}).get("regime") == "risk_off" else 0.03
            c["size_inr"] = round(frac * total)
            c["size_note"] = ("a half tranche (~1.5% of book — defensive tape)"
                              if frac == 0.015 else
                              "a starter tranche (~3% of book; scale on conviction)")
        actions.append(c)

    # ── Results-awareness: hold ahead of an imminent earnings print ──────────
    # A name reporting within days is an event the manager shouldn't front-run:
    # the print can reprice it hard either way. Flag it, defer a fresh ADD (the
    # "would change my mind" line becomes "wait for the print"), and never let a
    # results-imminent name sit at the very top of the ADD queue.
    for a in actions:
        rd = results_due.get(a["ticker"])
        if not rd:
            continue
        d = rd["days_away"]
        a["results_due"] = rd
        if d >= 0:
            when = "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"
            if a["action"].startswith(("ADD", "TOP-UP")):
                a["hold_for_results"] = True
                a["conviction"] = min(a["conviction"], 55)     # cap — don't lead the queue
                a["reasons"] = ["⏳ reports " + when + f" ({rd['date']}) — wait for the print, "
                                "then take the call"] + a.get("reasons", [])
                a["flip"] = ["the earnings print landing in line with (or ahead of) the thesis"] \
                    + (a.get("flip") or [])
            else:
                a["reasons"] = a.get("reasons", []) + [f"reports {when} — you may prefer to "
                                                       "act after the print"]
        else:
            a["reasons"] = a.get("reasons", []) + [f"just reported ({rd['date']}) — the "
                                                   "numbers above reflect it"]

    # ── Cash / dry-powder awareness ─────────────────────────────────────────
    # Size and gate ADD ideas by the investable cash the owner actually flagged.
    # No dry powder → adds are "fund from the exit queue first", not fresh buys.
    adds = [a for a in actions if a["action"].startswith(("ADD", "TOP-UP"))]
    cash_state = {"amount": cash, "deployable": None, "n_funded": 0}
    if cash is not None and adds:
        remaining = float(cash)
        funded = 0
        for a in sorted(adds, key=lambda x: -x["conviction"]):
            want = a.get("size_inr") or 0
            if a.get("hold_for_results"):
                a["cash_note"] = "hold for the print before deploying"
                continue
            if remaining >= want and want > 0:
                a["fundable"] = True
                remaining -= want
                funded += 1
            else:
                a["fundable"] = False
                a["cash_note"] = ("fund by trimming the exit queue first — "
                                  "dry powder is committed")
        cash_state["deployable"] = round(float(cash) - remaining)
        cash_state["n_funded"] = funded

    # ── Capital-rotation strategist ──────────────────────────────────────────
    # The self-funding way to act on a high-conviction idea with no cash: trim
    # the weakest / most-overvalued HELD names to fund the strongest new ones.
    # This is the responsible alternative to leverage — a conviction upgrade
    # with zero new capital and zero margin-call risk. Pure restatement of the
    # engine's own queues; the owner decides.
    rotations = []
    sell_side = sorted(
        [a for a in actions if a["action"].startswith("REVIEW") and a.get("size_inr")],
        key=lambda x: -x["size_inr"])
    buy_side = [a for a in actions if a["action"].startswith(("ADD", "TOP-UP"))
                and not a.get("hold_for_results") and a.get("fundable") is not True
                and a.get("size_inr")]
    buy_side.sort(key=lambda x: -x["conviction"])
    pool = [{"ticker": s["ticker"], "name": s.get("name"), "free": s["size_inr"],
             "conv": s.get("conviction", 0),
             "why": ("overvalued vs the evidence"
                     if (s.get("evidence") or {}).get("val_blend") is not None
                     and (s["evidence"]["val_blend"] or 0) < 0 else "flagged for review")}
            for s in sell_side]
    for b in buy_side:
        need = b["size_inr"]
        funders, freed = [], 0
        for p in pool:
            if freed >= need:
                break
            if p["free"] <= 0:
                continue
            take = min(p["free"], need - freed)
            funders.append({"ticker": p["ticker"], "name": p["name"],
                            "amount": round(take), "why": p["why"]})
            freed += take
            p["free"] -= take
        if funders and freed >= need * 0.8:
            rotations.append({
                "add": {"ticker": b["ticker"], "name": b.get("name"),
                        "conviction": b["conviction"], "size_inr": need},
                "fund_from": funders, "frees_inr": round(freed),
                "note": (f"Fund {b['ticker']} (conviction {b['conviction']}) by trimming "
                         + ", ".join(f["ticker"] for f in funders)
                         + " — a conviction upgrade with no new capital.")})
    rotations = rotations[:4]

    actions.sort(key=lambda x: (-x["conviction"], x.get("priority", 9)))

    # PM note — composed strictly from the numbers above.
    conc = analysis.get("concentration") or {}
    term = analysis.get("term") or {}
    vm = analysis.get("verdict_mix") or {}
    buy_w = vm.get("BUY") or 0
    risk_w = (vm.get("REDUCE") or 0) + (vm.get("SELL") or 0)
    lines = []
    if total:
        lines.append(f"The book runs {conc.get('n', 0)} positions worth ₹{total:,.0f}.")
    if conc.get("top1") is not None and conc["top1"] > 0.30:
        lines.append(f"Concentration is the first conversation: the largest position is "
                     f"{conc['top1']*100:.0f}% of the book.")
    if risk_w > 0.25:
        lines.append(f"{risk_w*100:.0f}% of value sits in names the model now rates "
                     f"REDUCE/SELL — review the exit queue first.")
    elif buy_w > 0.5:
        lines.append(f"{buy_w*100:.0f}% of value is in names the model still rates BUY — "
                     f"the book is broadly aligned with the research.")
    soon = (term.get("turning_lt_soon") or [])
    if soon:
        lines.append(f"{len(soon)} position{'s' if len(soon) != 1 else ''} turn"
                     f"{'' if len(soon) != 1 else 's'} long-term within 90 days — "
                     f"sequence any trims after the LTCG step-down where the thesis allows.")
    n_susp = sum(1 for a in actions[:10]
                 if (a.get("evidence") or {}).get("suspect"))
    if n_susp:
        lines.append(f"On {n_susp} name{'s' if n_susp != 1 else ''} the model's fair value "
                     f"failed cross-examination against consensus and the valuation band — "
                     f"it was set aside rather than trusted.")
    # Results-wait: name the imminent prints the manager is holding for.
    waiting = [a for a in actions[:10] if a.get("hold_for_results")]
    if waiting:
        names = ", ".join(a["ticker"] for a in waiting[:4])
        lines.append(f"Holding fire on {names}: {'they report' if len(waiting) != 1 else 'it reports'} "
                     f"within days — better to see the print than commit ahead of the event.")
    # Dry-powder discipline: size the whole plan to the cash actually available.
    if cash is not None:
        if cash <= 0:
            lines.append("No investable cash flagged — any add should be funded by trimming "
                         "the exit queue first, not fresh money.")
        elif cash_state.get("deployable"):
            lines.append(f"Dry powder ₹{cash:,.0f}: enough to fund {cash_state['n_funded']} of the "
                         f"top adds (₹{cash_state['deployable']:,.0f} deployed); the rest wait for "
                         f"cash or a trim.")
        else:
            lines.append(f"Dry powder ₹{cash:,.0f} — below a starter tranche; fund adds from trims.")
    # Capital-rotation strategy: the self-funding path to a conviction upgrade.
    if rotations:
        r0 = rotations[0]
        lines.append(f"Self-funding move: {r0['note']} "
                     f"{len(rotations)} such rotation{'s' if len(rotations) != 1 else ''} available — "
                     f"a way to act on the best ideas without new capital or leverage.")
    if not lines:
        lines.append("No structural flags: sizing, alignment and terms all read clean.")
    # Tax-smart layer: exemption harvesting, loss harvesting, ST→LT deferral.
    from app.tax_advisor import tax_plan, tax_note, estimate_tax
    tax = tax_plan(items)
    tnote = tax_note(tax)
    if tnote:
        lines.append(tnote)
    # After-tax proceeds on each trim/exit the manager proposes.
    exemption_left = tax.get("ltcg_exemption_usable") is not None and 125000.0 or 125000.0
    by_tk = {i["ticker"]: i for i in live}
    for a in actions:
        if a["action"].startswith("REVIEW") and a.get("size_inr"):
            it = by_tk.get(a["ticker"])
            if it and it.get("value") and it.get("pnl") is not None and it.get("term"):
                frac = min(1.0, a["size_inr"] / it["value"]) if it["value"] else 0
                gain = it["pnl"] * frac
                t = estimate_tax(gain, it["term"], exemption_left if it["term"] == "long" else 0)
                a["tax_estimate"] = t
                a["after_tax_inr"] = round(a["size_inr"] - max(0, t))
                if it["term"] == "long" and gain > 0:
                    exemption_left = max(0, exemption_left - gain)
    mnote = macro_note(macro or {})
    if mnote:
        lines.insert(0, mnote)
    return {"actions": actions[:10], "note": " ".join(lines), "aum": total,
            "tax": tax, "cash": cash_state, "rotations": rotations,
            "leverage_policy": ("This desk will not recommend pledging your holdings or "
                                "borrowing to invest. Leverage magnifies drawdowns and can "
                                "force a sale of your shares in a margin call at the worst "
                                "time — it funds conviction from rotation or fresh cash, "
                                "never from debt."),
            "results_wait": [{"ticker": a["ticker"], "name": a.get("name"),
                              **a["results_due"]} for a in actions if a.get("hold_for_results")],
            "macro": {k: (macro or {}).get(k) for k in
                      ("regime", "breadth_200dma", "breadth_50dma", "breadth_trend",
                       "vix", "rates", "nifty", "rs_leaders", "rs_laggards", "commodities",
                       "as_of")} if macro else None,
            "engine": {"version": "v4-triangulated",
                       "evidence_as_of": (evidence or {}).get("as_of"),
                       "calibration_as_of": (evidence or {}).get("calibration_as_of"),
                       "weights": weights}}


@router.post("/sync-dhan")
def sync_dhan_holdings(user: models.User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """One-click import of the owner's ACTUAL holdings from their own Dhan
    account (GET /v2/holdings — a read-only data endpoint on the same token
    the price feed uses; no order/trading API is ever touched). Upserts
    qty + avg cost; names outside coverage are reported, never dropped."""
    import httpx as _httpx
    from app.dhan import client as _dhan
    tok = _dhan.access_token()
    if not tok:
        raise HTTPException(503, "Dhan feed is not configured.")
    try:
        r = _httpx.get("https://api.dhan.co/v2/holdings",
                       headers={"access-token": tok, "Accept": "application/json"},
                       timeout=20)
        r.raise_for_status()
        rows = r.json()
    except _httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Dhan holdings error: HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(502, f"Dhan holdings error: {type(e).__name__}")
    if not isinstance(rows, list):
        rows = (rows or {}).get("data") or []

    uk = f"u{user.id}"
    imported, uncovered = 0, []
    for h in rows:
        sym = str(h.get("tradingSymbol") or "").upper().strip()
        sym = sym.split("-")[0] if sym.endswith(("-EQ", "-BE")) else sym
        qty = h.get("totalQty") or h.get("availableQty") or 0
        avg = h.get("avgCostPrice") or 0
        if not sym or not qty or not avg:
            continue
        co = db.query(models.Company).filter_by(ticker=sym).first()
        if not co:
            uncovered.append(sym)
            continue
        holding = (db.query(models.PortfolioHolding)
                     .filter_by(user_key=uk, company_id=co.id).first())
        if not holding:
            db.add(models.PortfolioHolding(user_key=uk, company_id=co.id,
                                           qty=float(qty), avg_cost=float(avg)))
        else:
            holding.qty, holding.avg_cost = float(qty), float(avg)
        imported += 1
    db.commit()
    return {"imported": imported, "uncovered": uncovered,
            "note": "Buy dates aren't in the holdings API — set them per "
                    "position for exact LTCG terms (tradebook import can come later)."}
