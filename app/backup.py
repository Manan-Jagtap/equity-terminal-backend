"""
app/backup.py — owned, ENCRYPTED database backups to R2.

Why this shape:
  · OWNED — Railway's backups are Railway's; a disaster-recovery copy the owner
    controls must exist outside the platform (also the AWS-migration cutover
    tool: dump here, restore into RDS Mumbai).
  · ENCRYPTED — the owner's DPDP posture forbids personal data at processors
    outside India, and the R2 bucket is not in India. Every byte is
    Fernet-encrypted (AES128-CBC + HMAC) BEFORE upload with a key derived from
    the `BACKUP_KEY` env passphrase, held only by the owner. Ciphertext-only
    off-stack satisfies the posture; without the passphrase the backup is noise.
  · PORTABLE — pure-Python JSONL.gz per table (no pg_dump binary dependency),
    restorable into Postgres OR SQLite via scripts/restore_backup.py.

Fidelity note: values round-trip through JSON (dates/datetimes as ISO strings —
Postgres coerces them back on insert; JSON columns stay native). That is fully
adequate for disaster recovery and migration of THIS schema; it is not a
byte-identical pg_dump.

Scheduler: Sundays 04:00 UTC (after the integrity sweep). Skips with a loud
log when BACKUP_KEY or R2 credentials are absent. Retention: last 8 weeklies.
"""
from __future__ import annotations
import base64
import datetime as _dt
import gzip
import hashlib
import io
import json
import logging
import os

log = logging.getLogger("app.backup")

PREFIX = "backups/"
KEEP = 30   # PERF-03: daily cadence keeps ~a month of history (was 8 weeklies)

# SEC-06: fixed application salt. scrypt's memory-hard work factor (not the salt)
# is what defeats a brute-force of a leaked ciphertext + low-entropy passphrase;
# a fixed salt keeps the format simple (no salt to store alongside the token)
# while still preventing cross-passphrase rainbow precomputation.
_SCRYPT_SALT = b"equityverdict-backup-kdf-v2"


def _fernet():
    """MultiFernet for BACKUP_KEY: ENCRYPTS with a scrypt-derived key (SEC-06,
    replacing the old single bare SHA-256), and DECRYPTS with scrypt OR the
    legacy sha256 key so backups written before this change still restore."""
    passphrase = os.getenv("BACKUP_KEY", "")
    if not passphrase:
        return None
    from cryptography.fernet import Fernet, MultiFernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    dk = Scrypt(salt=_SCRYPT_SALT, length=32, n=2 ** 14, r=8, p=1).derive(passphrase.encode())
    new = Fernet(base64.urlsafe_b64encode(dk))
    legacy = Fernet(base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode()).digest()))
    return MultiFernet([new, legacy])   # encrypt→new; decrypt tries new then legacy


def _row_to_dict(row) -> dict:
    return dict(row._mapping)


def dump_tables() -> dict[str, bytes]:
    """{table_name: gzipped JSONL bytes} for every table, FK-safe order.
    Pure read; usable directly for a local dump or the migration cutover."""
    from sqlalchemy import select
    from app.database import Base, engine
    out: dict[str, bytes] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            buf = io.BytesIO()
            n = 0
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                for row in conn.execute(select(table)):
                    gz.write((json.dumps(_row_to_dict(row), default=str) + "\n").encode())
                    n += 1
            out[table.name] = buf.getvalue()
            log.info(f"backup: dumped {table.name} ({n} rows, {len(out[table.name])//1024} KB gz)")
    return out


def _coercers(table):
    """Per-column string→python coercers for types JSON flattens. SQLite's
    Date/DateTime types refuse ISO strings (Postgres coerces them) — the restore
    must work on both, so parse them back explicitly."""
    import sqlalchemy as sa
    out = {}
    for col in table.columns:
        if isinstance(col.type, sa.DateTime):
            out[col.name] = lambda v: _dt.datetime.fromisoformat(v) if isinstance(v, str) else v
        elif isinstance(col.type, sa.Date):
            out[col.name] = lambda v: _dt.date.fromisoformat(v[:10]) if isinstance(v, str) else v
    return out


def restore_tables(dumps: dict[str, bytes], truncate: bool = True) -> dict[str, int]:
    """Load {table: gzipped JSONL} into DATABASE_URL. Deletes in reverse-FK
    order first (when truncate), inserts in FK order. Returns rows per table."""
    from app.database import Base, engine
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        if truncate:
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in dumps:
                    conn.execute(table.delete())
        for table in Base.metadata.sorted_tables:
            blob = dumps.get(table.name)
            if blob is None:
                continue
            coerce = _coercers(table)
            rows = []
            with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as gz:
                for line in gz:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    for k, fn in coerce.items():
                        if row.get(k) is not None:
                            row[k] = fn(row[k])
                    rows.append(row)
            for i in range(0, len(rows), 1000):
                conn.execute(table.insert(), rows[i:i + 1000])
            counts[table.name] = len(rows)
        # Rows were inserted WITH their original ids, which leaves Postgres
        # sequences behind max(id) — every later INSERT would collide. Resync.
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            for table in Base.metadata.sorted_tables:
                if table.name not in dumps or "id" not in table.c:
                    continue
                seq = conn.execute(
                    text(f"select pg_get_serial_sequence('{table.name}','id')")
                ).scalar()
                if seq:
                    conn.execute(text(
                        f"select setval('{seq}', (select coalesce(max(id),1) from {table.name}))"
                    ))
    return counts


def run_backup() -> dict:
    """Dump → encrypt → upload to R2 under backups/YYYY-MM-DD/ + manifest,
    then prune to the newest KEEP dates. Returns a summary; never raises."""
    f = _fernet()
    if f is None:
        log.warning("backup: BACKUP_KEY not set — skipping (set a strong "
                    "passphrase in the scheduler env to enable encrypted backups)")
        return {"status": "skipped", "reason": "no BACKUP_KEY"}
    try:
        from app.r2.client import upload_bytes, list_objects, delete_object
        date = _dt.date.today().isoformat()
        dumps = dump_tables()
        manifest = {"date": date, "tables": {}, "format": "jsonl.gz.fernet.v1"}
        total = 0
        for name, blob in dumps.items():
            enc = f.encrypt(blob)
            key = f"{PREFIX}{date}/{name}.jsonl.gz.enc"
            upload_bytes(key, enc, content_type="application/octet-stream")
            manifest["tables"][name] = {"bytes_enc": len(enc)}
            total += len(enc)
        upload_bytes(f"{PREFIX}{date}/manifest.json.enc",
                     f.encrypt(json.dumps(manifest).encode()),
                     content_type="application/octet-stream")
        # Retention: keep the newest KEEP date-prefixes.
        dates = sorted({o["key"].split("/")[1] for o in list_objects(PREFIX, max_keys=1000)
                        if o.get("key", "").count("/") >= 2})
        for old in dates[:-KEEP]:
            for o in list_objects(f"{PREFIX}{old}/", max_keys=1000):
                delete_object(o["key"])
            log.info(f"backup: pruned {old}")
        log.info(f"backup: {date} complete — {len(dumps)} tables, {total//1024//1024} MB encrypted")
        return {"status": "ok", "date": date, "tables": len(dumps), "bytes_enc": total}
    except Exception as e:
        log.error(f"backup failed: {e}")
        return {"status": "error", "error": str(e)[:200]}
