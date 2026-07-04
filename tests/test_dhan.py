"""Unit tests for the Dhan REST client + instrument parser (pure logic).
Live fetches (historical, option chain, scrip CSV) are prod-only."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.dhan import instruments
from app.dhan.client import rows_from_candles, normalize_chain, historical_daily, configured


def test_rows_from_candles_zips_and_dates():
    # 1326220200 = 2012-01-10 18:30 UTC = 2012-01-11 00:00 IST — a midnight-IST
    # stamped candle. The date must be read in IST: the old UTC conversion
    # shifted every such candle back a day (Sunday-dated rows in prod).
    data = {"open": [100, 101], "high": [102, 103], "low": [99, 100],
            "close": [101, 102], "volume": [1000, 2000],
            "timestamp": [1326220200, 1326306600]}
    rows = rows_from_candles(data)
    assert len(rows) == 2
    assert rows[0]["date"] == "2012-01-11" and rows[0]["close"] == 101.0 and rows[0]["volume"] == 1000.0
    assert rows[1]["date"] == "2012-01-12"
    assert rows[1]["open"] == 101.0


def test_rows_from_candles_both_timestamp_conventions_agree():
    # The same trading day (2024-05-15) stamped two ways: 09:15 IST market open
    # (03:45 UTC) and 00:00 IST midnight (18:30 UTC of May 14). Both must map to
    # 2024-05-15 — mixed conventions in one response caused the duplicate-key
    # crash when converted in UTC.
    market_open, midnight_ist = 1715744700, 1715711400
    data = {"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1],
            "volume": [1, 1], "timestamp": [market_open, midnight_ist]}
    rows = rows_from_candles(data)
    assert [r["date"] for r in rows] == ["2024-05-15", "2024-05-15"]


def test_rows_from_candles_empty_safe():
    assert rows_from_candles({}) == []
    assert rows_from_candles(None) == []


def test_normalize_chain_sorts_strikes_and_computes_pcr():
    data = {"data": {"last_price": 25000, "oc": {
        "25100.000000": {"ce": {"oi": 100, "last_price": 50, "implied_volatility": 12, "greeks": {"delta": 0.4}},
                          "pe": {"oi": 300, "last_price": 60}},
        "24900.000000": {"ce": {"oi": 200, "last_price": 80},
                         "pe": {"oi": 100, "last_price": 40}},
    }}, "status": "success"}
    n = normalize_chain(data)
    assert n["last_price"] == 25000
    assert [s["strike"] for s in n["strikes"]] == [24900.0, 25100.0]     # strike-sorted
    assert n["total_ce_oi"] == 300 and n["total_pe_oi"] == 400
    assert abs(n["pcr"] - 400 / 300) < 1e-9
    hi = n["strikes"][1]
    assert hi["ce"]["iv"] == 12 and hi["ce"]["delta"] == 0.4 and hi["pe"]["ltp"] == 60


def test_historical_unconfigured_returns_none(monkeypatch):
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    assert configured() is False
    assert historical_daily("1333", "2024-01-01", "2024-02-01") is None


SCRIP_CSV = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SM_SYMBOL_NAME\n"
    "NSE,E,11536,EQUITY,TCS,TATA CONSULTANCY SERVICES\n"
    "NSE,E,1333,EQUITY,HDFCBANK,HDFC BANK\n"
    "NSE,I,13,INDEX,NIFTY,NIFTY 50\n"
    "BSE,E,500180,EQUITY,HDFCBANK,HDFC BANK\n"
    "NSE,D,49081,FUTSTK,TCS-28Aug2026-FUT,TCS\n"
    "NSE,D,49082,OPTSTK,TCS-28Aug2026-3200-CE,TCS\n"
    "NSE,D,49090,FUTSTK,HDFCBANK-28Aug2026-FUT,\n"
    "NSE,D,49999,FUTSTK,011NSETEST-28Aug2026-FUT,011NSETEST\n"
    "NSE,D,49998,OPTSTK,GHOST-28Aug2026-100-CE,GHOST\n"
)


def test_parse_scrip_master_splits_equities_and_indices():
    eq, idx, fno = instruments.parse_scrip_master(SCRIP_CSV)
    assert eq["TCS"] == "11536" and eq["HDFCBANK"] == "1333"   # NSE equities
    assert idx["NIFTY"] == "13"                                # NSE index
    assert "NIFTY" not in eq                                   # index not in equity map
    assert len(eq) == 2                                         # BSE row excluded (NSE only)
    # F&O underlyings: from the plain-symbol column, or the derivative symbol
    # prefix when that column is blank. Test scrips (NSETEST) and underlyings
    # that aren't real listed equities (GHOST) are excluded.
    assert fno == {"TCS", "HDFCBANK"}


def test_security_id_lookup_with_quirks(monkeypatch):
    instruments._cache.update({"eq": {"TCS": "11536", "MM": "2031"},
                               "idx": {"NIFTY": "13"}, "ts": time.time()})
    assert instruments.security_id("tcs") == "11536"
    assert instruments.security_id("NIFTY", index=True) == "13"
    assert instruments.security_id("M&M") == "2031"            # '&' stripped → MM
    assert instruments.security_id("UNKNOWN") is None


def test_client_id_derived_from_token(monkeypatch):
    import base64, json
    from app.dhan import client
    payload = base64.urlsafe_b64encode(json.dumps(
        {"dhanClientId": "1100001234", "exp": 9999999999}).encode()).decode().rstrip("=")
    fake_jwt = f"eyJhbGciOiJIUzUxMiJ9.{payload}.sig"
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", fake_jwt)
    monkeypatch.setenv("DHAN_CLIENT_ID", "9999999999")   # wrong env must NOT win
    assert client._client_id_from_token() == "1100001234"
    assert client.client_id() == "1100001234"


def test_client_id_env_fallback_when_token_lacks_claim(monkeypatch):
    import base64, json
    from app.dhan import client
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 9999999999}).encode()).decode().rstrip("=")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", f"eyJhbGciOiJIUzUxMiJ9.{payload}.sig")
    monkeypatch.setenv("DHAN_CLIENT_ID", "1100005678")
    assert client._client_id_from_token() == ""
    assert client.client_id() == "1100005678"


def test_resolve_index_aliases_exact_and_fuzzy():
    idx = {"NIFTY": "13", "BANKNIFTY": "25", "NIFTY METAL": "31",
           "NIFTYNXT50": "38", "NIFTY MIDCAP 100": "36", "INDIA VIX": "21"}
    r = instruments.resolve_index
    assert r("NIFTY 50", idx) == "13"            # alias
    assert r("NIFTY Bank", idx) == "25"          # alias, case-insensitive
    assert r("NIFTY METAL", idx) == "31"         # exact
    assert r("NIFTY Next 50", idx) == "38"       # alias
    assert r("India VIX", idx) == "21"           # exact after casing
    assert r("NIFTY Midcap 100", idx) == "36"    # space-insensitive
    assert r("SENSEX", idx) is None              # BSE — honestly unresolvable
    assert r("", idx) is None and r("NIFTY 50", {}) is None
