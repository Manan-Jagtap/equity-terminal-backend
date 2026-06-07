"""
compute_valuations.py — precompute each company's INDEPENDENT valuation and
cache it in the `valuations` table, so /api/companies reads instantly instead of
running the full DCF + technicals for every company on each request.

The intrinsic here is the model's own view (DCF/RI from history-derived drivers).
The analyst consensus is stored in the SAME row but as separate columns — it is
shown next to the model, never blended into the intrinsic.

Resilient: a FRESH DB session per company with retry on transient drops.
Pure local computation for the model; the consensus is read from the already-
ingested CompanyInsight rows. No external API, no yfinance.

Run:
  python -m app.ingest.compute_valuations              # local SQLite
  railway run python -m app.ingest.compute_valuations  # against live Postgres
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from app.database import SessionLocal, engine, Base
from app import models, engines
from app.assemble import build_company, effective_assumptions
from app.consensus import analyst_consensus

Base.metadata.create_all(bind=engine)

# Only genuinely transient (network) errors get retried. A ProgrammingError
# (e.g. missing table / bad SQL) is persistent — retrying it just wastes time,
# so we surface its real message instead.
_TRANSIENT = (InterfaceError, OperationalError, ConnectionError, BrokenPipeError)


def _payload(co, data, rec, insight_data):
    v = rec["valuation"]; f = rec["fundamentals"]
    cons = analyst_consensus(insight_data, data.get("price")) or {}
    return dict(
        # rec["intrinsic"] is the BLENDED fair value (intrinsic model + relative
        # cross-checks). Store THAT so the screener shows the blend and its
        # MoS/verdict stay internally consistent. v["intrinsic"] is just the
        # primary model — one input to the blend, not the headline.
        intrinsic=rec.get("intrinsic"), mos=rec.get("mos"), verdict=rec.get("verdict"),
        composite=rec.get("composite"), reliable=1 if rec.get("reliable") else 0,
        confidence=(rec.get("confidence") or {}).get("level"),
        method="Blended", valuation_sector=rec.get("valuation_sector"),
        roe=f.get("roe"), pb=f.get("pb"), pe=f.get("pe"),
        analyst_target=cons.get("target"), analyst_low=cons.get("low"),
        analyst_high=cons.get("high"), analyst_rating=cons.get("rating"),
        analyst_upside=cons.get("upside"),
    )


def _compute_one(company_id, retries=3):
    for attempt in range(1, retries + 1):
        db = SessionLocal()
        try:
            co = db.query(models.Company).get(company_id)
            if co is None:
                return "missing"
            data = build_company(db, co)
            a = effective_assumptions(db, co, data)
            rec = engines.recommend(data, a)
            ins = db.query(models.CompanyInsight).filter_by(company_id=company_id).first()
            payload = _payload(co, data, rec, ins.data if ins else None)
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
                time.sleep(2 * attempt)
                continue
            return f"neterr:{type(e).__name__}"
        except Exception as e:
            try: db.rollback()
            except Exception: pass
            return f"err:{e}"
        finally:
            db.close()
    return "neterr"


def run(nifty50: bool = False):
    """Precompute the blended valuations into the `valuations` cache.

    nifty50=True → compute ONLY the Nifty 50 (fast; the universe we actively
    work on) and UPSERT those rows in place, leaving any other rows untouched.
    The drop+recreate is skipped in this mode so we don't wipe the rest of the
    table — it's only needed for a full rebuild when the schema changed."""
    from .indianapi_ingester import NIFTY_50

    if not nifty50:
        # Full rebuild: drop & recreate so the schema always matches the model.
        # (create_all/checkfirst only CREATES missing tables — it never ALTERs an
        # existing one, which is the "column valuations.X does not exist" failure
        # when the model gains a column after the table was first made.)
        try:
            models.Valuation.__table__.drop(bind=engine, checkfirst=True)
            print("  dropped stale 'valuations' table")
        except Exception as e:
            print(f"  (could not drop 'valuations' — continuing: {type(e).__name__}: {e})")
    # Always make sure the table exists (no-op if it already does).
    try:
        models.Valuation.__table__.create(bind=engine, checkfirst=True)
        print("  ensured 'valuations' table exists with current schema")
    except Exception as e:
        print(f"  ! could not (re)create 'valuations': {type(e).__name__}: {e}")

    db = SessionLocal()
    try:
        # Only companies that actually have market data (skip un-ingested seeds).
        q = db.query(models.Company).join(models.MarketSnapshot)
        rows = q.all()
        if nifty50:
            rows = [c for c in rows if (c.ticker or "").upper() in NIFTY_50]
        ids = [c.id for c in rows]
    finally:
        db.close()

    total = len(ids)
    ok = failed = 0
    scope = "NIFTY 50" if nifty50 else "ALL"
    print(f"Computing blended valuations ({scope}) for {total} companies...")
    t0 = time.time()
    for i, cid in enumerate(ids, 1):
        r = _compute_one(cid)
        if r == "ok":
            ok += 1
        else:
            failed += 1
            print(f"  company_id={cid}: {r}")
        if i % 50 == 0:
            print(f"  ...{i}/{total} ({ok} ok, {failed} failed)")
    print(f"\nDone in {time.time()-t0:.1f}s.  Computed: {ok}  Failed: {failed}")


if __name__ == "__main__":
    run()
