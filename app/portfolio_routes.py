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


class HoldingUpsert(BaseModel):
    ticker: str
    qty: float
    avg_cost: float
    buy_date: str | None = None    # ISO YYYY-MM-DD (optional)


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
    return {
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


def _build_items(db: Session, uk: str) -> list[dict]:
    holdings = (db.query(models.PortfolioHolding)
                  .filter_by(user_key=uk)
                  .join(models.Company).order_by(models.Company.ticker).all())
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()
    actions_by: dict[int, list[dict]] = {}
    for a in db.query(models.CorporateAction).all():
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
    return {"items": items, "totals": totals}


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

    def rec(action, ticker, name, reasons, priority):
        recs.append({"action": action, "ticker": ticker, "name": name,
                     "reasons": reasons, "priority": priority})

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
                i["ticker"], i["name"], reasons, 1 if verdict == "SELL" else 2)

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
            3)

    for i in live:
        verdict = "REDUCE" if i.get("verdict") == "TRIM" else i.get("verdict")
        if verdict == "BUY" and (i.get("mos") or 0) >= 0.25 and (i["value"] / total if total else 0) < 0.05:
            rec("TOP-UP CANDIDATE", i["ticker"], i["name"],
                [f"held at only {(i['value']/total)*100:.1f}% weight while the model"
                 f" still sees MoS {i['mos']*100:+.0f}%"], 3)

    recs.sort(key=lambda r: r["priority"])
    return {
        "sectors": sectors, "concentration": conc, "term": term,
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
    return analysis
