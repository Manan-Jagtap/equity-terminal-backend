"""Full-pipeline replay core: reconstruct one name's DB rows from the captured
fixture and run the REAL build_company -> effective_assumptions -> recommend with
beta/risk_free/segment_sotp stubbed. Used by tests/calibration_check.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_calib_replay.db")
import json
from app.database import Base, engine, SessionLocal
from app import models

_LOADED = False
_BETA = {}    # ticker -> frozen calc beta

def load_fixture(path=None):
    """The COMMITTED .gz is authoritative; the raw 3MB json is an opt-in override.

    This order is INVERTED from the original. Raw-json-wins was deliberate (it
    let a fresh capture take effect without re-gzipping), but the failure mode
    is silent: the raw file is gitignored, so a STALE local copy shadows the
    committed artefact and local scores diverge from CI with no warning.

    Found 2026-07-28 — a 21 July raw fixture (515 names) was shadowing a .gz
    that had been updated to 516, and every calibration number quoted that day
    came from a file CI never sees. Same disease as the git-ignored baseline
    fixed earlier the same day, in the same subsystem.

    Pass an explicit path for a fresh capture; the default is now what CI reads.
    """
    import os, gzip
    if path:                                   # explicit override, caller's risk
        return json.load(open(path))
    gz = "tests/calibration_inputs.json.gz"
    if os.path.exists(gz):
        with gzip.open(gz, "rt") as fh:
            return json.load(fh)
    raw = "tests/calibration_inputs.json"
    if os.path.exists(raw):
        return json.load(open(raw))
    raise FileNotFoundError(f"{gz} — re-capture via the public API "
                            f"(see calibration-harness notes)")

def build_db(fixture):
    """One throwaway SQLite holding every captured name (isolated by company_id)."""
    global _LOADED, _BETA
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    s = SessionLocal()
    for tk, e in fixture.items():
        c = e["company"]
        co = models.Company(ticker=c["ticker"], name=c.get("name") or tk,
                            type=c.get("type") or "nonfinancial", sector=c.get("sector") or "X",
                            template_code=c.get("template_code"),
                            shares_outstanding=c.get("shares") or 1.0,
                            bse_scrip_code=c.get("bse_scrip_code"))
        s.add(co); s.flush()
        for yr, blocks in (c.get("statements") or {}).items():
            for st, items in (blocks or {}).items():
                for li, v in (items or {}).items():
                    if v is not None:
                        s.add(models.HistoricalFinancial(company_id=co.id, fiscal_year=int(yr),
                              statement_type=st, line_item=li, value=v))
        ly = max((int(y) for y in (c.get("statements") or {})), default=2025)
        for concept, key in (("NET_WORTH","equity"),("NET_PROFIT","net_profit"),
                             ("REVENUE","revenue"),("NET_DEBT","net_debt"),("AUM","aum"),
                             ("GNPA","gnpa"),("NNPA","nnpa"),("CRAR","crar"),("NIM","nim"),("ROA","roa")):
            v = c.get(key)
            if v is not None:
                s.add(models.FinancialFact(company_id=co.id, fiscal_year=ly, concept=concept, value=v))
        if c.get("price"):
            s.add(models.MarketSnapshot(company_id=co.id, price=c["price"]))
        _BETA[c["ticker"].upper()] = (e.get("assumptions") or {}).get("beta")
    s.commit(); s.close()
    _install_stubs()

def _install_stubs():
    from app import sector_params as SP, beta as beta_mod, segment_sotp as ss
    SP.refresh_risk_free = lambda *a, **k: SP.RISK_FREE          # no macro read
    beta_mod.beta_for = lambda db, t: ({"beta": _BETA.get((t or "").upper())}
                                       if _BETA.get((t or "").upper()) else None)
    ss.get_segment_sotp = lambda *a, **k: None                    # DB-extracted SOTP unavailable offline

def score_name(tk, e):
    """Run the real pipeline for one name; return the recommendation dict."""
    from app import assemble, engines, sector_params as SP
    s = SessionLocal()
    co = s.query(models.Company).filter_by(ticker=e["company"]["ticker"]).first()
    SP.RISK_FREE = (e.get("assumptions") or {}).get("risk_free") or SP.RISK_FREE
    data = assemble.build_company(s, co)
    a = assemble.effective_assumptions(s, co, data)
    rec = engines.recommend(data, a)
    s.close()
    return rec
