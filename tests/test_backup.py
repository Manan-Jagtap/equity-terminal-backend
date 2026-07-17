"""Encrypted-backup core: dump → encrypt → decrypt → restore round-trip,
FK-order safety, JSON/date fidelity, and the no-key skip."""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_backup.db"
os.environ["BACKUP_KEY"] = "test-passphrase"

import pytest
from app.database import Base, engine, SessionLocal
from app import models
from app.backup import dump_tables, restore_tables, _fernet, run_backup


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _seed(db):
    # Distinctive values throughout: the pytest suite shares one engine across
    # modules, so lookups must never collide with other tests' rows.
    u = models.User(email="backup-roundtrip@test.local", name="A", password_hash="x")
    db.add(u); db.flush()
    co = models.Company(ticker="BKPTEST", name="Backup Test Co", sector="IT",
                        type="nonfinancial", shares_outstanding=362.0)
    db.add(co); db.flush()
    db.add(models.PortfolioHolding(user_key=f"u{u.id}", company_id=co.id,
                                   qty=10.5, avg_cost=3000.0,
                                   buy_date=dt.date(2025, 1, 15)))
    db.add(models.KVStore(key="bkp_test_k1", value={"nested": {"a": 1}, "list": [1, 2]}))
    db.commit()


def test_dump_restore_round_trip(db):
    # The suite shares one engine (first importer wins), so other test modules'
    # rows may coexist — assert RELATIVE to the pre-dump state, not absolutes.
    _seed(db)
    n_users = db.query(models.User).count()
    n_cos = db.query(models.Company).count()
    dumps = dump_tables()
    assert {"users", "companies", "portfolio_holdings", "kv_store"} <= set(dumps)

    # Wipe and restore from the dump.
    counts = restore_tables(dumps, truncate=True)
    assert counts["users"] == n_users and counts["companies"] == n_cos
    s = SessionLocal()
    try:
        h = s.query(models.PortfolioHolding).filter_by(qty=10.5).first()
        assert h is not None and str(h.buy_date) == "2025-01-15"  # date fidelity
        kv = s.query(models.KVStore).filter_by(key="bkp_test_k1").one()
        assert kv.value == {"nested": {"a": 1}, "list": [1, 2]}   # JSON fidelity
        assert s.query(models.User).filter_by(email="backup-roundtrip@test.local").count() == 1
    finally:
        s.close()


def test_encrypt_decrypt_round_trip(db):
    _seed(db)
    f = _fernet()
    dumps = dump_tables()
    blob = dumps["users"]
    enc = f.encrypt(blob)
    assert enc != blob and b"a@b.c" not in enc          # ciphertext leaks nothing
    assert f.decrypt(enc) == blob


def test_wrong_key_cannot_decrypt(db):
    _seed(db)
    enc = _fernet().encrypt(dump_tables()["users"])
    os.environ["BACKUP_KEY"] = "different-passphrase"
    try:
        from cryptography.fernet import InvalidToken
        with pytest.raises(InvalidToken):
            _fernet().decrypt(enc)
    finally:
        os.environ["BACKUP_KEY"] = "test-passphrase"


def test_run_backup_skips_without_key(db, monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", "")
    out = run_backup()
    assert out["status"] == "skipped"
