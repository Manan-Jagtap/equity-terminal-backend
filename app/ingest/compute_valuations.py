"""
compute_valuations.py — precompute each company's valuation and cache it in the
`valuations` table, so /api/companies reads instantly instead of running the
full DCF + technicals for ~500 companies on every request.

Resilient: a FRESH DB session per company (a dropped connection costs one
company, not the whole run) with retry on transient network errors. This is the
same pattern bulk_ingester uses to survive the laptop->Railway proxy dropping.

Pure local computation: no external API, no yfinance. Reuses the exact same
build_company / assumptions_dict / engines.recommend path the live API uses.

Run:
  python -m app.ingest.compute_valuations          # local
  railway run python -m app.ingest.compute_valuations   # against live DB
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy.exc import InterfaceError, OperationalError, DBAPIError
from app.database import SessionLocal, engine, Base
from app import models, engines
from app.assemble import build_company, assumptions_dict

Base.metadata.create_all(bind=engine)

_TRANSIENT = (InterfaceError, OperationalError, DBAPIError, ConnectionError, BrokenPipeError)


def _payload(rec):
    v = rec["valuation"]; f = rec["fundamentals"]
    return dict(
        intrinsic=v.get("intrinsic"), mos=rec.get("mos"),
        roe=f.get("roe"), pb=f.get("pb"), pe=f.get("pe"),
        composite=rec.get("composite"), verdict=rec.get("verdict"),
        market_implied_cap_years=v.get("market_implied_cap_years"),
        method=v.get("method"),
    )


def _compute_one(company_id, retries=3):
    """Fresh session per company; retry transient network drops."""
    for attempt in range(1, retries + 1):
        db = SessionLocal()
        try:
            co = db.query(models.Company).get(company_id)
            if co is None:
                return "missing"
            data = build_company(db, co)
            a = assumptions_dict(co.assumptions)
            rec = engines.recommend(data, a)
            payload = _payload(rec)
            row = db.query(models.Valuation).filter_by(company_id=company_id).first()
            if row:
                for k, val in payload.items():
                    setattr(row, k, val)
            else:
                db.add(models.Valuation(company_id=company_id, **payload))
            db.commit()
            return "ok"
        except _TRANSIENT as e:
            try: db.rollback()
            except Exception: pass
            if attempt < retries:
                time.sleep(2 * attempt)  # backoff, then retry with a new session
                continue
            return f"neterr:{type(e).__name__}"
        except Exception as e:
            try: db.rollback()
            except Exception: pass
            return f"err:{e}"
        finally:
            db.close()
    return "neterr"


def run():
    # collect ids with a short-lived session so we don't hold a connection open
    db = SessionLocal()
    try:
        ids = [c.id for c in db.query(models.Company).all()]
    finally:
        db.close()

    total = len(ids)
    ok = failed = 0
    print(f"Computing valuations for {total} companies (resilient, fresh session each)...")
    t0 = time.time()
    for i, cid in enumerate(ids, 1):
        r = _compute_one(cid)
        if r == "ok":
            ok += 1
        else:
            failed += 1
            print(f"  company_id={cid}: {r}")
        if i % 100 == 0:
            print(f"  ...{i}/{total} done ({ok} ok, {failed} failed)")
    dt = time.time() - t0
    print(f"\nDone in {dt:.1f}s.  Computed: {ok}  Failed: {failed}")


if __name__ == "__main__":
    run()
