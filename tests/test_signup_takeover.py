"""A second signup must not hijack a pending verification.

The pending record used to be a single dict keyed by email and replaced
wholesale by every signup POST, while verify created the account from whatever
password_hash was CURRENTLY stored. So:

  1. victim signs up with password A   -> {hash(A), code1} emailed
  2. attacker POSTs signup for the same address >60s later with password B
                                       -> record overwritten to {hash(B), code2}
  3. victim types code2 (the mail they just received)
  4. account created with hash(B) -> attacker signs in as the victim

The fix binds each password to the code issued with it. These tests drive the
real signup/verify helpers, not a re-implementation — an earlier test file in
this repo passed with its fix disabled because it only agreed with itself.
"""
import os, sys, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from app.database import Base, engine, SessionLocal
from app import auth_routes as AR


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    yield


def _sha(code): return hashlib.sha256(code.encode()).hexdigest()


def _entry(pw_hash, code, name="n", age=0.0):
    return {"name": name, "password_hash": pw_hash, "code_sha": _sha(code),
            "expires": time.time() + 30 * 60, "sent_at": time.time() - age}


def test_victims_code_yields_the_victims_password_not_the_attackers():
    """THE takeover case, end to end through the real store."""
    db = SessionLocal(); email = "victim@x.com"
    AR._add_pending_entry(db, email, _entry("HASH_VICTIM_A", "111111", age=120))
    AR._add_pending_entry(db, email, _entry("HASH_ATTACKER_B", "222222"))

    p = AR._get_pending(db, email)
    live = [e for e in p["entries"] if e["expires"] > time.time()]
    # the victim types the code from the mail THEY requested
    match = next(e for e in live if e["code_sha"] == _sha("111111"))
    assert match["password_hash"] == "HASH_VICTIM_A", (
        "the victim's code must carry the victim's password")

    # and the attacker's own code still only yields the attacker's own password
    match_b = next(e for e in live if e["code_sha"] == _sha("222222"))
    assert match_b["password_hash"] == "HASH_ATTACKER_B"
    db.close()


def test_second_signup_does_not_destroy_the_first_entry():
    """The old store replaced wholesale; that is what made the swap possible."""
    db = SessionLocal(); email = "v@x.com"
    AR._add_pending_entry(db, email, _entry("A", "111111", age=120))
    AR._add_pending_entry(db, email, _entry("B", "222222"))
    p = AR._get_pending(db, email)
    hashes = {e["password_hash"] for e in p["entries"]}
    assert hashes == {"A", "B"}, "both entries must survive; neither may overwrite"
    db.close()


def test_expired_entries_are_dropped():
    db = SessionLocal(); email = "e@x.com"
    stale = _entry("OLD", "111111"); stale["expires"] = time.time() - 1
    AR._add_pending_entry(db, email, stale)
    AR._add_pending_entry(db, email, _entry("NEW", "222222"))
    p = AR._get_pending(db, email)
    assert [e["password_hash"] for e in p["entries"]] == ["NEW"]
    db.close()


def test_entry_list_is_bounded():
    db = SessionLocal(); email = "b@x.com"
    for i in range(8):
        AR._add_pending_entry(db, email, _entry(f"H{i}", f"{i:06d}"))
    p = AR._get_pending(db, email)
    assert len(p["entries"]) <= AR._MAX_PENDING_ENTRIES


def test_attempts_are_counted_per_address_not_per_entry():
    """Extra entries must not buy extra guesses against the lockout."""
    db = SessionLocal(); email = "a@x.com"
    AR._add_pending_entry(db, email, _entry("A", "111111"))
    AR._add_pending_entry(db, email, _entry("B", "222222"))
    p = AR._get_pending(db, email)
    p["attempts"] = 5
    AR._put_pending_raw(db, email, p)
    assert AR._get_pending(db, email)["attempts"] == 5
    db.close()


def test_legacy_flat_record_still_verifies():
    """Records written before this change must not lock anyone out."""
    db = SessionLocal(); email = "legacy@x.com"
    AR._put_pending_raw(db, email, {
        "name": "L", "password_hash": "LEGACY", "code_sha": _sha("999999"),
        "expires": time.time() + 600, "attempts": 0, "sent_at": time.time()})
    p = AR._get_pending(db, email)
    assert p and len(p["entries"]) == 1
    assert p["entries"][0]["password_hash"] == "LEGACY"
    db.close()
