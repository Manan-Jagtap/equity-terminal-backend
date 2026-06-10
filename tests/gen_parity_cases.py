"""
tests/gen_parity_cases.py — regenerate the node-vs-python parity snapshot.

The frontend's lib/engine.js must stay bit-for-bit identical in behaviour to
app/engines.py. This script runs engines.blended() over 60 randomized cases
(financial + non-financial, every sector, fade horizons 6-15 so both growth
stages are exercised) and dumps inputs + expected outputs.

Run whenever engines.py / sector_params.py change:
    python tests/gen_parity_cases.py /path/to/equity-terminal/tests/parityCases.json
then:
    node tests/engineParity.mjs   (in the frontend repo)
"""
import json, random, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import engines  # noqa: E402
from app import sector_params as SP  # noqa: E402

random.seed(20260611)

NONFIN = [s for s, p in SP.SECTOR_PARAMS.items() if p.get("exit_ev_ebitda")]
FIN = ["BANK", "NBFC", "INSURANCE"]


def make_case(i: int) -> dict:
    fin = i % 3 == 2
    r = random.uniform
    if fin:
        vs = random.choice(FIN)
        co = {
            "type": "financial",
            "equity": round(r(2_000, 600_000), 2),
            "shares": round(r(20, 1_600), 2),
            "net_profit": round(r(300, 90_000), 2),
            "revenue": None,
            "net_debt": None,
            "price": round(r(80, 12_000), 2),
        }
        a = {
            "risk_free": round(r(0.06, 0.08), 4),
            "beta": round(r(0.7, 1.3), 3),
            "erp": round(r(0.04, 0.07), 4),
            "payout": round(r(0.0, 0.6), 3),
            "fade_years": random.randint(6, 15),
            "forecast_roe": round(r(0.06, 0.28), 4),
            "terminal_roe": round(r(0.10, 0.22), 4),
            "terminal_growth": round(r(0.04, 0.055), 4),
            "debt_weight": 0.2, "cost_debt": 0.085, "tax_rate": 0.25,
            "rev_growth": 0.10, "ebit_margin": 0.12, "reinvest_rate": 0.35,
            "_valuation_sector": vs,
        }
    else:
        vs = random.choice(NONFIN)
        co = {
            "type": "nonfinancial",
            "equity": round(r(2_000, 900_000), 2),
            "shares": round(r(15, 1_600), 2),
            "net_profit": round(r(-2_000, 95_000), 2),
            "revenue": round(r(3_000, 1_100_000), 2),
            "net_debt": round(r(-60_000, 250_000), 2),
            "price": round(r(80, 14_000), 2),
        }
        a = {
            "risk_free": round(r(0.06, 0.08), 4),
            "beta": round(r(0.6, 1.4), 3),
            "erp": round(r(0.04, 0.07), 4),
            "payout": round(r(0.1, 0.6), 3),
            "fade_years": random.randint(6, 15),
            "forecast_roe": round(r(0.08, 0.25), 4),
            "terminal_roe": round(r(0.10, 0.25), 4),
            "terminal_growth": round(r(0.04, 0.055), 4),
            "debt_weight": round(r(0.0, 0.6), 3),
            "cost_debt": round(r(0.06, 0.12), 4),
            "tax_rate": round(r(0.15, 0.32), 4),
            "rev_growth": round(r(0.02, 0.20), 4),
            "ebit_margin": round(r(0.04, 0.40), 4),
            "reinvest_rate": round(r(0.10, 0.80), 4),
            "_valuation_sector": vs,
        }
    b = engines.blended(co, a)
    return {
        "co": co, "a": a,
        "exp_blended": b["blended"],
        "exp_primary": b["primary"],
        "exp_components": [c["value"] for c in b["components"]],
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/parity_cases.json"
    cases = [make_case(i) for i in range(60)]
    with open(out, "w") as f:
        json.dump(cases, f, indent=1)
    n_fin = sum(1 for c in cases if c["co"]["type"] == "financial")
    print(f"Wrote {len(cases)} cases ({n_fin} financial) -> {out}")


if __name__ == "__main__":
    main()
