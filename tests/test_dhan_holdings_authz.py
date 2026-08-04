"""/api/portfolio/sync-dhan must be ADMIN ONLY.

The endpoint reads the OWNER's brokerage positions using the server's own Dhan
token, so the caller's identity does not scope the data — every caller receives
the same holdings. It was gated on `get_current_user`, so any signed-up account
could POST here and have the owner's real positions (ticker, quantity, average
cost) written into its own portfolio and rendered back.

Found 2026-08-04 by an adversarial audit. This test pins the dependency so the
gate cannot be loosened by accident.
"""
import os, sys, inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import portfolio_routes
from app.admin_routes import require_admin
from app.auth import get_current_user


def _dep_callables(fn):
    out = []
    for p in inspect.signature(fn).parameters.values():
        d = p.default
        if d is not inspect.Parameter.empty and hasattr(d, "dependency"):
            out.append(d.dependency)
    return out


def test_sync_dhan_requires_admin_not_merely_a_logged_in_user():
    deps = _dep_callables(portfolio_routes.sync_dhan_holdings)
    assert require_admin in deps, (
        "sync-dhan must depend on require_admin — it returns the OWNER's real "
        "brokerage holdings regardless of who calls it")
    assert get_current_user not in deps, (
        "get_current_user admits ANY signed-up account; that is the defect")


def test_no_other_portfolio_route_leaks_the_owner_feed():
    """Any route touching the owner's Dhan holdings feed must be admin-gated."""
    import re
    src = inspect.getsource(portfolio_routes)
    for m in re.finditer(r"def (\w+)\([^)]*\):(?:(?!\ndef ).)*?v2/holdings", src, re.S):
        fn = getattr(portfolio_routes, m.group(1), None)
        if fn is None:
            continue
        assert require_admin in _dep_callables(fn), (
            f"{m.group(1)} reads the owner's holdings but is not admin-gated")
