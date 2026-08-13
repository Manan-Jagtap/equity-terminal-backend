"""Rendering a watchlist must not cost two queries per watched name.

`_enrich` fetched a Valuation and the last two HistoricalPrice rows for EACH
item, so a 40-name watchlist opened ~80 round-trips to draw one screen. Both are
keyed by company_id, so both collapse into one IN-query each.

These tests pin the two things that could go wrong in that collapse: the query
count, and the day-move arithmetic (which must stay latest/previous - 1 from the
two most recent rows, not the two oldest).
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import models
from app.watchlist_routes import _prefetch, _day_move


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def seeded(db):
    ids = []
    for i in range(5):
        co = models.Company(ticker=f"T{i}", name=f"Co {i}", type="nonfinancial",
                            sector="Software & Programming", shares_outstanding=10.0)
        db.add(co); db.commit(); ids.append(co.id)
        db.add(models.Valuation(company_id=co.id, gate_state="clean",
                                intrinsic=100.0 + i, mos=0.1, verdict="HOLD"))
        # three closes; only the newest two may be used
        for d, close in ((dt.date(2026, 8, 1), 90.0),
                         (dt.date(2026, 8, 11), 100.0),
                         (dt.date(2026, 8, 12), 110.0)):
            db.add(models.HistoricalPrice(company_id=co.id, date=d, close=close))
    db.commit()
    return db, ids


def _count_queries(session, fn):
    n = {"q": 0}
    def before(*a, **k): n["q"] += 1
    event.listen(session.get_bind(), "before_cursor_execute", before)
    try:
        fn()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", before)
    return n["q"]


def test_prefetch_is_constant_not_per_name(seeded):
    db, ids = seeded
    n = _count_queries(db, lambda: _prefetch(db, ids))
    assert n <= 2, f"expected 2 queries for the whole list, got {n}"


def test_prefetch_scales_flat(seeded):
    """The whole point: 5 names must cost the same as 1."""
    db, ids = seeded
    one = _count_queries(db, lambda: _prefetch(db, ids[:1]))
    five = _count_queries(db, lambda: _prefetch(db, ids))
    assert one == five, f"1 name={one} queries, 5 names={five} — still per-name"


def test_day_move_matches_the_original_per_row_helper(seeded):
    """Same arithmetic, same rows: latest / previous - 1, newest two only."""
    db, ids = seeded
    _, move_by = _prefetch(db, ids)
    for cid in ids:
        assert move_by[cid] == pytest.approx(_day_move(db, cid))
        assert move_by[cid] == pytest.approx(110.0 / 100.0 - 1.0)   # NOT 110/90


def test_valuations_are_keyed_correctly(seeded):
    db, ids = seeded
    val_by, _ = _prefetch(db, ids)
    assert set(val_by) == set(ids)
    for i, cid in enumerate(ids):
        assert val_by[cid].intrinsic == 100.0 + i


def test_empty_watchlist_costs_nothing(db):
    assert _count_queries(db, lambda: _prefetch(db, [])) == 0


def test_THE_ROUTE_cost_is_FLAT_in_list_size(db):
    """The tests above call _prefetch directly, so they pass whether or not the
    ROUTE uses it — no test at all.

    This one goes through list_watchlist and asserts the property that actually
    matters: the query count does NOT grow with the number of watched names. An
    absolute budget would be a magic number to tune; flatness is the fix.

    Measured before: 1 name = 17 queries, 20 names = 65 (~3 per name — the
    Valuation and price lookups, plus `item.company` and `co.market` lazy-loading
    one query each). After: 6 either way.
    """
    from app.watchlist_routes import list_watchlist

    def build(n):
        u = models.User(email=f"w{n}@x.com", name="W", password_hash="x")
        db.add(u); db.commit()
        for i in range(n):
            co = models.Company(ticker=f"F{n}_{i}", name=f"Co{i}", type="nonfinancial",
                                sector="Software & Programming", shares_outstanding=10.0)
            db.add(co); db.commit()
            db.add(models.Valuation(company_id=co.id, gate_state="clean",
                                    intrinsic=100.0, mos=0.1, verdict="HOLD"))
            for d, c in ((dt.date(2026, 8, 11), 100.0), (dt.date(2026, 8, 12), 110.0)):
                db.add(models.HistoricalPrice(company_id=co.id, date=d, close=c))
            db.add(models.WatchlistItem(user_key=f"u{u.id}", company_id=co.id))
        db.commit()
        return u

    few, many = build(3), build(18)
    n_few = _count_queries(db, lambda: list_watchlist(user=few, db=db))
    n_many = _count_queries(db, lambda: list_watchlist(user=many, db=db))
    assert n_many <= n_few, (
        f"3 names={n_few} queries, 18 names={n_many} — cost still grows per name")
