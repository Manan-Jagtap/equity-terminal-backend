"""Batch 36 — REIT distribution-yield model + DBnomics cross-check leg +
vendor quota in /api/admin/usage."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.alt_models import reit_value, is_reit, alternative_intrinsic
from app.alt_models import _REIT_REQ_YIELD, _REIT_DIST_GROWTH


def _reit_co(name="Embassy Office Parks REIT", units=94.78, div_years=(2100, 2250, 2300)):
    stmts = {}
    for i, d in enumerate(div_years):
        stmts[2024 + i] = {"PL": {"revenue": 3800.0, "pat": 1000.0},
                           "BS": {"net_worth": 25000.0},
                           "CF": {"dividends": -float(d)}}   # CF outflow sign
    return {"ticker": "EMBASSY", "name": name, "shares": units,
            "statements": stmts, "type": "nonfinancial", "sector": "Real Estate Operations"}


def test_reit_values_on_distribution_yield():
    co = _reit_co()
    out = reit_value(co, {})
    assert out is not None and out["method"] == "Distribution yield (REIT)"
    dpu = 2250.0 / 94.78                       # median of the 3 years
    expect = dpu * (1 + _REIT_DIST_GROWTH) / (_REIT_REQ_YIELD - _REIT_DIST_GROWTH)
    assert abs(out["intrinsic"] - expect) < 1e-6
    # EMBASSY-scale sanity: DPU ~23.7 → intrinsic in the low-mid 400s
    assert 350 < out["intrinsic"] < 550


def test_fresh_listing_without_payout_record_abstains():
    co = _reit_co(name="Knowledge Realty Trust", div_years=(2300,))   # 1 year only
    assert reit_value(co, {}) is None          # KRT-class: honest abstention


def test_developer_never_matches():
    co = _reit_co(name="Anant Raj Ltd.")
    assert not is_reit(co)
    assert reit_value(co, {}) is None          # developers keep the normal engine


def test_alternative_intrinsic_routes_reits():
    out = alternative_intrinsic(_reit_co(), {"_valuation_sector": "REALTY"})
    assert out is not None and "REIT" in out["method"]


def test_dbnomics_fetch_mocked(monkeypatch):
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    from app import macro_sources as MS

    class _R:
        def raise_for_status(self): pass
        def json(self):
            return {"series": {"docs": [{"period": ["2025-06", "2025-07"],
                                         "value": [194.2, 196.0]}]}}
    monkeypatch.setattr(MS.requests, "get", lambda *a, **k: _R())
    db = SessionLocal()
    n = MS.fetch_dbnomics(db)
    assert n == 2
    from app import macro_data
    pts = dict(macro_data.series(db, MS.DBN_IMF_CPI))
    assert pts.get("2025-07-31") == 196.0      # month-END, own slug
    db.close()


def test_dbnomics_skips_NA_observations(monkeypatch):
    """DBnomics encodes missing observations as the string 'NA' (live IMF
    India-CPI series carried one). float('NA') used to abort the whole leg,
    leaving dbn_imf_cpi_2010_100 empty — each bad point must be skipped, not
    fatal, and the good points still land."""
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    from app import macro_sources as MS

    # Collision-proof dates: the overlay DB persists across the test session
    # and write_overlay counts only net-new points, so reuse of a real month
    # would spuriously report 0 written. Sentinel 2099 months never collide.
    class _R:
        def raise_for_status(self): pass
        def json(self):
            return {"series": {"docs": [{"period": ["2099-05", "2099-06", "2099-07"],
                                         "value": ["NA", 194.2, 196.0]}]}}
    monkeypatch.setattr(MS.requests, "get", lambda *a, **k: _R())
    db = SessionLocal()
    n = MS.fetch_dbnomics(db)
    assert n == 2                                  # the two numeric points only
    from app import macro_data
    pts = dict(macro_data.series(db, MS.DBN_IMF_CPI))
    assert pts.get("2099-07-31") == 196.0
    assert "2099-05-31" not in pts                 # the 'NA' month dropped
    db.close()


def test_dbnomics_kill_switch(monkeypatch):
    from app import macro_sources as MS
    monkeypatch.setenv("DBNOMICS", "0")
    called = []
    monkeypatch.setattr(MS.requests, "get", lambda *a, **k: called.append(1))
    assert MS.fetch_dbnomics(None) == 0 and not called


def test_iip_summary_prefers_new_base(monkeypatch):
    """The MoSPI new-base IIP (2022-23=100) is fresher than the DBIE-seed
    2011-12 base; the headline iip_yoy and the dashboard card must prefer it,
    with the old base as a fallback if the new one is ever absent."""
    from app import macro_data as MD
    # ≥13 months on each base so the year-ago anchor exists; new base is a month
    # fresher (latest 2026-01-31 vs 2025-12-28).
    new_pts = [[f"2025-{m:02d}-28", 100 + m] for m in range(1, 13)] + [["2026-01-31", 113.0]]
    old_pts = [["2024-12-28", 200.0]] + [[f"2025-{m:02d}-28", 200 + m] for m in range(1, 13)]
    monkeypatch.setattr(MD, "series", lambda db, slug: (
        new_pts if slug == MD.IIP_2022_23 else old_pts if slug == MD.IIP else []))
    out = MD.macro_summary(None)
    assert out["iip_yoy"]["as_of"] == "2026-01-31"      # new-base month, not old
    # fallback: when the new base is empty, the old base still populates the card
    monkeypatch.setattr(MD, "series", lambda db, slug: (old_pts if slug == MD.IIP else []))
    out2 = MD.macro_summary(None)
    assert out2["iip_yoy"]["as_of"] == "2025-12-28"


def test_overlay_update_path_persists_across_sessions():
    """DATA-11 regression: the SECOND overlay write (the UPDATE path) was
    silently lost — in-place mutation of the loaded JSON made old == new, so
    SQLAlchemy skipped the UPDATE while the function still reported points
    written. Every write must survive a fresh session."""
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    from app import macro_data as MD
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    MD.write_overlay(db, {"d11_a": {"name": "a", "freq": "M",
                                    "points": [["2026-01-31", 1.0]]}})
    db.close()
    db = SessionLocal()                                    # UPDATE path
    n = MD.write_overlay(db, {"d11_b": {"name": "b", "freq": "M",
                                        "points": [["2026-02-28", 2.0]]}})
    db.close()
    db = SessionLocal()                                    # fresh read-back
    assert n == 1
    assert dict(MD.series(db, "d11_a")).get("2026-01-31") == 1.0
    assert dict(MD.series(db, "d11_b")).get("2026-02-28") == 2.0   # was LOST
    db.close()


def test_admin_usage_carries_vendor_quota():
    import inspect
    from app import admin_routes
    src = inspect.getsource(admin_routes)
    assert "vendor_quota" in src and "month_budget" in src
