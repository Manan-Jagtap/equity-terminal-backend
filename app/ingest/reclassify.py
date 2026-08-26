"""
reclassify.py — recompute each company's template_code (and type) using the
name-aware classifier, fixing banks that were mis-routed to NBFC because their
sector string is the generic 'Financial Services'.

Idempotent and safe: only updates template_code / type; never touches financials.

Every run also prints a template DISTRIBUTION and, separately, the names that
landed on the MANUFACTURING default without an industrial-looking sector string.
That fall-through is the shape of the known ~160/1001 gap caused by a null
vendor sector — a DATA backfill need, not a rules bug — and until now it was
invisible: this script printed only the rows it changed, so a name that has been
silently wrong since the first ingest never appears at all.

Run:
  python -m app.ingest.reclassify             # report + apply
  python -m app.ingest.reclassify --dry       # report only, no writes

(Deployment note: this used to say `railway run`. Railway was retired 18 Jul
2026; the backend runs on AWS, so execute it inside the container —
`docker exec -w /srv -e PYTHONPATH=/srv web python -m app.ingest.reclassify`.)
"""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.database import SessionLocal, engine, Base
from app import models
from app.templates import classify_company, FINANCIAL_TEMPLATES

Base.metadata.create_all(bind=engine)


# A sector string that plausibly belongs in MANUFACTURING. Anything landing on
# that template WITHOUT one of these is likelier to have fallen through from a
# null/unknown vendor sector than to be genuinely industrial.
_INDUSTRIAL_HINTS = (
    "auto", "metal", "chemical", "cement", "telecom", "real estate",
    "industrial", "machinery", "construction", "infrastructure", "mining",
    "steel", "paper", "textile", "logistic", "transport", "aviation", "rubber",
    "plastic", "diversified", "conglomerate", "fertili", "packaging",
    "shipping", "defence", "defense", "aerospace", "manufactur", "energy",
    "power", "oil", "gas", "electric", "engineering", "glass", "tyre", "tire",
)


def _looks_industrial(sector: str | None) -> bool:
    s = (sector or "").lower()
    return any(k in s for k in _INDUSTRIAL_HINTS)


def run(dry=False):
    db = SessionLocal()
    changed = 0
    counts: Counter = Counter()
    fell_through: list[tuple[str, str]] = []
    try:
        for co in db.query(models.Company).all():
            new_tmpl = classify_company(co.name, co.sector)
            counts[new_tmpl] += 1
            if new_tmpl == "MANUFACTURING" and not _looks_industrial(co.sector):
                fell_through.append((co.ticker, co.sector or "<null>"))
            new_type = "financial" if new_tmpl in FINANCIAL_TEMPLATES else "nonfinancial"
            if new_tmpl != co.template_code or new_type != co.type:
                print(f"  {co.ticker:<12} {co.template_code or '—':<14} → {new_tmpl:<14} "
                      f"({co.type} → {new_type})   [{co.sector}]")
                if not dry:
                    co.template_code = new_tmpl
                    co.type = new_type
                changed += 1
        if not dry:
            db.commit()
        print(f"\n{'(dry run) ' if dry else ''}{changed} companies reclassified.")

        # Report-only from here. Deliberately printed on EVERY run, changes or
        # not: the point is the standing state of the universe, not this run's
        # delta.
        print("\nTemplate distribution:")
        for tmpl, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {tmpl:<16} {n:>4}  {'#' * min(n, 40)}")

        if fell_through:
            total = sum(counts.values()) or 1
            pct = len(fell_through) * 100.0 / total
            print(f"\n{len(fell_through)} of {total} ({pct:.1f}%) landed on the "
                  f"MANUFACTURING default without an industrial-looking sector.")
            print("These are a DATA gap (missing vendor sector), not a rules bug — "
                  "fix by backfilling the sector, not by adding a template rule:")
            for ticker, sector in fell_through[:40]:
                print(f"    {ticker:<14} sector={sector!r}")
            if len(fell_through) > 40:
                print(f"    … and {len(fell_through) - 40} more")
    finally:
        db.close()


if __name__ == "__main__":
    run(dry="--dry" in sys.argv[1:])
