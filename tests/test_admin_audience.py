"""/api/admin/audience — the owner's view of who is actually using the platform.

Built only from tables we own (usage_events, auth_events, users). The counting
rules are the point of these tests: "people" must never quietly mean "browsers",
and a failed login must never inflate a login count.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, admin_routes


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def seeded(db):
    now = dt.datetime.utcnow()
    u1 = models.User(email="a@x.com", name="A", password_hash="x")
    u2 = models.User(email="b@x.com", name="B", password_hash="x")
    db.add_all([u1, u2]); db.commit()
    db.add_all([
        models.UsageEvent(user_id=u1.id, event="view:screener", created_at=now - dt.timedelta(minutes=2)),
        models.UsageEvent(user_id=u2.id, event="view:company",  created_at=now - dt.timedelta(minutes=3)),
        models.UsageEvent(user_id=u1.id, event="view:company",  created_at=now - dt.timedelta(hours=5)),
        models.UsageEvent(user_id=None,  event="view:landing",  created_at=now - dt.timedelta(hours=2)),
        models.UsageEvent(user_id=u2.id, event="view:screener", created_at=now - dt.timedelta(days=9)),
        models.AuthEvent(user_id=u1.id, email="a@x.com", event="login",        created_at=now - dt.timedelta(hours=1)),
        models.AuthEvent(user_id=None,  email="c@x.com", event="login_failed", created_at=now - dt.timedelta(hours=1)),
    ])
    db.commit()
    return db, u1


def test_online_now_is_a_five_minute_window(seeded):
    db, admin = seeded
    out = admin_routes.audience(days=30, user=admin, db=db)
    assert out["people"]["online_now"] == 2


def test_a_person_active_twice_counts_once(seeded):
    """u1 has two events in 24h. DAU is people, not hits."""
    db, admin = seeded
    out = admin_routes.audience(days=30, user=admin, db=db)
    assert out["people"]["active_24h"] == 2      # u1 and u2, not 3 events


def test_anonymous_traffic_is_not_counted_as_people(seeded):
    """The NULL-user_id row is real traffic but is not a person — DPDP erasure
    also nulls user_id, so folding it in would silently inflate the headline."""
    db, admin = seeded
    out = admin_routes.audience(days=30, user=admin, db=db)
    assert out["events"]["anonymous_30d"] == 1
    assert out["people"]["active_30d"] == 2       # unchanged by the anonymous row


def test_failed_logins_do_not_inflate_the_login_count(seeded):
    db, admin = seeded
    out = admin_routes.audience(days=30, user=admin, db=db)
    assert out["logins"]["last_24h"] == 1
    assert out["logins"]["failed_24h"] == 1


def test_series_and_top_events(seeded):
    db, admin = seeded
    out = admin_routes.audience(days=30, user=admin, db=db)
    assert len(out["series"]) >= 2
    counts = [r["count"] for r in out["top_events"]]
    assert counts == sorted(counts, reverse=True)


def test_window_is_clamped(seeded):
    db, admin = seeded
    assert admin_routes.audience(days=99999, user=admin, db=db)["window_days"] == 365
    assert admin_routes.audience(days=0, user=admin, db=db)["window_days"] == 1
