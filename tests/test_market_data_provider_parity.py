"""The two market-data providers must be interchangeable — pinned mechanically.

app/market_data.py exists so MARKET_DATA_PROVIDER can swap the vendor with an
env change and a restart. That promise is only real if every attribute a caller
touches exists on BOTH providers. It did not:

  /api/dhan/status called client._client_id_from_token() and client._post() —
  Dhan-private, absent from app/upstox/client.py — so after the 7 Sep 2026 swap
  the admin diagnostics endpoint raised AttributeError on its FIRST line, in a
  module whose docstring promises "never a 500". It broke exactly when someone
  would reach for it, and nothing caught it: the endpoint is admin-only, so no
  test and no uptime probe ever called it.

Reviewing for this by eye does not scale — the shim has ~20 call sites. So:

  test_public_surfaces_match  — the interface itself cannot drift.
  test_no_unguarded_private_access — an AST scan for the exact shape of the bug
      above: reaching past the shared interface into vendor internals from
      outside the vendor packages, without a hasattr/provider guard.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules bound from app.market_data that callers treat as the shared interface.
_SHIM_NAMES = {"client", "instruments"}
# Aliases the codebase binds these to (`from app.market_data import client as _dhan`).
_ALIASES = {"_dhan", "_md", "_feed_client", "_mdi"}

# Private attributes that ARE part of the de-facto shared interface: both
# providers implement them and callers legitimately use them. Anything else
# private must be hasattr/provider guarded at the call site.
_SHARED_PRIVATES = {"_load", "_cache"}


def _provider_modules():
    import app.dhan.client as dc
    import app.dhan.instruments as di
    import app.upstox.client as uc
    import app.upstox.instruments as ui
    return (("client", dc, uc), ("instruments", di, ui))


def _public(mod):
    return {n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n))}


@pytest.mark.parametrize("label", ["client", "instruments"])
def test_public_surfaces_match(label):
    """Neither provider may carry a public callable the other lacks — that is
    an AttributeError waiting for whichever provider is not currently active."""
    for name, dhan_mod, ups_mod in _provider_modules():
        if name != label:
            continue
        dhan_only = _public(dhan_mod) - _public(ups_mod)
        ups_only = _public(ups_mod) - _public(dhan_mod)
        # The master parsers are legitimately vendor-shaped (CSV vs JSON) and
        # are never reached through the shim — only by their own unit tests.
        dhan_only -= {"parse_scrip_master"}
        ups_only -= {"parse_master"}
        assert not dhan_only, f"{label}: only on Dhan, would break under Upstox: {sorted(dhan_only)}"
        assert not ups_only, f"{label}: only on Upstox, would break under Dhan: {sorted(ups_only)}"


def _scan_files():
    """Every .py outside the vendor packages that could hold a call site."""
    for p in list((ROOT / "app").rglob("*.py")) + [ROOT / "scheduler.py"]:
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith("app/dhan/") or rel.startswith("app/upstox/"):
            continue          # inside a vendor package, privates are its own
        yield rel, p


def _hasattr_names(test: ast.AST) -> set[str]:
    """Attribute names asserted by hasattr(x, "name") inside a condition."""
    names = set()
    for node in ast.walk(test):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "hasattr" and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            names.add(node.args[1].value)
    return names


def _find_offenders(node: ast.AST, guarded: frozenset, out: list, rel: str):
    """Walk with SCOPE. An access counts as guarded only when it sits lexically
    inside the branch that proved the attribute exists.

    Two earlier versions of this test failed to catch the very bug it exists
    for, both by being too coarse: first by asking whether the function
    mentioned hasattr/PROVIDER anywhere, then by collecting hasattr names
    function-wide — so a legitimate guard lower down excused an unguarded
    access above it. Scope is the whole point; without it this is theatre."""
    if isinstance(node, ast.If):
        inner = guarded | _hasattr_names(node.test)
        for child in node.body:
            _find_offenders(child, inner, out, rel)
        for child in node.orelse:          # else-branch proved nothing
            _find_offenders(child, guarded, out, rel)
        _find_offenders_expr(node.test, guarded, out, rel)
        return
    if isinstance(node, ast.IfExp):        # x._a if hasattr(x, "_a") else None
        inner = guarded | _hasattr_names(node.test)
        _find_offenders(node.body, inner, out, rel)
        _find_offenders(node.orelse, guarded, out, rel)
        _find_offenders_expr(node.test, guarded, out, rel)
        return
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        # hasattr(x, "_a") and x._a  — each operand sees the ones before it
        acc = guarded
        for v in node.values:
            _find_offenders(v, acc, out, rel)
            acc = acc | _hasattr_names(v)
        return
    if isinstance(node, ast.Attribute):
        base = node.value
        if (isinstance(base, ast.Name) and base.id in (_SHIM_NAMES | _ALIASES)
                and node.attr.startswith("_")
                and node.attr not in _SHARED_PRIVATES
                and node.attr not in guarded):
            out.append(f"{rel}:{node.lineno} {base.id}.{node.attr}")
    for child in ast.iter_child_nodes(node):
        _find_offenders(child, guarded, out, rel)


def _find_offenders_expr(node, guarded, out, rel):
    """The condition itself: hasattr(...) calls there are checks, not uses."""
    for child in ast.iter_child_nodes(node):
        _find_offenders(child, guarded, out, rel)


def test_no_unguarded_private_access():
    """Reaching into vendor internals from outside is how the status endpoint
    broke. Allowed only inside a branch that hasattr-checked that exact
    attribute."""
    offenders = []
    for rel, path in _scan_files():
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:                       # not ours to police here
            continue
        _find_offenders(tree, frozenset(), offenders, rel)
    assert not offenders, (
        "Unguarded vendor-private access outside app/dhan|app/upstox — these "
        "raise AttributeError whenever the other provider is active. Guard with "
        'hasattr(client, "<attr>") naming that exact attribute:\n  '
        + "\n  ".join(offenders))
