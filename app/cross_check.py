"""
app/cross_check.py — second-source price cross-check (market-data feed vs
IndianAPI). "Dhan" in identifiers/messages below is HISTORICAL — the feed is
Upstox as of 7 Sep 2026, reached through app.market_data like everything else.
Left unrenamed on purpose (see app/market_data.py's own docstring): the point
of that indirection is that call sites never need to know or say the vendor's
name, and this file is the one place that still hardcodes it, in user-facing
copy no less. TODO: read app.market_data.PROVIDER and title-case it instead.

Both vendors already land in the DB (HistoricalPrice ← feed backfill + daily
top-up; MarketSnapshot ← IndianAPI EOD/intraday), so the check costs ZERO API
calls. It catches the two failure modes a single-vendor pipeline is blind to:

  STALE_HISTORY  — the feed's series stopped updating (token died, backfill
                   broke, OR — found 7 Sep 2026 — the ticker resolved to the
                   WRONG instrument; see _dhan_coverage_lookup's docstring and
                   tests/test_upstox_instruments.py)
  STALE_SNAPSHOT — the IndianAPI live price stopped updating (quota, outage)
  DIVERGENT      — the vendors disagree on the close: a stale tick, a missed
                   split/bonus adjustment, or plain bad data — the Bajaj-Finance
                   class of bug, now caught automatically instead of by eye.

check_row() is pure (unit-tested without a DB); assemble_rows() does the reads.
"""
from __future__ import annotations
import datetime as _dt

from . import models

# Calendar-day staleness allowances (generous enough for weekends + holidays).
HIST_STALE_DAYS = 7
SNAP_STALE_DAYS = 4
# Vendors are compared only when both are fresh. A close-vs-live gap beyond WARN
# is suspicious; beyond ALERT it is split-shaped (≈50% for a 1:1 bonus).
DIVERGE_WARN = 0.06
DIVERGE_ALERT = 0.20


def check_row(row: dict, today: _dt.date | None = None, feed_name: str = "Upstox") -> dict:
    """row: {ticker, snapshot_price, snapshot_date (date|None),
             hist_close, hist_date (date|None)} → status + flags.

    `feed_name` names the vendor in user-facing messages ONLY — stays a plain
    parameter (default matches the current app.market_data.PROVIDER) rather
    than importing app.market_data here, so this stays pure and unit-testable
    without an env var. Callers going through cross_check_universe() get the
    live provider name resolved once, not once per row."""
    today = today or _dt.date.today()
    flags: list[dict] = []

    hist_age = (today - row["hist_date"]).days if row.get("hist_date") else None
    snap_age = (today - row["snapshot_date"]).days if row.get("snapshot_date") else None

    # STALE_HISTORY reads as "our pipeline stopped" and sends you looking at the
    # token, the backfill and the entitlement. For a name the feed does not
    # carry at all, that is a false lead: no retry can ever fix it. JBCHEPHARM
    # sat in the alert list saying "Dhan history last updated 12d ago" through a
    # credential rotation, a full backfill and an entitlement check, when the
    # truth was that it is absent from the scrip master entirely. A second,
    # subtler version of the same false lead surfaced 7 Sep 2026: ELECTCAST and
    # MOTHERSON resolved to an INSTRUMENT_KEY, so `covered` read True, but the
    # key was for a same-named warrant/debenture rather than the equity —
    # covered-but-wrong looks identical to a stopped pipeline from here. That is
    # a ticker-resolution bug (fixed in app/upstox/instruments.py, pinned by
    # tests/test_upstox_instruments.py), not a staleness one; this function has
    # no way to tell the two apart and does not need to — both correctly land on
    # STALE_HISTORY, which is the honest description of what is actually true.
    #
    # `covered` is TRI-STATE and None means "don't know" — if the scrip master
    # failed to load we must NOT relabel the whole universe as uncovered, so an
    # unknown falls through to the original staleness wording. Fail open.
    _feed_name = (feed_name or "Upstox").capitalize()
    covered = row.get("covered")
    if covered is False:
        flags.append({"code": "NO_FEED_COVERAGE", "level": "warn",
                      "message": (f"Not in the {_feed_name} scrip master — single-source on IndianAPI; "
                                  "history cannot update and no retry will change that")})
    elif row.get("hist_close") is None:
        flags.append({"code": "NO_HISTORY", "level": "warn",
                      "message": f"No {_feed_name} price history stored — single-source name"})
    elif hist_age is not None and hist_age > HIST_STALE_DAYS:
        flags.append({"code": "STALE_HISTORY", "level": "warn",
                      "message": f"{_feed_name} history last updated {hist_age}d ago"})

    if row.get("snapshot_price") is None:
        flags.append({"code": "NO_SNAPSHOT", "level": "alert",
                      "message": "No live price snapshot — valuation MoS is not meaningful"})
    elif snap_age is not None and snap_age > SNAP_STALE_DAYS:
        flags.append({"code": "STALE_SNAPSHOT", "level": "alert",
                      "message": f"Live price is {snap_age}d old — screener marks are stale"})

    gap = None
    # Only compare prices from the SAME trading day. During market hours the
    # live snapshot is today's price while the feed's history still holds
    # yesterday's close (the EOD backfill catches up after the bell) — comparing
    # those two just measures today's move, not a vendor divergence. Requiring
    # snapshot_date == hist_date removes that whole class of false positives
    # while still catching genuine same-day, split-shaped mismatches.
    same_day = (row.get("snapshot_date") and row.get("hist_date")
                and row["snapshot_date"] == row["hist_date"])
    both_fresh = (row.get("hist_close") and row.get("snapshot_price")
                  and hist_age is not None and hist_age <= HIST_STALE_DAYS
                  and snap_age is not None and snap_age <= SNAP_STALE_DAYS
                  and same_day)
    if both_fresh:
        gap = row["snapshot_price"] / row["hist_close"] - 1.0
        if abs(gap) > DIVERGE_ALERT:
            flags.append({"code": "DIVERGENT", "level": "alert",
                          "message": (f"Vendors disagree {gap*100:+.1f}% — split-shaped; "
                                      "check corporate-action adjustment")})
        elif abs(gap) > DIVERGE_WARN:
            flags.append({"code": "DIVERGENT", "level": "warn",
                          "message": f"{_feed_name} close vs live price gap {gap*100:+.1f}%"})

    status = ("alert" if any(f["level"] == "alert" for f in flags)
              else "warn" if flags else "ok")
    return {"ticker": row.get("ticker"), "status": status, "flags": flags,
            "gap_pct": gap,
            "snapshot_price": row.get("snapshot_price"),
            "hist_close": row.get("hist_close"),
            "hist_date": row["hist_date"].isoformat() if row.get("hist_date") else None,
            "snapshot_date": row["snapshot_date"].isoformat() if row.get("snapshot_date") else None}


def _to_date(v) -> _dt.date | None:
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _feed_coverage_lookup():
    """ticker -> bool, or None when the active feed's scrip master is
    unavailable.

    Returning None (rather than an empty map) is load-bearing: an empty map
    would mark EVERY name uncovered and turn one transient fetch failure into a
    universe-wide relabelling. Callers must fail open on None."""
    try:
        from app.market_data import instruments
        instruments._load()
        eq = instruments._cache.get("eq")
        return set(eq) if eq else None
    except Exception:
        return None


def assemble_rows(db, tickers) -> list[dict]:
    """One row per ticker with the latest close each vendor holds."""
    from sqlalchemy import func
    covered = _feed_coverage_lookup()
    tset = {(t or "").upper() for t in tickers}
    cos = [c for c in db.query(models.Company).all() if (c.ticker or "").upper() in tset]
    snap = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}

    latest = dict(db.query(models.HistoricalPrice.company_id,
                           func.max(models.HistoricalPrice.date))
                    .group_by(models.HistoricalPrice.company_id).all())
    close_by = {}
    if latest:
        for hp in (db.query(models.HistoricalPrice)
                     .filter(models.HistoricalPrice.date.in_(set(latest.values()))).all()):
            if latest.get(hp.company_id) == hp.date:
                close_by[hp.company_id] = hp

    rows = []
    for co in cos:
        m, hp = snap.get(co.id), close_by.get(co.id)
        tk = (co.ticker or "").upper()
        # Mirror instruments.security_id()'s spelling fallbacks so a name is not
        # called "uncovered" merely because of the feed's own '&'/'-' quirks.
        if covered is None:
            is_cov = None
        else:
            is_cov = any(c in covered for c in (tk, tk.replace("-", ""), tk.replace("&", "")))
        rows.append({"ticker": tk,
                     "snapshot_price": m.price if m else None,
                     "snapshot_date": _to_date(m.as_of) if m else None,
                     "hist_close": hp.close if hp else None,
                     "hist_date": _to_date(hp.date) if hp else None,
                     "covered": is_cov})
    return rows


def cross_check_universe(db, tickers, today: _dt.date | None = None) -> dict:
    from app.market_data import PROVIDER
    feed_name = PROVIDER.capitalize()
    checked = [check_row(r, today, feed_name=feed_name) for r in assemble_rows(db, tickers)]
    checked.sort(key=lambda r: ({"alert": 0, "warn": 1, "ok": 2}[r["status"]], r["ticker"] or ""))
    n_alert = sum(1 for r in checked if r["status"] == "alert")
    n_warn = sum(1 for r in checked if r["status"] == "warn")
    return {"as_of": (today or _dt.date.today()).isoformat(),
            "count": len(checked), "alerts": n_alert, "warnings": n_warn,
            "ok": len(checked) - n_alert - n_warn,
            "feed_provider": PROVIDER,
            "flagged": [r for r in checked if r["status"] != "ok"]}
