"""
scripts/backfill_nbfc_docs.py — Resilient NBFC document backfill (B3.2).

Resilience improvements over the original:
  - Each job gets its OWN short-lived DB session. A failure in one job can't
    poison subsequent jobs (fixes the PendingRollbackError cascade).
  - Network/DB errors are caught per-job; the session is rolled back and
    rebuilt, then the loop continues.
  - --start-from TICKER resumes mid-list (skip companies already done).
  - --max-consecutive-errors N aborts if the run is clearly broken (e.g.
    laptop lost network for good), instead of grinding for hours.
  - Idempotent: docs already in R2 are skipped, so resuming is cheap.

RECOMMENDED — run detached on Railway (stable network, same region as DB):
    railway run python -m scripts.backfill_nbfc_docs

FLAGS:
    --dry-run                 List matrix, fetch nothing
    --limit N                 First N companies only
    --start-from TICKER       Resume from this ticker (inclusive), alphabetical
    --quarters Q3FY26,...     Override quarters (default Q1-Q4 FY26)
    --tickers MUTHOOTFIN,..   Explicit list instead of NBFC filter
    --sleep 2.0               Pause between fetches
    --force                   Re-download even if already in R2
    --max-consecutive-errors  Abort after this many back-to-back failures (default 15)
"""
from __future__ import annotations
import argparse
import sys
import time

from app.database import SessionLocal
from app import models
from app.bse.fetcher import fetch_quarterly_documents
from app.bse.scrip_codes import get_scrip_code

DEFAULT_QUARTERS = ["Q1FY26", "Q2FY26", "Q3FY26", "Q4FY26"]


def latest_reported_quarter(today=None):
    """Most recently reported Indian-FY quarter, allowing for filing lag."""
    from datetime import date
    if today is None:
        today = date.today()
    y, m = today.year, today.month
    if m in (5, 6, 7):
        q, fy = 4, y
    elif m in (8, 9, 10):
        q, fy = 1, y + 1
    elif m in (11, 12):
        q, fy = 2, y + 1
    elif m in (1, 2):
        q, fy = 2, y
    else:  # 3, 4
        q, fy = 3, y
    return f"Q{q}FY{str(fy)[-2:]}"


def resolve_companies(tickers, limit, start_from):
    """Return [(ticker, name)] using a throwaway session."""
    db = SessionLocal()
    try:
        if tickers:
            q = (db.query(models.Company)
                   .filter(models.Company.ticker.in_([t.upper() for t in tickers]))
                   .order_by(models.Company.ticker))
        else:
            q = (db.query(models.Company)
                   .filter(models.Company.template_code == "NBFC")
                   .order_by(models.Company.ticker))
        rows = [(c.ticker, c.name) for c in q.all()]
    finally:
        db.close()

    if start_from:
        sf = start_from.upper()
        rows = [r for r in rows if r[0] >= sf]
    if limit:
        rows = rows[:limit]
    return rows


def run_one_job(ticker, quarter, force):
    """
    Run a single fetch with its own session. Returns (stored_list, errors_list).
    Any exception is caught here; the session is always closed.
    """
    db = SessionLocal()
    try:
        result = fetch_quarterly_documents(db, ticker, quarter, force=force)
        return list(result.documents_stored.keys()), list(result.errors)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return [], [f"{type(e).__name__}: {str(e)[:120]}"]
    finally:
        try:
            db.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start-from", type=str, default=None)
    ap.add_argument("--quarters", type=str, default=None)
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--sleep", type=float, default=2.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--latest", action="store_true",
                    help="Fetch only the latest reported quarter (default for one-pager)")
    ap.add_argument("--max-consecutive-errors", type=int, default=15)
    args = ap.parse_args()

    if args.latest:
        quarters = [latest_reported_quarter()]
    elif args.quarters:
        quarters = [q.strip().upper() for q in args.quarters.split(",")]
    else:
        quarters = DEFAULT_QUARTERS
    tickers = args.tickers.split(",") if args.tickers else None

    companies = resolve_companies(tickers, args.limit, args.start_from)
    if not companies:
        print("✗ No companies matched.")
        sys.exit(1)

    total_jobs = len(companies) * len(quarters)
    print("NBFC document backfill (resilient)")
    print(f"  companies: {len(companies)}")
    print(f"  quarters:  {', '.join(quarters)}")
    print(f"  jobs:      {total_jobs}")
    if args.start_from:
        print(f"  start-from: {args.start_from.upper()}")
    print(f"  mode:      {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    missing = [t for t, _ in companies if not get_scrip_code(t)]
    if missing:
        print(f"⚠ {len(missing)} companies have no scrip code (skipped): {', '.join(missing)}")
        print()

    if args.dry_run:
        for ticker, name in companies:
            scrip = get_scrip_code(ticker) or "NO-SCRIP"
            print(f"  {ticker:14} [{scrip:>8}] {name[:40]}  × {len(quarters)}q")
        print("\n--dry-run set, nothing fetched.")
        return

    audit = {}
    all_errors = []
    job_n = 0
    consecutive_errors = 0
    t_start = time.time()

    for ticker, name in companies:
        audit[ticker] = {"IP": False, "TRANSCRIPT": False, "errors": 0}
        for q in quarters:
            job_n += 1
            prefix = f"[{job_n:>3}/{total_jobs}] {ticker:14} {q}"

            stored, errors = run_one_job(ticker, q, args.force)

            for dt in stored:
                if dt in audit[ticker]:
                    audit[ticker][dt] = True
            for err in errors:
                all_errors.append(f"{ticker} {q}: {err}")
                audit[ticker]["errors"] += 1

            if stored:
                consecutive_errors = 0
                tag = "✓ " + "+".join(stored)
                if errors:
                    tag += f"  ✗ {len(errors)}"
            elif errors:
                consecutive_errors += 1
                tag = f"✗ {errors[0][:60]}"
            else:
                consecutive_errors = 0
                tag = "· nothing"

            print(f"{prefix}  {tag}")

            if consecutive_errors >= args.max_consecutive_errors:
                print(f"\n⚠ ABORTING: {consecutive_errors} consecutive failures "
                      f"(network likely down). Resume later with:")
                print(f"    railway run python -m scripts.backfill_nbfc_docs "
                      f"--start-from {ticker}")
                _summary(companies, audit, all_errors, t_start, aborted=True)
                return

            time.sleep(args.sleep)

    _summary(companies, audit, all_errors, t_start)


def _summary(companies, audit, all_errors, t_start, aborted=False):
    elapsed = time.time() - t_start
    print("\n" + "=" * 70)
    print("BACKFILL SUMMARY" + ("  (ABORTED EARLY)" if aborted else ""))
    print("=" * 70)
    done = [t for t in audit]
    ip_ok = sum(1 for t in done if audit[t]["IP"])
    tr_ok = sum(1 for t in done if audit[t]["TRANSCRIPT"])
    print(f"  processed:         {len(done)}/{len(companies)}")
    print(f"  IP stored:         {ip_ok}")
    print(f"  TRANSCRIPT stored: {tr_ok}")
    print(f"  total errors:      {len(all_errors)}")
    print(f"  elapsed:           {elapsed/60:.1f} min")
    print()
    print(f"{'TICKER':14} {'IP':>4} {'TR':>4}  notes")
    print("-" * 50)
    for ticker, _ in companies:
        if ticker not in audit:
            continue
        a = audit[ticker]
        ip = "✓" if a["IP"] else "—"
        tr = "✓" if a["TRANSCRIPT"] else "—"
        note = f"{a['errors']} err" if a["errors"] else ""
        print(f"{ticker:14} {ip:>4} {tr:>4}  {note}")
    if all_errors:
        print(f"\nERRORS (first 30 of {len(all_errors)}):")
        for e in all_errors[:30]:
            print(f"  • {e}")


if __name__ == "__main__":
    main()
