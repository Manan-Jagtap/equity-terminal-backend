"""
app/kv.py — ARCH-04: the typed envelope + registry over KVStore.

KVStore grew into an untyped JSON dumping ground shared by 17 modules: no
per-key ownership, no updated_at contract, so shape drift and staleness were
invisible (the FM macro blob in ENG-01 sat frozen for days partly because
nothing recorded when it was last written).

This module is ADDITIVE — it changes no stored value's shape, so every
existing reader keeps working untouched:

  * kv_put(db, key, value, schema_version=1)
        upserts the value row exactly as _kv_put always did, AND stamps a
        sidecar row  kv_meta:<key> = {"schema_version", "updated_at"}.
  * kv_get(db, key)          plain read (value only, unchanged shape).
  * kv_meta(db, key)         the sidecar (None if the key predates the envelope).
  * kv_health(db)            every non-meta key with its registry owner,
                             updated_at and age — staleness is now queryable.

Consumers migrate to kv_put as they are next touched; nothing force-migrates.
manager_engine._kv_put delegates here already, so the hottest blobs (FM
evidence / macro / calibration — the ENG-01 surface) are stamped from day one.

── KEY REGISTRY (owner module → keys/prefixes) ──────────────────────────────
  app/manager_engine.py     fm_evidence_v1, fm_macro_v1
  app/manager_calibration.py fm_calibration_v1
  app/hidden_gems.py        manager:hidden_gems:v2
  app/beta.py               computed_betas_v1
  app/coverage.py           vendor_unresolved_v1
  app/macro_data.py         macro_updates_v1, macro_forecast_overlay
  app/nse_sources.py        insider_trades_v1, pledge_data_v1, bulk_block_deals_v1
  app/regulatory.py         regulatory_feed_v1
  app/segment_sotp.py       segment_financials_v1
  app/data_integrity.py     data_integrity_v1
  scheduler.py              scheduler_heartbeat
  app/portfolio_routes.py   fm_cash_<user_key>            (per-user prefix)
  app/auth_routes.py        pending_signup:<email>        (transient prefix)
  app/kv.py                 kv_meta:<key>                 (this envelope)
Add new keys HERE when introducing them — the registry is the ownership map.
"""
import datetime as dt

_META_PREFIX = "kv_meta:"

# key (or prefix ending in ':'/'_') → owner module. Mirrors the header comment;
# kv_health uses it to label rows so an unowned key stands out immediately.
KV_REGISTRY = {
    "fm_evidence_v1":        "app/manager_engine.py",
    "fm_macro_v1":           "app/manager_engine.py",
    "fm_calibration_v1":     "app/manager_calibration.py",
    "manager:hidden_gems:v2": "app/hidden_gems.py",
    "computed_betas_v1":     "app/beta.py",
    "vendor_unresolved_v1":  "app/coverage.py",
    "macro_updates_v1":      "app/macro_data.py",
    "macro_forecast_overlay": "app/macro_data.py",
    "insider_trades_v1":     "app/nse_sources.py",
    "pledge_data_v1":        "app/nse_sources.py",
    "bulk_block_deals_v1":   "app/nse_sources.py",
    "regulatory_feed_v1":    "app/regulatory.py",
    "segment_financials_v1": "app/segment_sotp.py",
    "data_integrity_v1":     "app/data_integrity.py",
    "scheduler_heartbeat":   "scheduler.py",
    "fm_cash_":              "app/portfolio_routes.py",   # per-user prefix
    "pending_signup:":       "app/auth_routes.py",        # transient prefix
    "kv_meta:":              "app/kv.py",                 # this envelope
}


def owner_of(key: str) -> str | None:
    """Registry owner for a key — exact match first, then prefix match."""
    if key in KV_REGISTRY:
        return KV_REGISTRY[key]
    for prefix, owner in KV_REGISTRY.items():
        if prefix.endswith((":", "_")) and key.startswith(prefix):
            return owner
    return None


def _upsert(db, key: str, value):
    # DATA-11: a caller that mutated the LOADED dict in place and handed it
    # back would defeat SQLAlchemy's change detection (old == new → UPDATE
    # silently skipped — the write_overlay lost-update bug). flag_modified
    # forces the write regardless of equality.
    from sqlalchemy.orm.attributes import flag_modified
    from app import models
    row = db.query(models.KVStore).filter_by(key=key).first()
    if row:
        row.value = value
        flag_modified(row, "value")
    else:
        db.add(models.KVStore(key=key, value=value))


def kv_put(db, key: str, value, schema_version: int = 1) -> None:
    """Upsert value (shape untouched) + stamp the kv_meta sidecar. Commits."""
    _upsert(db, key, value)
    _upsert(db, _META_PREFIX + key, {
        "schema_version": schema_version,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    db.commit()


def kv_get(db, key: str):
    from app import models
    row = db.query(models.KVStore).filter_by(key=key).first()
    return row.value if row else None


def kv_meta(db, key: str) -> dict | None:
    return kv_get(db, _META_PREFIX + key)


def kv_health(db) -> list[dict]:
    """Every non-meta key with owner, schema_version, updated_at and age_hours
    (the latter None for keys written before the envelope existed)."""
    from app import models
    rows = db.query(models.KVStore.key).all()
    keys = sorted(k for (k,) in rows if not k.startswith(_META_PREFIX))
    metas = {k[len(_META_PREFIX):]: v for k, v in (
        (r.key, r.value) for r in
        db.query(models.KVStore)
          .filter(models.KVStore.key.like(_META_PREFIX + "%")).all())}
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for k in keys:
        meta = metas.get(k) or {}
        age_h = None
        if meta.get("updated_at"):
            try:
                ts = dt.datetime.fromisoformat(meta["updated_at"])
                age_h = round((now - ts).total_seconds() / 3600, 1)
            except Exception:
                pass    # unparseable stamp → surfaces as age unknown
        out.append({
            "key": k,
            "owner": owner_of(k),
            "schema_version": meta.get("schema_version"),
            "updated_at": meta.get("updated_at"),
            "age_hours": age_h,
        })
    return out
