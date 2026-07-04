"""Unit tests for the Dhan REST client + instrument parser (pure logic).
Live fetches (historical, option chain, scrip CSV) are prod-only."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.dhan import instruments
from app.dhan.client import rows_from_candles, normalize_chain, historical_daily, configured


def test_rows_from_candles_zips_and_dates():
    data = {"open": [100, 101], "high": [102, 103], "low": [99, 100],
            "close": [101, 102], "volume": [1000, 2000],
            "timestamp": [1326220200, 1326306600]}
    rows = rows_from_candles(data)
    assert len(rows) == 2
    assert rows[0]["date"] == "2012-01-10" and rows[0]["close"] == 101.0 and rows[0]["volume"] == 1000.0
    assert rows[1]["open"] == 101.0


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
)


def test_parse_scrip_master_splits_equities_and_indices():
    eq, idx = instruments.parse_scrip_master(SCRIP_CSV)
    assert eq["TCS"] == "11536" and eq["HDFCBANK"] == "1333"   # NSE equities
    assert idx["NIFTY"] == "13"                                # NSE index
    assert "NIFTY" not in eq                                   # index not in equity map
    assert len(eq) == 2                                         # BSE row excluded (NSE only)


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
