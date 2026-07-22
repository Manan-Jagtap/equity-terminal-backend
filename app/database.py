"""Database engine + session. SQLite locally (zero config); Postgres in prod via DATABASE_URL."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./terminal.db")

# Railway/Render hand out "postgres://..." but SQLAlchemy needs "postgresql+pg8000://..."
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# RDS requires TLS, but pg8000 only encrypts when handed an ssl_context.
# Auto-enable for *.rds.amazonaws.com (or DB_SSL=require) — Railway's internal
# Postgres is unaffected.
#
# SEC-11: server-certificate VERIFICATION is opt-in via DB_SSL_VERIFY=full plus
# RDS_CA_BUNDLE pointing at the RDS CA PEM (verify-full — encrypted AND the
# server identity checked, closing the theoretical in-VPC MITM). It defaults OFF
# because a wrong bundle/hostname would take the whole app down; enable it after
# a connection rehearsal. Without the flag we keep encryption-in-transit with the
# server cert unverified (the pre-existing behaviour) — never plaintext.
if "+pg8000" in DATABASE_URL and (
    ".rds.amazonaws.com" in DATABASE_URL
    or os.getenv("DB_SSL", "").lower() in ("1", "true", "require")
):
    import ssl
    _ca = os.getenv("RDS_CA_BUNDLE", "")
    if os.getenv("DB_SSL_VERIFY", "").lower() == "full" and _ca and os.path.exists(_ca):
        _ctx = ssl.create_default_context(cafile=_ca)   # verify-full: CERT_REQUIRED + hostname
    else:
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
    connect_args["ssl_context"] = _ctx
# SCALE-01: size the pool for prod concurrency. The defaults (5 + 10 overflow =
# 15 conns, 30s blocking wait) meet a ~40-thread sync worker: >15 concurrent
# DB-touching requests queued 30s then 500'd. 10 + 20 overflow (30 max) vs the
# web+scheduler share of RDS max_connections (~80-100 on db.t4g.micro) is safe;
# pool_timeout 5s fails fast instead of hanging; pool_recycle drops stale conns.
# SQLite (dev/CI) uses a different pool that rejects these kwargs, so guard it.
_pool_kwargs = {} if DATABASE_URL.startswith("sqlite") else dict(
    pool_size=10, max_overflow=20, pool_timeout=5, pool_recycle=1800)
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **_pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
