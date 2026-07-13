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
                          ("regime", "breadth_200dma", "breadth_50dma",
                           "rs_leaders", "rs_laggards", "as_of")} if mac else None}
    except Exception:
        db.rollback()
        return {"engine": "v4-triangulated", "evidence_as_of": None, "names": 0}


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
        from app.manager_engine import load_evidence, load_macro, snapshot_evidence
        evidence, macro = load_evidence(db), load_macro(db)
        if not evidence:
            snapshot_evidence(db)          # first run after deploy: build + store
            evidence, macro = load_evidence(db), load_macro(db)
    except Exception:
        db.rollback()
    analysis["manager"] = manager_report(items, analysis, mom_by, target_by,
                                         intel_by, quote_by, evidence, macro)
    return analysis


# ── Fund Manager report ──────────────────────────────────────────────────────

def manager_report(items: list[dict], analysis: dict, mom_by: dict | None = None,
                   target_by: dict | None = None, intel_by: dict | None = None,
                   quote_by: dict | None = None, evidence: dict | None = None,
                   macro: dict | None = None) -> dict:
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
                                    macro_note, PRIOR_WEIGHTS)
    weights = ev_weights or PRIOR_WEIGHTS

    def _tax_notes(reasons):
        return [x for x in reasons if "LTCG" in x or "long-term" in x.lower()]

    def _score(action_name: str, tk: str, base_reasons: list[str]):
        """v4 conviction from evidence; falls back to a v3-style score for a
        name the nightly evidence build hasn't covered."""
        ev = ev_names.get(tk)
        i = by_ticker.get(tk)
        w_in_book = (i["value"] / total) if (i and total) else None
        if ev:
            if action_name.startswith("REVIEW"):
                cv, rs = conviction_trim(ev, w_in_book, weights, macro)
            else:
                cv, rs = conviction_add(ev, weights, macro)
            return cv, rs + _tax_notes(base_reasons), ev
        # fallback (thin): old MoS-based path, marked as such
        cv = 40
        mos = (i or {}).get("mos")
        if mos is not None:
            cv += min(20, int(abs(mos) * 30))
        return max(10, min(70, cv)), base_reasons + ["evidence pending for this name"], None

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
        a["levels"] = lv
        if ev:
            a["evidence"] = {
                "suspect": (ev.get("tri") or {}).get("suspect"),
                "val_blend": (ev.get("tri") or {}).get("score"),
                "val_sources": (ev.get("tri") or {}).get("used"),
                "quality": (ev.get("quality") or {}).get("composite"),
                "red_flags": (ev.get("quality") or {}).get("red_flags"),
                "pe_pct_5y": (ev.get("band") or {}).get("pe_pct"),
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
                      "priority": 3, "conviction": cv,
                      "levels": lv,
                      "evidence": {
                          "suspect": tri.get("suspect"), "val_blend": tri.get("score"),
                          "val_sources": tri.get("used"),
                          "quality": q.get("composite"), "red_flags": q.get("red_flags"),
                          "pe_pct_5y": (ev.get("band") or {}).get("pe_pct"),
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
    if not lines:
        lines.append("No structural flags: sizing, alignment and terms all read clean.")
    mnote = macro_note(macro or {})
    if mnote:
        lines.insert(0, mnote)
    return {"actions": actions[:10], "note": " ".join(lines), "aum": total,
            "macro": {k: (macro or {}).get(k) for k in
                      ("regime", "breadth_200dma", "breadth_50dma", "nifty",
                       "rs_leaders", "rs_laggards", "commodities", "as_of")} if macro else None,
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
