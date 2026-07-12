"""
test_safety.py — locks the defensive guards in the valuation engine so a bad
data row can never crash recommend() or fabricate a confident verdict.

Pure-compute: imports only engines / derive / data_quality (no DB), so it runs
anywhere. Run:  pytest tests/test_safety.py   ·   python tests/test_safety.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import engines
from app.derive import derive_assumptions
from app.data_quality import data_quality


def _a_nonfin():
    return derive_assumptions({}, "IT_SERVICES", False)


def _a_fin():
    return derive_assumptions({}, "BANK", True)


def _co(**over):
    co = dict(type="non-financial", ticker="TEST", name="Test", sector="IT",
              price=100.0, shares=100.0, equity=1000.0, net_profit=120.0,
              revenue=2000.0, net_debt=-200.0,
              series=[{"i": i, "close": 100.0 + i} for i in range(60)],
              synthetic_series=False, synthetic_price=False, nbfc=None)
    co.update(over)
    return co


def test_zero_price_no_crash():
    # B1: mos is None when price is 0 — verdict bands must not do None > 0.15.
    r = engines.recommend(_co(price=0.0), _a_nonfin())
    assert r["verdict"] in ("NO DATA", "LOW CONF")


def test_none_price_no_crash():
    r = engines.recommend(_co(price=None), _a_nonfin())
    assert r["verdict"] in ("NO DATA", "LOW CONF")


def test_nbfc_none_no_crash():
    # B2: co["nbfc"] explicitly None must not crash the financial branch.
    co = _co(type="financial", nbfc=None, revenue=None, net_debt=None)
    r = engines.recommend(co, _a_fin())
    assert "verdict" in r


def test_empty_series_no_crash():
    # B3: empty / missing price series must not crash technicals/_rsi/max.
    assert engines.recommend(_co(series=[]), _a_nonfin())["verdict"]
    assert engines.recommend(_co(series=None), _a_nonfin())["verdict"]
    # very short series (< RSI seed window) too
    short = [{"i": i, "close": 100.0 + i} for i in range(5)]
    assert engines.recommend(_co(series=short), _a_nonfin())["verdict"]


def test_synthetic_series_momentum_neutral():
    # C8: synthetic series must not fabricate bullish momentum.
    r = engines.recommend(_co(synthetic_series=True), _a_nonfin())
    mom = next(x for x in r["reasons"] if x["label"] == "Momentum")
    assert mom["score"] == 50 and not mom["good"]


def test_synthetic_price_forces_low_conf():
    # C9: a missing live price must never produce a BUY off a bogus margin of
    # safety. The engine now goes further than LOW CONF: a synthetic sentinel
    # price yields mos=None → verdict NO DATA (the honest state), and the
    # confidence score still drops below the reliable threshold.
    co = _co(synthetic_price=True)
    assert data_quality(co)["score"] < 0.5
    rec = engines.recommend(co, _a_nonfin())
    assert rec["verdict"] == "NO DATA"
    assert rec["mos"] is None


def test_normal_company_sane():
    r = engines.recommend(_co(), _a_nonfin())
    assert r["intrinsic"] is not None and r["intrinsic"] > 0
    assert r["mos"] is not None
    assert r["verdict"] in ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID", "LOW CONF")


def test_negative_equity_financial_low_conf():
    # Negative net worth → book-based valuation meaningless → not reliable.
    co = _co(type="financial", equity=-500.0, nbfc={}, revenue=None, net_debt=None)
    dq = data_quality(co)
    assert dq["score"] < 0.5


def _stmts(rev0, margin, nw, borrow, n=5, g=0.08):
    s = {}
    for k in range(n):
        yr = 2020 + k
        rev = rev0 * (1 + g) ** k
        ebit = rev * margin
        s[yr] = {"PL": {"revenue": rev, "ebit": ebit, "ebitda": ebit * 1.15,
                        "pat": ebit * 0.74, "tax": ebit * 0.26, "pbt": ebit},
                 "BS": {"net_worth": nw * (1 + g) ** k, "borrowings": borrow}, "CF": {}}
    return s


def test_company_roic_lifts_high_roic_only():
    # Capital-light, no debt → very high realised ROIC → LOWER reinvestment
    # (more free cash) than the flat sector rate.
    hi = derive_assumptions(_stmts(23000, 0.22, 5157, 0), "CONSUMER", False)
    # Heavy capital base, thin margin → ROIC below sector → reinvestment must
    # NOT be lifted (clamp floors at sector, so it equals the sector rate).
    mid = derive_assumptions(_stmts(50000, 0.15, 60000, 40000), "MANUFACTURING", False)
    import app.sector_params as SP
    sector_consumer = min(max(hi["rev_growth"] / SP.params("CONSUMER")["mature_roic"], 0.10), 0.80)
    sector_manu = min(max(mid["rev_growth"] / SP.params("MANUFACTURING")["mature_roic"], 0.10), 0.80)
    assert hi["reinvest_rate"] < sector_consumer - 0.05   # genuinely lifted
    assert abs(mid["reinvest_rate"] - sector_manu) < 1e-3  # unchanged for sub-sector ROIC (4dp rounding)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed.")
    sys.exit(0 if passed == len(fns) else 1)


def test_implausible_mos_forces_low_conf():
    # The AWL/REDINGTON class of failure: internally-consistent inputs, but the
    # sector model produces an intrinsic many times the price (thin-margin
    # business on premium multiples). Must read LOW CONF, never a confident BUY.
    co = _co(price=10.0)                     # tiny price vs healthy fundamentals
    r = engines.recommend(co, _a_nonfin())
    assert r["mos"] is not None and r["mos"] > 2.0   # the setup must reach the guard
    assert r["verdict"] == "LOW CONF"
    assert r["reliable"] is False
    assert any("Implausible margin of safety" in (x.get("note") or "")
               for x in r["reasons"])


def test_plausible_mos_untouched():
    # A normal-gap name must NOT trip the implausibility guard.
    r = engines.recommend(_co(), _a_nonfin())
    assert r["mos"] is None or r["mos"] <= 2.0 or r["verdict"] == "LOW CONF"
    if r["mos"] is not None and 0 < r["mos"] <= 2.0:
        assert r["verdict"] != "LOW CONF" or r["confidence"]["score"] < 0.5


def test_cagr_sign_flip_returns_none_not_complex():
    # (negative/positive) ** (1/3) is a COMPLEX number in Python — it crashed
    # /financials with a 500 for names transitioning profit→loss.
    from app.metrics import _cagr
    assert _cagr(100.0, -50.0, 3) is None
    assert _cagr(-100.0, 50.0, 3) is None
    r = _cagr(100.0, 150.0, 3)
    assert isinstance(r, float) and r > 0


def test_drop_bad_ticks_removes_v_spikes_keeps_real_moves():
    from app.price_hygiene import drop_bad_ticks
    mk = lambda cs: [{"date": f"d{i}", "close": c} for i, c in enumerate(cs)]
    # V-spike (special-session bad tick): 216 -> 79 -> 215 → middle dropped
    out = drop_bad_ticks(mk([210, 216, 79.6, 215.6, 220]))
    assert [r["close"] for r in out] == [210, 216, 215.6, 220]
    # Sustained repricing (real demerger): 660 -> 395 -> 401 → kept
    out = drop_bad_ticks(mk([655, 660, 395, 401, 398]))
    assert 395 in [r["close"] for r in out]
    # Normal series untouched; short series untouched
    assert len(drop_bad_ticks(mk([100, 101, 102]))) == 3
    assert len(drop_bad_ticks(mk([100, 101]))) == 2


def test_metric_plausibility_gate():
    from app.metrics import _plausible, _div
    # LICHSGFIN case: misfiled Rs.51cr interest vs Rs.2.66L cr borrowings → 0.019%
    assert _plausible(_div(51.0, 266216.0), 0.01, 0.25) is None
    # Real lender CoF ~7% passes
    assert abs(_plausible(0.072, 0.01, 0.25) - 0.072) < 1e-9
    assert _plausible(None, 0.01, 0.25) is None
    assert _plausible(0.40, 0.01, 0.25) is None    # >25% = misfiled too


def test_statement_integrity_gate():
    """Serving-time gate: misfiled quarterly/mis-scaled lines must never render
    under an annual column (LICHSGFIN screenshot bug), and Reuters-negative
    cost lines display as magnitudes."""
    from app.financials import _sanitize_statements

    # LICHSGFIN-shaped: FY25 from the annual INC block (negative interest),
    # FY26 poisoned by quarterly supplement lines + misfiled ₹51cr interest.
    nested = {
        2025: {"PL": {"interest_expense": -19515.04, "pbt": 6878.86,
                      "tax": 1436.16, "pat": 5442.7}},
        2026: {"PL": {"interest_income": 6938.0, "interest_expense": 51.31,
                      "nii": 6936.0, "other_income": 24.0, "total_income": 6960.0,
                      "opex": 4879.0, "pbt": 7103.47, "tax": 1499.23, "pat": 5604.24}},
    }
    _sanitize_statements(nested, financial=True)
    # FY26 supplement block dropped (total_income < pbt, ii < pbt, ie collapse)
    for k in ("interest_income", "interest_expense", "nii", "other_income",
              "total_income", "opex"):
        assert k not in nested[2026]["PL"], k
    # annual figures from /stock survive
    assert nested[2026]["PL"]["pat"] == 5604.24
    # FY25 interest expense now a magnitude
    assert nested[2025]["PL"]["interest_expense"] == 19515.04

    # A clean lender year passes untouched
    clean = {2026: {"PL": {"interest_income": 27000.0, "interest_expense": 19000.0,
                           "nii": 8000.0, "other_income": 300.0, "total_income": 8300.0,
                           "opex": 900.0, "pbt": 7100.0, "pat": 5600.0}}}
    _sanitize_statements(clean, financial=True)
    assert clean[2026]["PL"]["interest_income"] == 27000.0

    # Non-financial: revenue collapsing >80% while PAT holds = misfiled period
    nf = {
        2025: {"PL": {"revenue": 50000.0, "pat": 4000.0}},
        2026: {"PL": {"revenue": 6000.0, "pat": 4200.0, "pbt": 5500.0}},
    }
    _sanitize_statements(nf, financial=False)
    assert "revenue" not in nf[2026]["PL"]
    assert nf[2026]["PL"]["pat"] == 4200.0
    # ...but a real revenue collapse WITH a matching PAT collapse is kept
    real = {
        2025: {"PL": {"revenue": 50000.0, "pat": 4000.0}},
        2026: {"PL": {"revenue": 6000.0, "pat": 300.0}},
    }
    _sanitize_statements(real, financial=False)
    assert real[2026]["PL"]["revenue"] == 6000.0


def test_lender_interest_vs_borrowings_gate():
    """MUTHOOTFIN bug: Reuters INC files scrap values (₹-1.85cr) as a lender's
    interest expense against a ₹60,000cr borrowing book — implied cost of funds
    under 1% is a misfile, publish nothing."""
    from app.financials import _sanitize_statements
    nested = {
        2023: {"PL": {"interest_expense": -1.85, "pbt": 4922.78, "pat": 3669.77},
               "BS": {"borrowings": 60000.0}},
        # plausible lender year survives (LICHSGFIN FY25: 7.3% implied CoF)
        2025: {"PL": {"interest_expense": -19515.04, "pbt": 6878.86},
               "BS": {"borrowings": 266216.0}},
    }
    _sanitize_statements(nested, financial=True)
    assert "interest_expense" not in nested[2023]["PL"]
    assert nested[2025]["PL"]["interest_expense"] == 19515.04


def test_document_date_normalization():
    """Ratings without a year must not reach the browser (Date() turns '30 Jun'
    into 2001); the agency URL carries the real year. Audio-recording
    announcements attach to the matching concall."""
    from app.documents_routes import _normalize_docs
    docs = {
        "credit_ratings": [
            {"date": "30 Jun", "title": "Rating update",
             "url": "https://www.crisil.com/.../LICHousingFinanceLimited_June 30_ 2026_RR_399153.html"},
            {"date": "6 Jan", "title": "Rating update",
             "url": "https://www.careratings.com/upload/CompanyFiles/PR/202601130141_LIC.pdf"},
            {"date": "10 Nov 2025", "title": "Rating update", "url": "https://x/y.pdf"},
            {"date": "10 Nov 2025", "title": "Rating update", "url": "https://x/y.pdf"},  # dupe
        ],
        "concalls": [{"date": "May 2026", "transcript": "t.pdf", "ppt": "p.pdf", "summary": None}],
        "announcements": [
            {"title": "Audio Recording of Earnings Conference Call", "date": "2 May - Q4 call",
             "url": "https://bse/rec.pdf"},
        ],
    }
    out = _normalize_docs(docs)
    rt = out["credit_ratings"]
    assert rt[0]["date"] == "30 Jun 2026"
    assert rt[1]["date"] == "6 Jan 2026"
    assert rt[2]["date"] == "10 Nov 2025"
    assert len(rt) == 3                       # dupe collapsed
    assert out["concalls"][0]["recording"] == "https://bse/rec.pdf"


def test_profile_snapshot_fallback(tmp_path):
    """Budget-exhausted or vendor-down profile requests must serve the last
    stored snapshot, never an empty shell (July 2026 quota burn)."""
    from app import profile_routes

    class _Ins:
        data = {"profile_snapshot": {
            "fetched_at": 1.0,   # ancient — stale, so live path is attempted
            "payload": {"ticker": "LICHSGFIN", "description": "Housing lender."}}}
    snap = profile_routes._load_snapshot(_Ins())
    assert snap["payload"]["description"] == "Housing lender."
    # a malformed blob never raises
    class _Bad:
        data = {"profile_snapshot": "garbage"}
    assert profile_routes._load_snapshot(_Bad()) is None
    assert profile_routes._load_snapshot(None) is None


def test_metric_bands_gate_impossible_values():
    """Every published metric carries a plausibility band — misfiled vendor
    inputs (wrong period/unit/line) must yield absence, not authority."""
    from app.metrics import METRIC_BY_KEY

    def v(key, ctx):
        return METRIC_BY_KEY[key].compute(ctx)

    base = dict.fromkeys(
        ["price","shares","market_cap","equity","pat_l","pat_p","pat_3","rev_l","rev_p","rev_3",
         "nii_l","nii_p","nii_3","ebit_l","ebitda_l","pbt_l","int_l","total_assets","borrowings",
         "cash","lt_debt","net_debt","op_cf","capex","fcf","div_paid","provisions","aum","gnpa",
         "nnpa","crar","nim","roa","n_years"])
    # ROE on a face-value share-capital denominator (equity ₹10cr, PAT ₹5,000cr → 500x)
    assert v("roe", {**base, "pat_l": 5000.0, "equity": 10.0}) is None
    # ...but a real 34% ROE passes
    assert abs(v("roe", {**base, "pat_l": 3400.0, "equity": 10000.0}) - 0.34) < 1e-9
    # GNPA of 90% is a decimal-vs-percent misfile
    assert v("gnpa_pct", {**base, "gnpa": 0.9}) is None
    assert v("gnpa_pct", {**base, "gnpa": 0.021}) == 0.021
    # Negative P/E (loss year) is meaningless — absent, not published
    assert v("pe_ratio", {**base, "price": 100.0, "pat_l": -50.0, "shares": 10.0}) is None
    # Interest cover exploding off a misfiled ₹1cr interest line
    assert v("interest_cover", {**base, "ebit_l": 9000.0, "int_l": 1.0}) is None
    # NIM of 45% is a misfile
    assert v("nim_metric", {**base, "nim": 0.45}) is None

    # The fabricated metrics are gone / honest now
    assert "spread" not in METRIC_BY_KEY
    # credit_cost publishes ONLY from reported provisions
    assert v("credit_cost", {**base, "pat_l": 1000.0, "aum": 50000.0}) is None
    assert abs(v("credit_cost", {**base, "provisions": 500.0, "aum": 50000.0}) - 0.01) < 1e-9


def test_ratio_series_gate():
    """Vendor Screener-style ratio cells outside physical bands null out
    per-cell; sane cells (incl. legal negative days) survive."""
    from app.history_routes import _gate_ratio_series
    data = {"ratios": {
        "ROCE %": {"Mar 2024": 23.0, "Mar 2025": 8500.0},
        "Working Capital Days": {"Mar 2024": -45.0, "Mar 2025": 250000.0},
        "Cash Conversion Cycle": {"Mar 2024": 120.0},
    }}
    _gate_ratio_series(data)
    r = data["ratios"]
    assert r["ROCE %"]["Mar 2024"] == 23.0
    assert r["ROCE %"]["Mar 2025"] is None
    assert r["Working Capital Days"]["Mar 2024"] == -45.0
    assert r["Working Capital Days"]["Mar 2025"] is None
    assert r["Cash Conversion Cycle"]["Mar 2024"] == 120.0


def test_ownership_keeps_every_category():
    """'Other' and 'Government' vendor buckets must not silently vanish."""
    from app.ownership_logic import ownership_snapshot
    stock = {"shareholding": [
        {"displayName": "Promoter", "categories": [
            {"holdingDate": "2026-03-31", "percentage": "45.2"}]},
        {"displayName": "Government", "categories": [
            {"holdingDate": "2026-03-31", "percentage": "1.5"}]},
        {"displayName": "Other", "categories": [
            {"holdingDate": "2026-03-31", "percentage": "3.3"}]},
    ]}
    out = ownership_snapshot(stock)
    assert out["promoter"]["pct"] == 45.2
    assert out["government"]["pct"] == 1.5
    assert out["other"]["pct"] == 3.3


def test_market_news_matching_is_word_bounded():
    """'LIC' must not match 'public'/'policy': the fallback that fills a
    company's News tab from general market news matches whole identifiers."""
    from app.news_routes import _match_terms, _mentions
    terms = _match_terms("LICHSGFIN", "LIC Housing Finance Ltd.")
    assert "lic housing" in terms and "lichsgfin" in terms
    # substring traps that used to match
    assert not _mentions("Public sector banks rally on policy hopes", terms)
    assert not _mentions("Navy key protector of republic's interests", terms)
    # genuine mentions match
    assert _mentions("LIC Housing Finance Q4 profit rises 12%", terms)
    assert _mentions("Brokerages raise LICHSGFIN target price", terms)
    # single-word names stay word-bounded (Titan ≠ titanium)
    t2 = _match_terms("TITAN", "Titan Company Ltd.")
    assert not _mentions("Titanium prices surge on aerospace demand", t2)
    assert _mentions("Titan reports strong jewellery sales", t2)


def test_recent_news_items_parse():
    """Production /stock recentNews shape parses into the tab's item shape."""
    from app.news_routes import _recent_news_items
    rn = [{"id": "1", "headline": "LIC Housing cuts home loan rates",
           "date": "12 Jul 2026", "timeToRead": "2 min",
           "url": "https://x/1", "listimage": "img"},
          {"headline": "", "url": "https://x/2"},          # no title → dropped
          "garbage"]
    out = _recent_news_items(rn)
    assert len(out) == 1
    assert out[0]["title"] == "LIC Housing cuts home loan rates"
    assert out[0]["published"].startswith("2026-07-12")
    assert out[0]["url"] == "https://x/1"


def test_market_rows_map_to_universe_only():
    """Dashboard movers must never list names outside our coverage, and every
    kept row carries OUR ticker for clickthrough."""
    from app import market_routes as mr
    mr._uni_cache.update(
        ts=9e12,
        by_ticker={"JIOFIN": "JIOFIN", "TATASTEEL": "TATASTEEL"},
        by_name={mr._norm_name("Jio Financial Services Ltd."): "JIOFIN",
                 mr._norm_name("Tata Steel Ltd."): "TATASTEEL"})
    # RIC row resolves by name (JIOF.NS is a Reuters code, not the NSE symbol)
    assert mr._to_universe({"ticker": "JIOF.NS", "company": "Jio Financial Services"}) == "JIOFIN"
    # nseCode resolves directly
    assert mr._to_universe({"nseCode": "TATASTEEL", "company_name": "Tata Steel"}) == "TATASTEEL"
    # outside coverage → dropped
    assert mr._to_universe({"ticker": "SCAN.BO", "company": "Scan Steels"}) is None
    assert mr._to_universe({"company": "Ruby Mills Ltd"}) is None
    # short-stem prefix trap: Pioneer Investcorp must NOT map to PI Industries
    mr._uni_cache["by_name"][mr._norm_name("PI Industries Ltd.")] = "PIIND"
    assert mr._to_universe({"company": "Pioneer Investcorp"}) is None
    # ...while a real suffix-dropped vendor name still maps
    mr._uni_cache["by_name"][mr._norm_name("Sun Pharmaceutical Industries Ltd.")] = "SUNPHARMA"
    assert mr._to_universe({"company": "Sun Pharmaceutical Industries"}) == "SUNPHARMA"
    mr._uni_cache.update(ts=0, by_ticker={}, by_name={})


def test_dhan_totp_rfc6238():
    """RFC 6238 test vector (SHA-1, T=59s → 287082) — the stdlib TOTP must
    match the spec exactly or every minted token request fails."""
    import base64
    from app.dhan.auth import totp_now
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_now(secret, at=59) == "287082"
    assert totp_now(secret, at=1111111109) == "081804"
    assert totp_now(secret, at=1234567890) == "005924"


def test_dhan_auto_auth_disabled_without_creds(monkeypatch):
    """Without the three env vars the auto path stays inert and the env token
    keeps working exactly as before."""
    from app.dhan import auth, client
    for k in ("DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert auth.enabled() is False
    assert auth.auto_token() == ""
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "envtok")
    assert client.access_token() == "envtok"


def test_dhan_mint_failure_cooldown(monkeypatch):
    """A failed mint must back off, not retry on every live-price poll —
    hammering generateAccessToken tripped Dhan's lockout on day one."""
    from app.dhan import auth
    monkeypatch.setenv("DHAN_CLIENT_ID", "1000000001")
    monkeypatch.setenv("DHAN_PIN", "123456")
    monkeypatch.setenv("DHAN_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    calls = {"n": 0}
    monkeypatch.setattr(auth, "_generate", lambda: (calls.__setitem__("n", calls["n"] + 1), ("", 0.0))[1])
    monkeypatch.setattr(auth, "_read_store", lambda: ("", 0.0))
    auth._mem.update(token="", exp=0.0, next_try=0.0)
    for _ in range(5):
        assert auth.auto_token() == ""
    assert calls["n"] == 1          # one attempt, then cooldown
    auth._mem.update(token="", exp=0.0, next_try=0.0)


def test_portfolio_analysis_and_strategist():
    """Pure analysis math: allocation, concentration, LTCG terms, and the
    strategist's mechanical suggestions with exit/tax reasoning."""
    from app.portfolio_routes import build_analysis, compute_totals
    items = [
        {"ticker": "AAA", "name": "Alpha", "sector": "IT", "value": 60000.0, "cost": 40000.0,
         "mos": -0.40, "verdict": "REDUCE", "term": "short", "days_to_lt": 40, "confidence": "HIGH"},
        {"ticker": "BBB", "name": "Beta", "sector": "Banks", "value": 30000.0, "cost": 25000.0,
         "mos": 0.30, "verdict": "BUY", "term": "long", "days_to_lt": 0, "confidence": "HIGH"},
        {"ticker": "CCC", "name": "Gamma", "sector": "Banks", "value": 10000.0, "cost": 9000.0,
         "mos": 0.05, "verdict": "HOLD", "term": "short", "days_to_lt": 200, "confidence": "MED"},
    ]
    compute_totals(items)
    universe = [
        {"ticker": "DDD", "name": "Delta", "sector": "Pharma", "mos": 0.45, "verdict": "BUY", "confidence": "HIGH"},
        {"ticker": "EEE", "name": "Eps", "sector": "IT", "mos": 0.30, "verdict": "BUY", "confidence": "LOW"},  # LOW conf → excluded
        {"ticker": "AAA", "name": "Alpha", "sector": "IT", "mos": 0.50, "verdict": "BUY", "confidence": "HIGH"},  # held → excluded
    ]
    a = build_analysis(items, universe)
    assert a["concentration"]["n"] == 3 and abs(a["concentration"]["top1"] - 0.6) < 1e-9
    assert a["sectors"][0]["sector"] == "IT"
    assert a["term"]["long"]["n"] == 1 and a["term"]["short"]["n"] == 2
    assert a["term"]["turning_lt_soon"][0]["ticker"] == "AAA"
    acts = {(r["action"], r["ticker"]) for r in a["recommendations"]}
    assert ("REVIEW EXIT", "AAA") in acts          # REDUCE verdict + 60% weight
    assert ("ADD CANDIDATE", "DDD") in acts        # BUY, high MoS, new sector
    assert not any(r["ticker"] == "EEE" for r in a["recommendations"])  # LOW conf never suggested
    aaa = next(r for r in a["recommendations"] if r["ticker"] == "AAA")
    assert any("LONG-TERM in 40d" in x for x in aaa["reasons"])         # LTCG timing note
    assert "Not investment advice" in a["disclaimer"]


def test_term_fields_classification():
    import datetime as dt
    from app.portfolio_routes import _term_fields
    class H:
        buy_date = dt.date.today() - dt.timedelta(days=400)
        added_at = None
    t = _term_fields(H())
    assert t["term"] == "long" and t["days_to_lt"] == 0 and t["date_source"] == "buy_date"
    class H2:
        buy_date = None
        added_at = dt.datetime.now() - dt.timedelta(days=100)
    t2 = _term_fields(H2())
    assert t2["term"] == "short" and t2["days_to_lt"] == 265 and t2["date_source"] == "added"


def test_manager_report_convictions_and_note():
    from app.portfolio_routes import build_analysis, compute_totals, manager_report
    items = [
        {"ticker": "AAA", "name": "Alpha", "sector": "IT", "value": 60000.0, "cost": 40000.0,
         "mos": -0.40, "verdict": "REDUCE", "term": "short", "days_to_lt": 40, "confidence": "HIGH"},
        {"ticker": "BBB", "name": "Beta", "sector": "Banks", "value": 40000.0, "cost": 25000.0,
         "mos": 0.30, "verdict": "BUY", "term": "long", "days_to_lt": 0, "confidence": "HIGH"},
    ]
    compute_totals(items)
    uni = [{"ticker": "DDD", "name": "Delta", "sector": "Pharma", "mos": 0.45,
            "verdict": "BUY", "confidence": "HIGH"}]
    analysis = build_analysis(items, uni)
    m = manager_report(items, analysis, mom_by={"AAA": "below", "DDD": "above"},
                       target_by={"AAA": 0.35})
    exit_a = next(a for a in m["actions"] if a["ticker"] == "AAA")
    add_d = next(a for a in m["actions"] if a["ticker"] == "DDD")
    # exit conviction beats a fresh add; momentum reasons attached; sized in ₹
    assert exit_a["conviction"] > add_d["conviction"]
    assert any("50-DMA" in x for x in exit_a["reasons"])
    assert exit_a["size_inr"] == round((0.6 - 0.35) * 100000)
    assert add_d["size_inr"] == round(0.03 * 100000)
    assert m["aum"] == 100000.0
    assert "Concentration" in m["note"] or "positions worth" in m["note"]


def test_manager_v3_levels_and_intel():
    """Entry band = fair/1.25 (the BUY gate), target = fair value; ownership
    flow and results momentum shift conviction in the right direction."""
    from app.portfolio_routes import build_analysis, compute_totals, manager_report
    items = [{"ticker": "AAA", "name": "Alpha", "sector": "IT", "value": 50000.0,
              "cost": 40000.0, "mos": -0.40, "verdict": "REDUCE", "term": "long",
              "days_to_lt": 0, "confidence": "HIGH", "price": 100.0, "intrinsic": 60.0}]
    compute_totals(items)
    uni = [{"ticker": "DDD", "name": "Delta", "sector": "Pharma", "mos": 0.50,
            "verdict": "BUY", "confidence": "HIGH", "price": 200.0}]
    analysis = build_analysis(items, uni)
    m = manager_report(items, analysis,
                       intel_by={"AAA": {"inst_delta": -1.2, "pat_yoy": -0.30},
                                 "DDD": {"inst_delta": 1.1, "pat_yoy": 0.34, "surprise_pct": 0.17}},
                       quote_by={"DDD": {"price": 200.0, "mos": 0.50}})
    add = next(a for a in m["actions"] if a["ticker"] == "DDD")
    # fair = 200*(1+0.5)=300; entry_below = 300/1.25 = 240; upside 50%
    assert add["levels"]["target"] == 300.0
    assert add["levels"]["entry_below"] == 240.0
    assert abs(add["levels"]["upside_pct"] - 0.5) < 1e-9
    assert any("institutions added" in x for x in add["reasons"])
    assert any("PAT +34%" in x for x in add["reasons"])
    exit_a = next(a for a in m["actions"] if a["ticker"] == "AAA")
    assert exit_a["levels"]["target"] == 60.0            # model fair value
    assert any("institutions cut" in x for x in exit_a["reasons"])


def test_holding_and_book_xirr():
    """Per-position XIRR annualises capital + dividends over the REAL holding
    period; sub-week positions stay None (annualisation noise)."""
    import datetime as dt
    from app.portfolio_routes import _item

    class Co:
        ticker, name, sector = "AAA", "Alpha", "IT"
    class H:
        id, qty, avg_cost = 1, 10.0, 100.0
        company = Co()
        buy_date = dt.date.today() - dt.timedelta(days=365)
        added_at = None
    it = _item(H(), price=121.0, val=None, actions=None)
    assert abs(it["xirr"] - 0.21) < 0.01              # 21% over exactly a year
    class H2(H):
        buy_date = dt.date.today() - dt.timedelta(days=3)
    assert _item(H2(), price=121.0, val=None, actions=None)["xirr"] is None
