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
# Postgres is unaffected. Encryption-in-transit without CA pinning (the RDS CA
# isn't in the system trust store); tighten to verify-full by shipping the RDS
# bundle if the DB ever holds more than it does today.
if "+pg8000" in DATABASE_URL and (
    ".rds.amazonaws.com" in DATABASE_URL
    or os.getenv("DB_SSL", "").lower() in ("1", "true", "require")
):
    import ssl
    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
    connect_args["ssl_context"] = _ctx
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
