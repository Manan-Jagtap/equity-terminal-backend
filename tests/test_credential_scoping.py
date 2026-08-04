"""A paid credential goes to the vendor host and nowhere else.

The logo route fetches from a public CDN (www.livemint.com) before falling back
to the vendor. It used to attach the IndianAPI key to BOTH requests, handing the
paid credential to an unrelated third party on every cache miss, and it ticked
the vendor quota meter for the CDN fetch too — inflating our own tally against a
request the vendor never saw.
"""
import os, sys, inspect, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import logo_routes, dhan_routes
from app.admin_routes import require_admin


def test_key_is_only_attached_to_the_vendor_host():
    src = inspect.getsource(logo_routes)
    assert "_vendor = url.startswith(BASE)" in src, "host check missing"
    assert 'hdrs = {"X-API-Key": KEY, "x-api-key": KEY} if _vendor else {}' in src, \
        "the credential must be conditional on the vendor host"
    # the unconditional form must be gone
    assert 'requests.get(url, headers={"X-API-Key": KEY' not in src, \
        "credential still sent unconditionally"


def test_vendor_meter_only_ticks_for_vendor_calls():
    src = inspect.getsource(logo_routes)
    m = re.search(r"if _vendor:\s*\n\s*from app import vendor_meter; vendor_meter\.tick\(\)", src)
    assert m, "the CDN fetch must not tick the vendor quota meter"


def test_livemint_is_still_tried_first():
    """The fallback order is deliberate — the fix must not change behaviour,
    only who receives the credential."""
    src = inspect.getsource(logo_routes)
    i_cdn = src.index("livemint.com")
    i_vendor = src.index('f"{BASE}/logo/')
    assert i_cdn < i_vendor, "CDN-first ordering should be preserved"


def _deps(fn):
    return [p.default.dependency for p in inspect.signature(fn).parameters.values()
            if p.default is not inspect.Parameter.empty and hasattr(p.default, "dependency")]


def test_dhan_status_is_admin_only():
    """It makes several LIVE broker calls per request; anonymous callers must
    not be able to spend the owner's broker quota or stall worker threads."""
    assert require_admin in _deps(dhan_routes.dhan_status), \
        "/api/dhan/status must be admin-gated"
