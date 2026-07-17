"""
tests/gen_derive_cases.py — regenerate the node-vs-python DERIVE parity snapshot.

The frontend's lib/derive.js must stay bit-for-bit identical in behaviour to
app/derive.py (numeric fields; `_drivers` provenance strings are exempt). This
script synthesizes statement histories spanning every branch of the derivation —
cyclicals, semi-cyclicals, CEMENT's EBITDA rebuild, asset-light high-ROIC
franchises, capital-heavy build-outs, net-worth rebuilds from equity+reserves,
financials with rising/declining ROE and dividend histories, single-year
snapshots and empty statements — runs derive_assumptions() over each, and dumps
inputs + expected outputs.

Run whenever derive.py / sector_params.py change:
    python tests/gen_derive_cases.py /path/to/equity-terminal/tests/deriveCases.json
then:
    node tests/deriveParity.mjs   (in the frontend repo)
"""
import json, random, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.derive import derive_assumptions  # noqa: E402
from app import sector_params as SP  # noqa: E402

random.seed(20260717)

NONFIN = [s for s, p in SP.SECTOR_PARAMS.items() if p.get("exit_ev_ebitda")]
FIN = ["BANK", "NBFC", "INSURANCE"]
r = random.uniform


def _years(n, end=2026):
    return list(range(end - n + 1, end + 1))


def make_nonfin_statements(i: int) -> dict:
    """Randomized multi-year statements exercising the FCFF derivation paths."""
    n = random.choice([1, 3, 4, 5, 6, 7])
    years = _years(n)
    g = r(-0.06, 0.28)                        # revenue trend
    margin = r(0.03, 0.42)
    rev0 = r(2_000, 400_000)
    use_networth = random.random() < 0.7      # else equity+reserves rebuild
    have_ebit = random.random() < 0.85        # else EBITDA-only fallback
    have_cf = random.random() < 0.6           # capex / NWC block
    heavy = random.random() < 0.3             # capital-heavy build-out
    out = {}
    for k, y in enumerate(years):
        rev = rev0 * (1 + g) ** k * r(0.96, 1.04)
        ebit = rev * margin * r(0.85, 1.15)
        ebitda = ebit + rev * r(0.02, 0.08)
        pbt = ebit * r(0.85, 1.0)
        pl = {"revenue": round(rev, 2), "ebitda": round(ebitda, 2),
              "pbt": round(pbt, 2), "tax": round(pbt * r(0.15, 0.30), 2),
              "pat": round(pbt * r(0.68, 0.80), 2),
              "depreciation": round(rev * r(0.01, 0.06), 2),
              "interest_expense": round(rev * r(0.001, 0.03), 2)}
        if have_ebit:
            pl["ebit"] = round(ebit, 2)
        nwv = rev * r(0.3, 1.2)
        bs = {"borrowings": round(rev * r(0.0, 0.6), 2),
              "receivables": round(rev * r(0.05, 0.25), 2),
              "inventory": round(rev * r(0.05, 0.30), 2),
              "payables": round(rev * r(0.04, 0.20), 2)}
        if use_networth:
            bs["net_worth"] = round(nwv, 2)
        else:
            bs["equity"] = round(nwv * 0.05, 2)
            bs["reserves"] = round(nwv * 0.95, 2)
        if random.random() < 0.5:
            bs["cash"] = round(rev * r(0.01, 0.15), 2)
        if random.random() < 0.4:
            bs["investments"] = round(rev * r(0.02, 0.30), 2)
        if random.random() < 0.3:
            bs["goodwill"] = round(rev * r(0.0, 0.12), 2)
        cf = {}
        if have_cf:
            cf["capex"] = round(-rev * (r(0.10, 0.35) if heavy else r(0.01, 0.09)), 2)
        out[y] = {"PL": pl, "BS": bs, "CF": cf}
    return out


def make_fin_statements(i: int) -> dict:
    """Randomized financial histories: rising / flat / declining ROE + payouts."""
    n = random.choice([1, 3, 4, 5])
    years = _years(n)
    roe0 = r(0.04, 0.32)
    drift = random.choice([-0.03, -0.015, 0.0, 0.01, 0.02])
    nw0 = r(3_000, 500_000)
    pay = random.random() < 0.7
    out = {}
    nw = nw0
    for k, y in enumerate(years):
        roe = max(0.01, roe0 + drift * k)
        patv = nw * roe * r(0.95, 1.05)
        bs = {"net_worth": round(nw, 2)} if random.random() < 0.8 else \
             {"equity": round(nw * 0.03, 2), "reserves": round(nw * 0.97, 2)}
        cf = {"dividends": round(-patv * r(0.05, 0.6), 2)} if pay else {}
        out[y] = {"PL": {"pat": round(patv, 2)}, "BS": bs, "CF": cf}
        nw = nw * (1 + roe * r(0.5, 0.9))
    return out


def make_case(i: int) -> dict:
    fin = i % 3 == 2
    if fin:
        vs = random.choice(FIN)
        statements = {} if i in (2, 5) else make_fin_statements(i)
    else:
        # Force branch coverage: CEMENT rebuild, cyclicals, asset-light.
        if i % 9 == 0:
            vs = "CEMENT"
        elif i % 9 == 3:
            vs = random.choice(["METAL", "ENERGY", "PAPER", "SUGAR", "TEXTILES"])
        elif i % 9 == 6:
            vs = random.choice(["IT_SERVICES", "CONSUMER", "PHARMA"])
        else:
            vs = random.choice(NONFIN)
        statements = {} if i in (0, 3) else make_nonfin_statements(i)
    exp = derive_assumptions(statements, vs, fin)
    exp = {k: v for k, v in exp.items() if k != "_drivers"}   # strings exempt
    return {"statements": statements, "vs": vs, "is_financial": fin, "exp": exp}


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/derive_cases.json"
    cases = [make_case(i) for i in range(48)]
    with open(out, "w") as f:
        json.dump(cases, f, indent=1)
    n_fin = sum(1 for c in cases if c["is_financial"])
    n_empty = sum(1 for c in cases if not c["statements"])
    print(f"Wrote {len(cases)} cases ({n_fin} financial, {n_empty} empty-statement) -> {out}")


if __name__ == "__main__":
    main()
