"""
app/sector_params.py — defensible India valuation parameters by sector.

WHY THIS EXISTS
---------------
The 8 P&L templates (templates.py) are the right granularity for *statement
shape*, but far too coarse for *valuation*: autos, metals, cement, telecom and
utilities all collapse into MANUFACTURING, yet they trade at wildly different
multiples and carry very different risk. Valuation must not inherit that
coarseness.

So we keep the 8 templates for P&L/ratios, and add a finer `valuation_sector`
used ONLY to drive: cost of equity (sector beta), terminal growth, mature
return assumptions, and exit-multiple sanity checks.

NUMBERS — sourcing & intent
---------------------------
- Risk-free  : India 10Y G-Sec, ~6.9% nominal (mid-2025/26).
- ERP        : ~5.0%. We deliberately use the nominal G-Sec as Rf and a ~5% ERP
               (rather than a real-rate + total-country-ERP build-up) so we do
               not double-count India country risk. This yields Ke ≈ 11–13%,
               in line with how the street discounts large Indian compounders
               (e.g. AlphaSpread's TCS Ke ≈ 10.7%).
- Betas      : levered sector betas, India, rounded to defensible values
               (Damodaran India dataset + observed 2–5yr betas). They are NOT
               per-company precise — they encode sector risk, which is the point
               of a transparent, reproducible model.
- Terminal g : capped well below Ke; anchored to long-run nominal GDP (~5–5.5%),
               trimmed down for mature/cyclical sectors.
- Mature ROE / ROIC : the return the business fades TO in steady state.
- Exit multiples : used for a cross-check / sanity band, never to anchor the DCF.

These are intentionally editable in ONE place. When you refine a number, every
valuation updates and stays consistent.
"""
from __future__ import annotations

# ── Market-wide constants ──────────────────────────────────────────────────
RISK_FREE: float = 0.069     # India 10Y G-Sec (nominal)
ERP: float = 0.050           # equity risk premium (see header)


def cost_of_equity(beta: float) -> float:
    """CAPM cost of equity from a sector beta."""
    return RISK_FREE + beta * ERP


# ── Valuation-sector parameter table ───────────────────────────────────────
# Each entry:
#   beta              levered sector beta  → cost of equity
#   terminal_growth   perpetuity growth (must be < Ke)
#   mature_roe        steady-state ROE the business fades to (financials & RI)
#   mature_roic       steady-state ROIC (non-financials, reinvestment math)
#   exit_pe           sanity P/E on terminal earnings
#   exit_ev_ebitda    sanity EV/EBITDA on terminal EBITDA
#   exit_pb           sanity P/B (financials)
_P = dict
SECTOR_PARAMS: dict[str, dict] = {
    # ── Non-financials ──────────────────────────────────────────────────────
    "IT_SERVICES":   _P(beta=0.85, terminal_growth=0.055, mature_roic=0.30, mature_roe=0.30, exit_pe=25, exit_ev_ebitda=16, exit_pb=None),
    "CONSUMER":      _P(beta=0.72, terminal_growth=0.055, mature_roic=0.22, mature_roe=0.30, exit_pe=42, exit_ev_ebitda=28, exit_pb=None),
    "CONSUMER_DISC": _P(beta=0.95, terminal_growth=0.055, mature_roic=0.18, mature_roe=0.22, exit_pe=38, exit_ev_ebitda=22, exit_pb=None),
    "PHARMA":        _P(beta=0.78, terminal_growth=0.050, mature_roic=0.18, mature_roe=0.20, exit_pe=30, exit_ev_ebitda=18, exit_pb=None),
    "AUTO":          _P(beta=1.10, terminal_growth=0.050, mature_roic=0.16, mature_roe=0.18, exit_pe=24, exit_ev_ebitda=13, exit_pb=None),
    "METAL":         _P(beta=1.30, terminal_growth=0.040, mature_roic=0.12, mature_roe=0.13, exit_pe=11, exit_ev_ebitda=6,  exit_pb=None),
    "CEMENT":        _P(beta=1.05, terminal_growth=0.050, mature_roic=0.14, mature_roe=0.15, exit_pe=24, exit_ev_ebitda=13, exit_pb=None),
    "ENERGY":        _P(beta=1.00, terminal_growth=0.040, mature_roic=0.12, mature_roe=0.14, exit_pe=12, exit_ev_ebitda=7,  exit_pb=None),
    "UTILITIES":     _P(beta=0.75, terminal_growth=0.045, mature_roic=0.11, mature_roe=0.13, exit_pe=15, exit_ev_ebitda=9,  exit_pb=None),
    "TELECOM":       _P(beta=0.90, terminal_growth=0.050, mature_roic=0.13, mature_roe=0.15, exit_pe=32, exit_ev_ebitda=9,  exit_pb=None),
    "MANUFACTURING": _P(beta=1.00, terminal_growth=0.050, mature_roic=0.15, mature_roe=0.16, exit_pe=28, exit_ev_ebitda=15, exit_pb=None),
    # ── Financials (RI / P-B-vs-ROE) ────────────────────────────────────────
    "BANK":          _P(beta=1.05, terminal_growth=0.055, mature_roic=0.15, mature_roe=0.155, exit_pe=16, exit_ev_ebitda=None, exit_pb=2.4),
    "NBFC":          _P(beta=1.20, terminal_growth=0.055, mature_roic=0.16, mature_roe=0.165, exit_pe=18, exit_ev_ebitda=None, exit_pb=3.0),
    "INSURANCE":     _P(beta=0.90, terminal_growth=0.055, mature_roic=0.15, mature_roe=0.16,  exit_pe=24, exit_ev_ebitda=None, exit_pb=2.2),
}

# A safe default if classification ever misses.
DEFAULT_SECTOR = "MANUFACTURING"


def params(valuation_sector: str | None) -> dict:
    return SECTOR_PARAMS.get(valuation_sector or "", SECTOR_PARAMS[DEFAULT_SECTOR])


# ── Classifier: sector string (+ template) → valuation_sector ───────────────
# Walks in order, first substring match wins. More specific keywords first.
# Falls back to the P&L template, then MANUFACTURING.
_RULES: list[tuple[str, str]] = [
    # Financials
    ("insurance", "INSURANCE"), ("reinsur", "INSURANCE"), ("life insurer", "INSURANCE"),
    ("private sector bank", "BANK"), ("public sector bank", "BANK"),
    ("small finance bank", "BANK"), ("payments bank", "BANK"),
    ("banks ", "BANK"), (" bank", "BANK"),
    ("housing finance", "NBFC"), ("gold loan", "NBFC"), ("microfinance", "NBFC"),
    ("vehicle finance", "NBFC"), ("consumer finance", "NBFC"), ("nbfc", "NBFC"),
    ("asset management", "NBFC"), ("capital markets", "NBFC"),
    ("non-banking finance", "NBFC"), ("financial services", "NBFC"),
    ("diversified financ", "NBFC"), ("credit", "NBFC"), ("financ", "NBFC"),
    # IT
    ("information technology", "IT_SERVICES"), ("it services", "IT_SERVICES"),
    ("it consulting", "IT_SERVICES"), ("software", "IT_SERVICES"),
    ("internet", "IT_SERVICES"), ("technology", "IT_SERVICES"),
    # Pharma / healthcare
    ("pharmaceutical", "PHARMA"), ("pharma", "PHARMA"), ("drug manufacturer", "PHARMA"),
    ("biotech", "PHARMA"), ("hospital", "PHARMA"), ("healthcare", "PHARMA"),
    ("health care", "PHARMA"), ("diagnostic", "PHARMA"), ("medical", "PHARMA"),
    # Telecom
    ("telecom", "TELECOM"), ("communication", "TELECOM"), ("wireless", "TELECOM"),
    # Utilities / power
    ("electric utilit", "UTILITIES"), ("utilities", "UTILITIES"), ("power", "UTILITIES"),
    ("renewable", "UTILITIES"),
    # Energy / oil & gas / coal. NOTE: the IndianAPI sector string is
    # "Oil Gas & Consumable Fuels" — without an ampersand between oil and gas —
    # so the plain "oil gas" / "consumable fuels" keys are what actually match
    # ONGC, Coal India, BPCL etc. (they were silently falling through to
    # MANUFACTURING, which gave them rich multiples and absurd +100% upside).
    ("oil gas", "ENERGY"), ("consumable fuels", "ENERGY"),
    ("oil & gas", "ENERGY"), ("oil and gas", "ENERGY"), ("petroleum", "ENERGY"),
    ("refiner", "ENERGY"), ("gas distribut", "ENERGY"), ("coal", "ENERGY"), ("energy", "ENERGY"),
    # Metals / mining
    ("steel", "METAL"), ("aluminium", "METAL"), ("aluminum", "METAL"),
    ("metal", "METAL"), ("mining", "METAL"), ("zinc", "METAL"),
    # Cement / building materials
    ("cement", "CEMENT"), ("construction material", "CEMENT"), ("building material", "CEMENT"),
    # Autos
    ("automobile", "AUTO"), ("auto component", "AUTO"), ("auto part", "AUTO"),
    ("two wheeler", "AUTO"), ("two-wheeler", "AUTO"), ("auto", "AUTO"),
    # Consumer (staples vs discretionary)
    ("fast moving consumer", "CONSUMER"), ("fmcg", "CONSUMER"), ("packaged food", "CONSUMER"),
    ("beverages", "CONSUMER"), ("tobacco", "CONSUMER"), ("personal product", "CONSUMER"),
    ("household product", "CONSUMER"), ("consumer staple", "CONSUMER"),
    ("retail", "CONSUMER_DISC"), ("apparel", "CONSUMER_DISC"), ("luxury", "CONSUMER_DISC"),
    ("restaurant", "CONSUMER_DISC"), ("hotel", "CONSUMER_DISC"), ("leisure", "CONSUMER_DISC"),
    ("media", "CONSUMER_DISC"), ("entertainment", "CONSUMER_DISC"),
    ("consumer durable", "CONSUMER_DISC"), ("consumer discretion", "CONSUMER_DISC"),
    ("jewell", "CONSUMER_DISC"), ("consumer", "CONSUMER"),
]

# When we only have the P&L template code, map it to a valuation_sector.
_TEMPLATE_FALLBACK = {
    "BANK": "BANK", "NBFC": "NBFC", "INSURANCE": "INSURANCE",
    "IT_SERVICES": "IT_SERVICES", "PHARMA": "PHARMA", "ENERGY": "ENERGY",
    "CONSUMER": "CONSUMER", "MANUFACTURING": "MANUFACTURING",
}


def classify_valuation_sector(sector: str | None, template_code: str | None = None) -> str:
    """Map a free-text sector string to a valuation_sector.
    An explicit BANK/INSURANCE template (which is name-derived and unambiguous)
    wins over the generic sector string, so a bank carrying the sector
    'Financial Services' isn't mis-priced as an NBFC. Otherwise keyword match,
    then template fallback, then MANUFACTURING."""
    if template_code in ("BANK", "INSURANCE"):
        return template_code
    s = (sector or "").lower()
    for kw, code in _RULES:
        if kw in s:
            return code
    if template_code and template_code in _TEMPLATE_FALLBACK:
        return _TEMPLATE_FALLBACK[template_code]
    return DEFAULT_SECTOR


if __name__ == "__main__":
    tests = [
        ("Information Technology", None, "IT_SERVICES"),
        ("Automobiles", "MANUFACTURING", "AUTO"),
        ("Steel", "MANUFACTURING", "METAL"),
        ("Cement", "MANUFACTURING", "CEMENT"),
        ("Telecom Services", "MANUFACTURING", "TELECOM"),
        ("Power Generation", "ENERGY", "UTILITIES"),
        ("Oil & Gas Refining", "ENERGY", "ENERGY"),
        ("FMCG", "CONSUMER", "CONSUMER"),
        ("Apparel Retail", "CONSUMER", "CONSUMER_DISC"),
        ("Banks - Private Sector", "BANK", "BANK"),
        ("Financial Services", "NBFC", "NBFC"),
        ("Life Insurance", "INSURANCE", "INSURANCE"),
        ("Gold Loan NBFC", "NBFC", "NBFC"),
    ]
    ok = 0
    for sector, tmpl, exp in tests:
        got = classify_valuation_sector(sector, tmpl)
        flag = "✓" if got == exp else "✗"
        ok += got == exp
        ke = cost_of_equity(params(got)["beta"])
        print(f"  {flag} {sector:<28} → {got:<14} Ke={ke*100:.1f}%  (exp {exp})")
    print(f"\n{ok}/{len(tests)} passed.")
