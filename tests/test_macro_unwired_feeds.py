"""The Economy dashboard's two silences must not arrive as one flag.

Seven high-frequency activity indicators (GST collections, e-way bills, both
PMIs, peak power, auto sales, UPI) have no source wired, so /api/macro returned
`awaiting: True` for them — the same flag it uses for a wired source whose next
release simply has not printed. The page could not tell "late" from "never", so
seven of twenty rows read as data merely on its way.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import macro_data as M

UNWIRED = [M.GST_COLLECTIONS, M.EWAY_BILLS, M.PMI_MFG, M.PMI_SVC,
           M.POWER_DEMAND, M.AUTO_SALES, M.UPI_TXN]


def _rows(db=None):
    """slug → dashboard row. db=None reads the committed DBIE seed only, so the
    empty indicators are exactly the ones with no feed behind them."""
    return {r["slug"]: r for sec in M.dashboard(db)["sections"] for r in sec["series"]}


def test_unconfigured_activity_indicators_report_no_feed(monkeypatch):
    for slug in UNWIRED:
        monkeypatch.delenv(f"ACTIVITY_{M.ACTIVITY_ENV[slug]}_URL", raising=False)
    rows = _rows()
    for slug in UNWIRED:
        r = rows[slug]
        assert r["awaiting"] is True and r["value"] is None
        assert r["status"] == "no_feed", f"{slug} reported {r.get('status')!r}"
        assert r["detail"], "an unwired row must say why it is blank"
        assert r["source"], "an unwired row must still name its publisher"


def test_wiring_a_feed_flips_the_status(monkeypatch):
    monkeypatch.setenv(f"ACTIVITY_{M.ACTIVITY_ENV[M.UPI_TXN]}_URL",
                       "https://example.invalid/upi.json")
    r = _rows()[M.UPI_TXN]
    # Still no points — the fetcher has not run — but now honestly "late",
    # not "never", and read from the env so no redeploy is needed.
    assert r["awaiting"] is True and r["status"] == "awaiting_release"


def test_a_wired_source_between_prints_is_not_called_unwired():
    # OECD CLI has a keyless fetcher (macro_sources.fetch_oecd_cli); against the
    # bare seed it carries no points, and that genuinely IS awaiting a release.
    # Without this, "empty" and "unwired" would just be synonyms again.
    r = _rows()["oecd_cli_india"]
    assert r["awaiting"] is True and r["status"] == "awaiting_release"


def test_a_populated_indicator_carries_no_status():
    r = _rows()[M.CPI_2024]
    assert not r.get("awaiting") and r["value"] is not None
    assert "status" not in r      # `status` explains an ABSENT value, nothing else


def test_every_activity_indicator_has_an_env_hook():
    # A new activity slug with no ACTIVITY_ENV entry would fall through to
    # "awaiting_release" forever — the exact claim this fix removes.
    assert set(M.ACTIVITY_META) == set(M.ACTIVITY_ENV)


def test_fetcher_and_dashboard_share_one_env_map():
    from app import macro_sources as S
    assert S._ACTIVITY_ENV is M.ACTIVITY_ENV
