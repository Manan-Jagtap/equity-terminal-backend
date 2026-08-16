"""
tests/gen_verdict_cases.py — regenerate the node-vs-python VERDICT parity snapshot.

The two other harnesses pin the NUMBERS (engine.js ↔ engines.blended, derive.js
↔ derive_assumptions) and nothing pinned the LABEL: the frontend's
lib/recommend.js re-derives BUY / ACCUMULATE / HOLD / REDUCE / AVOID / LOW CONF
/ NO DATA from the same inputs on the seed/offline path (every row, whenever
/api/companies fails) and on the company page while /api/companies/{ticker}
loads — and its ladder had drifted from engines.recommend. The "TRIM"→"REDUCE"
rename was caught by hand; the −0.25→−0.18 AVOID floor, the +50% BUY cap, the
high-ROE REDUCE band and cliff, and the corroboration gates were not. This
script runs engines.recommend() over deterministic cases that straddle every
zone edge and gate, and dumps inputs + the expected verdict.

Design of the cases:
  · A handful of company PROFILES whose derived margin / leverage / ROE land the
    composite firmly inside one regime (high → BUY-eligible, mid → ACCUMULATE
    at most, weak → HOLD/REDUCE/AVOID; a strong bank and an average NBFC).
  · For each profile the intrinsic is computed FIRST, then the price is set to
    hit a TARGET margin of safety just either side of every threshold in
    engines.recommend (the base bands, the +50% BUY cap, the +80% lender gate,
    the +100% implausible-upside cliff, the −45% high-ROE cliff, the −90%
    collapsed-value cliff). blended() never reads price, so pinning the price
    after the fact does not move the intrinsic — and both sides then see the
    identical (iv − price) / price to the last ulp.
  · Named SPECIALS for the gates a target-MoS sweep cannot reach: no shares
    (NO DATA), no live price (NO DATA via mos None), thin data (LOW CONF via
    confidence), INSURANCE, negligible / negative ROE, a synthetic price series
    (confidence capped to MEDIUM), real up/down-trend series (momentum leg),
    and a relative-only value (primary DCF unusable). Each special asserts the
    backend actually took the intended path, so a future engine change cannot
    silently turn "the INSURANCE gate" into "some other case".

Deliberately OUTSIDE this contract, exactly as alt_models is outside the engine
harness: ticker-keyed overrides (alt_models presets, _CONGLOMERATES,
_FEE_FINANCIALS, _segment_sotp) and the FIX-16 archetype models. Every case
uses a neutral ticker/sector so none of those fire; the low-ROE and loss-maker
specials keep under three years of statements so young_company_value() abstains.

Every case carries FULL statements (backend shape) so both sides derive the
assumption block from the identical dict — the derive harness (48/48) is the
gate that keeps THAT step honest; this one assumes it. (A seed row without
statements derives from a one-year snapshot instead; that is an INTRINSIC
difference and belongs to the derive/engine harnesses, not here.)

Run whenever engines.recommend / data_quality.py / sector_params.py change:
    python tests/gen_verdict_cases.py /path/to/equity-terminal/tests/verdictCases.json
then:
    node tests/verdictParity.mjs   (in the frontend repo)
"""
import json, random, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import engines  # noqa: E402
from app.derive import derive_assumptions  # noqa: E402

random.seed(20260816)
r = random.uniform

# Company profiles. `roe`/`margin`/`borrow` drive the DERIVED assumption block
# (derive.py reads PAT/net worth, EBIT/revenue, borrowings/(borrowings+net
# worth)) and the snapshot ratios the composite reads.
NONFIN_PROFILES = {
    # High-return franchise: quality ~100, no leverage → composite clears 68 at
    # any positive MoS. Also _high_roe (>= 16%), so it walks the high-ROE
    # REDUCE band and the -45% cliff.
    "franchise": dict(vs="CONSUMER", roe=0.26, margin=0.28, borrow=0.0,
                      growth=0.12, capex=0.03),
    # Average industrial: mid quality, some debt → ACCUMULATE at most, never
    # BUY below the +50% cap; NOT high-ROE.
    "average":   dict(vs="MANUFACTURING", roe=0.12, margin=0.15, borrow=0.35,
                      growth=0.08, capex=0.05),
    # Weak: quality ~15, no leverage flag → HOLD / REDUCE / AVOID until a very
    # large MoS. Its legs disagree ~7× (a thin DCF under fat sector multiples),
    # so it also walks the DAT-15 dispersion withhold on the AVOID side.
    "weak":      dict(vs="CAPITAL_GOODS", roe=0.06, margin=0.06, borrow=0.30,
                      growth=0.03, capex=0.05),
    # Levered average: debt_weight ≈ 0.42 — inside the band where the backend
    # flags "High leverage" (> 0.40) and the client does not (> 0.45).
    "levered":   dict(vs="MANUFACTURING", roe=0.12, margin=0.15, borrow=0.72,
                      growth=0.08, capex=0.05),
}
FIN_PROFILES = {
    # Strong bank: quality ~80 → BUY-eligible; forecast ROE >= 16% → high-ROE.
    "bank_strong": dict(vs="BANK", roe=0.24, gnpa=0.012, nnpa=0.003, crar=0.19,
                        nim=0.045, payout=0.20),
    # Average NBFC: thin quality → ACCUMULATE only at a large MoS — which is
    # where the lender gate's Gordon-P/B corroboration decides the label.
    "nbfc_avg":    dict(vs="NBFC", roe=0.13, gnpa=0.035, nnpa=0.012, crar=0.165,
                        nim=0.080, payout=0.15),
}
PROFILES = {**{k: (v, False) for k, v in NONFIN_PROFILES.items()},
            **{k: (v, True) for k, v in FIN_PROFILES.items()}}

# The sweep: every threshold engines.recommend (and the price_gates mirror)
# keys on, with a point 0.001 to each side, × the profiles that make it
# informative. An explicit table rather than something derived from the
# engine's constants, so a silently-moved band shows up as a fixture diff — not
# as a fixture that moved with it. CORE = one of each regime; PAIR = one
# high-ROE BUY-capable name and one plain one, where the other regimes add
# nothing.
#
# Why ±0.001 and never exactly ON a threshold: engine parity is 1e-9 RELATIVE,
# not bit-exact — the two ports sum in different orders and the intrinsic can
# differ in its last ulp. An exactly-on-threshold MoS then lands on opposite
# sides of a `>=` by float noise alone (seen: weak @ −0.10 read HOLD in Python
# and REDUCE in node with |Δmos| = 1.1e-16). A 0.001 offset is 10^6× that
# noise and still pins every edge to a tenth of a percent.
CORE = ("franchise", "average", "weak", "bank_strong")
PAIR = ("franchise", "average")
SWEEP = [
    # target mos, profiles                            what sits here in engines.recommend
    (-0.950, CORE), (-0.901, CORE), (-0.899, CORE),   # DAT-13 collapsed value (mos <= -0.90)
    (-0.500, PAIR), (-0.451, CORE), (-0.449, CORE),   # high-ROE cliff (< -0.45) / high-ROE REDUCE floor (>= -0.45)
    (-0.300, PAIR + ("levered",)),
    (-0.251, PAIR), (-0.249, PAIR),                   # the client's STALE -0.25 REDUCE/AVOID edge
    (-0.181, CORE), (-0.179, CORE),                   # REDUCE floor (>= -0.18)
    (-0.101, CORE), (-0.099, CORE),                   # HOLD floor (>= -0.10)
    ( 0.000, PAIR),
    ( 0.049, CORE + ("levered",)), ( 0.051, CORE + ("levered",)),    # ACCUMULATE floor (> 0.05)
    ( 0.149, CORE + ("levered",)), ( 0.151, CORE + ("levered",)),    # BUY floor (> 0.15)
    ( 0.300, PAIR + ("levered", "nbfc_avg")),
    ( 0.499, CORE + ("levered",)), ( 0.501, CORE), ( 0.650, PAIR),   # BUY cap (<= 0.50) / VAL-01 band (> 0.50)
    ( 0.799, PAIR + ("nbfc_avg",)), ( 0.801, CORE + ("nbfc_avg",)),  # lender divergence gate (>= 0.80)
    ( 0.999, PAIR), ( 1.001, CORE + ("nbfc_avg",)), ( 1.500, PAIR),  # implausible upside (> 1.0)
]


def _years(n, end=2026):
    return list(range(end - n + 1, end + 1))


def _nonfin_statements(p, n=3):
    """Backend-shape statements whose derived margin / leverage / growth match
    the profile (mild noise so no two cases share a fixture row)."""
    rev0 = r(20_000, 90_000)
    out = {}
    for k, y in enumerate(_years(n)):
        rev = rev0 * (1 + p["growth"]) ** k * r(0.99, 1.01)
        ebit = rev * p["margin"] * r(0.97, 1.03)
        ebitda = ebit + rev * 0.03
        pbt = ebit * 0.95
        pat = pbt * 0.75
        nw = pat / p["roe"]
        out[y] = {
            "PL": {"revenue": round(rev, 2), "ebit": round(ebit, 2), "ebitda": round(ebitda, 2),
                   "pbt": round(pbt, 2), "tax": round(pbt * 0.25, 2), "pat": round(pat, 2),
                   "depreciation": round(rev * 0.03, 2),
                   "interest_expense": round(nw * p["borrow"] * 0.08, 2)},
            "BS": {"net_worth": round(nw, 2), "borrowings": round(nw * p["borrow"], 2),
                   "cash": round(rev * 0.04, 2), "receivables": round(rev * 0.12, 2),
                   "inventory": round(rev * 0.10, 2), "payables": round(rev * 0.08, 2)},
            "CF": {"capex": round(-rev * p["capex"], 2)},
        }
    return out


def _fin_statements(p, n=3):
    nw0 = r(20_000, 200_000)
    out, nw = {}, nw0
    for y in _years(n):
        pat = nw * p["roe"] * r(0.98, 1.02)
        out[y] = {"PL": {"pat": round(pat, 2)},
                  "BS": {"net_worth": round(nw, 2)},
                  "CF": {"dividends": round(-pat * p["payout"], 2)}}
        nw = nw * (1 + p["roe"] * (1 - p["payout"]))
    return out


def _series(kind, last, n=60):
    """A real-looking close series ending at `last`: 'up' (above both SMAs,
    RSI > 70) or 'down' (below both, RSI < 30)."""
    step = 0.006 if kind == "up" else -0.006
    closes = [last / (1 + step) ** (n - 1 - i) for i in range(n)]
    return [{"i": i, "close": round(c, 2)} for i, c in enumerate(closes)]


def make_company(idx, name, prof, *, fin, tag=""):
    """Snapshot + statements in the exact shape assemble.build_company emits."""
    st = _fin_statements(prof) if fin else _nonfin_statements(prof)
    last = st[max(st)]
    co = {
        "ticker": f"VP{idx:03d}", "name": f"Parity {name}{tag}",
        # Neutral sector: matches none of engines._FEE_FINANCIAL_SECTOR_HINTS
        # and none of alt_models' name/sector archetype hints.
        "sector": "Parity Test",
        "type": "financial" if fin else "nonfinancial",
        "template_code": prof["vs"], "valuation_sector": prof["vs"],
        "shares": round(r(50, 800), 2),
        "equity": last["BS"]["net_worth"], "net_profit": last["PL"]["pat"],
        "series": [], "synthetic_series": False, "synthetic_price": False,
        "statements": st,
    }
    if fin:
        co["nbfc"] = {"aum": None, "gnpa": prof["gnpa"], "nnpa": prof["nnpa"],
                      "crar": prof["crar"], "nim": prof["nim"], "roa": None}
    else:
        co["revenue"] = last["PL"]["revenue"]
        co["net_debt"] = round(last["BS"]["borrowings"] - last["BS"]["cash"], 2)
    return co


def price_for(co, a, target_mos):
    """Pin the price so (iv − price)/price == target_mos, where iv is the
    intrinsic recommend() itself would price against (the blend, or the
    relative-only fallback). Nothing in that number reads price, so a throwaway
    1.0 gets it without moving it."""
    iv = engines.recommend({**co, "price": 1.0}, a).get("intrinsic")
    if not iv or iv <= 0:
        return None
    return iv / (1 + target_mos)


def finish(co, a, label):
    rec = engines.recommend(co, a)
    # `_drivers` are provenance strings (exempt from parity, and a third of the
    # fixture's bytes); the client's derive.js re-derives its own from the same
    # statements. Everything numeric stays.
    a_out = {k: v for k, v in a.items() if k != "_drivers"}
    return {
        "label": label, "co": co, "a": a_out,
        "exp_verdict": rec["verdict"],
        # Diagnostics only — the frontend has no gate_state / value_suppressed.
        # Kept so a verdict mismatch can be attributed (ladder vs composite vs
        # confidence) without re-running Python.
        "exp_mos": rec["mos"], "exp_composite": rec["composite"],
        "exp_conf_level": (rec.get("confidence") or {}).get("level"),
        "exp_gate": rec.get("gate_state"),
        "exp_value_suppressed": bool(rec.get("value_suppressed")),
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/verdict_cases.json"
    cases = []

    # 1. The MoS sweep.
    for m, names in SWEEP:
        for name in names:
            prof, fin = PROFILES[name]
            co = make_company(len(cases), name, prof, fin=fin)
            a = derive_assumptions(co["statements"], prof["vs"], fin)
            co["price"] = price_for(co, a, m)
            cases.append(finish(co, a, f"{name} @ mos {m:+.3f}"))

    # 2. Specials — the gates a MoS sweep cannot reach. `pre` reshapes the
    #    company BEFORE the assumptions are derived and the price pinned (so
    #    both sides still derive from the same statements); `post` runs after
    #    (needs the price, or removes it). `expect` pins the backend's own path.
    def special(name, prof, fin, mos, label, *, pre=None, post=None, expect=None):
        co = make_company(len(cases), name, prof, fin=fin, tag=f" [{label}]")
        if pre:
            pre(co)
        a = derive_assumptions(co["statements"], prof["vs"], fin)
        co["price"] = price_for(co, a, mos)
        if post:
            post(co)
        case = finish(co, a, f"{name} @ mos {mos:+.3f} [{label}]")
        for k, v in (expect or {}).items():
            if case[k] != v:
                raise SystemExit(f"special '{label}' no longer reaches the path it exists "
                                 f"to pin: {k}={case[k]!r}, expected {v!r} — re-engineer "
                                 f"the special, do not just regenerate")
        cases.append(case)

    F, A, W = NONFIN_PROFILES["franchise"], NONFIN_PROFILES["average"], NONFIN_PROFILES["weak"]
    B = FIN_PROFILES["bank_strong"]

    def _trim_years(co, keep):
        for y in sorted(co["statements"])[:-keep]:
            del co["statements"][y]

    def _set_roe(co, roe):
        # Restate PAT in every remaining year AND the snapshot to the target ROE.
        for y, st in co["statements"].items():
            st["PL"]["pat"] = round(st["BS"]["net_worth"] * roe, 2)
        co["net_profit"] = co["statements"][max(co["statements"])]["PL"]["pat"]

    # NO DATA: no share count → no per-share intrinsic on either side.
    special("average", A, False, 0.30, "no shares",
            post=lambda co: co.update(shares=None),
            expect={"exp_verdict": "NO DATA"})
    # NO DATA: no live price → mos None. build_company keeps a 1.0 sentinel +
    # synthetic_price; /api/companies sends the client price=null. Same state.
    special("franchise", F, False, 0.30, "no live price",
            post=lambda co: co.update(price=None, synthetic_price=True),
            expect={"exp_verdict": "NO DATA"})
    # LOW CONF by confidence: no book equity AND no PAT → score 0.40.
    special("average", A, False, 0.30, "thin data",
            post=lambda co: co.update(equity=None, net_profit=None),
            expect={"exp_verdict": "LOW CONF"})
    # LOW CONF by confidence: an explicit upstream data warning (0.45) + no book.
    special("average", A, False, 0.30, "data warning",
            post=lambda co: co.update(equity=None,
                                      data_warning="Live price differs sharply from seeded basis"),
            expect={"exp_verdict": "LOW CONF"})
    # INSURANCE: life insurers are LOW CONF outright (value is embedded value).
    special("bank_strong", dict(B, vs="INSURANCE"), True, 0.30, "insurance",
            expect={"exp_verdict": "LOW CONF"})
    # Negligible ROE (< 4%): the intrinsic model is unreliable → LOW CONF.
    # Two years of statements so FIX-16's young_company_value() cannot fire.
    special("average", A, False, 0.00, "low roe",
            pre=lambda co: (_trim_years(co, 2), _set_roe(co, 0.02)),
            expect={"exp_verdict": "LOW CONF", "exp_gate": "abstain"})
    # Loss-maker: negative ROE → LOW CONF (plus a 0.30 confidence penalty).
    special("weak", W, False, 0.00, "loss maker",
            pre=lambda co: (_trim_years(co, 2), _set_roe(co, -0.05)),
            expect={"exp_verdict": "LOW CONF"})
    # Synthetic price series: confidence capped to MEDIUM on both sides — the
    # client's BUY additionally demands level === "high"; the backend's does not.
    special("franchise", F, False, 0.30, "synthetic series",
            post=lambda co: co.update(synthetic_series=True),
            expect={"exp_verdict": "BUY", "exp_conf_level": "medium"})
    # Real series: the momentum leg (SMA-20/50 flags + RSI extremes) exercised.
    special("franchise", F, False, 0.10, "uptrend series",
            post=lambda co: co.update(series=_series("up", co["price"])))
    special("average", A, False, 0.10, "downtrend series",
            post=lambda co: co.update(series=_series("down", co["price"])))
    special("average", A, False, 0.30, "uptrend series",
            post=lambda co: co.update(series=_series("up", co["price"])))
    special("weak", W, False, -0.05, "downtrend series",
            post=lambda co: co.update(series=_series("down", co["price"])))
    # Synthetic series WITH history: the backend scores momentum neutral when
    # the series is flagged synthetic; the client reads the walk as tape.
    special("franchise", F, False, 0.30, "synthetic series + history",
            post=lambda co: co.update(synthetic_series=True,
                                      series=_series("up", co["price"])),
            expect={"exp_verdict": "BUY", "exp_conf_level": "medium"})

    # Relative-only (DAT-14): heavy reinvestment + heavy debt make the primary
    # FCFF DCF negative while two relative legs stay positive → LOW CONF on a
    # reference figure. Fixed statements: this shape is engineered, not sampled.
    def _relative_only(co):
        st = {}
        for k, y in enumerate(_years(5)):
            rev = 30_000 * 1.25 ** k
            st[y] = {"PL": {"revenue": rev, "ebit": rev * 0.04, "ebitda": rev * 0.07,
                            "pat": rev * 0.025, "pbt": rev * 0.035, "tax": rev * 0.01,
                            "depreciation": rev * 0.03, "interest_expense": rev * 0.01},
                     "BS": {"net_worth": 20_000 * 1.1 ** k, "borrowings": 40_000,
                            "receivables": rev * 0.2, "inventory": rev * 0.25,
                            "payables": rev * 0.1, "cash": 2_000},
                     "CF": {"capex": -rev * 0.30}}
        last = st[max(st)]
        co.update(statements=st, shares=100.0,
                  equity=round(last["BS"]["net_worth"], 2),
                  net_profit=round(last["PL"]["pat"], 2),
                  revenue=round(last["PL"]["revenue"], 2), net_debt=80_000.0)
    special("weak", W, False, 0.00, "relative only", pre=_relative_only,
            expect={"exp_verdict": "LOW CONF", "exp_gate": "relative_only"})

    with open(out, "w") as f:
        json.dump(cases, f, indent=1)
    from collections import Counter
    dist = Counter(c["exp_verdict"] for c in cases)
    print(f"Wrote {len(cases)} cases -> {out}")
    print("  expected verdicts:", dict(sorted(dist.items())))


if __name__ == "__main__":
    main()
