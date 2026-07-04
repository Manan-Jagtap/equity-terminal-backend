"""
tests/test_scenario_share.py — locks the share-token format (sign/verify/tamper).
Run: python tests/test_scenario_share.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.scenario_routes import _share_token, parse_share_token


def test_round_trip():
    for sid in (1, 42, 999_999):
        assert parse_share_token(_share_token(sid)) == sid
    print("  ok round trip")


def test_tampered_token_rejected():
    tok = _share_token(42)
    body, sig = tok.split(".", 1)
    assert parse_share_token(body + "." + "0" * len(sig)) is None      # bad sig
    other_body = _share_token(43).split(".", 1)[0]
    assert parse_share_token(other_body + "." + sig) is None           # swapped id
    print("  ok tamper rejected")


def test_garbage_tokens_rejected():
    for junk in ("", "no-dot", "a.b", "..", "🙂.sig", _share_token(1)[:-4]):
        assert parse_share_token(junk) is None
    print("  ok garbage rejected")


if __name__ == "__main__":
    test_round_trip()
    test_tampered_token_rejected()
    test_garbage_tokens_rejected()
    print("test_scenario_share: all passed")
