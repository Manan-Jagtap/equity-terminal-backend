"""
scripts/restore_backup.py — restore an encrypted R2 backup into DATABASE_URL.

This is both disaster recovery AND the AWS-migration cutover tool: point
DATABASE_URL at the NEW database (e.g. RDS Mumbai), supply the backup date and
the BACKUP_KEY passphrase, and the full dataset lands there.

Usage (from the backend repo root):
    DATABASE_URL=postgresql://...target... BACKUP_KEY=... \
        venv313/bin/python scripts/restore_backup.py 2026-07-20 [--keep-existing]

  · date          backups/<date>/ prefix in R2 (see the scheduler backup logs)
  · --keep-existing   skip the pre-restore delete (default truncates each table)

Requires R2_* env vars for the source bucket. Prints per-table row counts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    truncate = "--keep-existing" not in sys.argv
    if not args:
        print(__doc__)
        sys.exit(2)
    date = args[0]

    from app.backup import _fernet, restore_tables, PREFIX
    from app.r2.client import list_objects, download_bytes

    f = _fernet()
    if f is None:
        print("BACKUP_KEY is not set — cannot decrypt.")
        sys.exit(2)

    objs = [o["Key"] for o in list_objects(f"{PREFIX}{date}/", max_keys=1000)
            if o.get("Key", "").endswith(".jsonl.gz.enc")]
    if not objs:
        print(f"No backup objects found under {PREFIX}{date}/ — check the date and R2_* env.")
        sys.exit(1)

    print(f"Restoring {len(objs)} tables from {date} "
          f"({'TRUNCATE then insert' if truncate else 'append'}) into "
          f"{os.getenv('DATABASE_URL', 'sqlite default')}")
    dumps = {}
    for key in objs:
        name = key.split("/")[-1].replace(".jsonl.gz.enc", "")
        blob = download_bytes(key)
        if blob is None:
            print(f"  ! could not download {key}"); sys.exit(1)
        dumps[name] = f.decrypt(blob)
    counts = restore_tables(dumps, truncate=truncate)
    for name, n in sorted(counts.items()):
        print(f"  {name:28} {n:>9} rows")
    print("Restore complete.")


if __name__ == "__main__":
    main()
