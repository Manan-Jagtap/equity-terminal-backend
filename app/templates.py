"""
templates.py — Sector-to-template classifier.

This module defines the 8 P&L templates the platform supports, and maps an
arbitrary sector / industry string (e.g. from yfinance, NSE, or manual entry)
to exactly one template code.

The template code determines:
  - The shape of the P&L returned by /api/companies/{ticker}/financials
  - Which metric categories are surfaced in the Ratios tab
  - The valuation engine used (RI for banks/NBFCs/insurers, FCFF for the rest)

Why 8 templates: a bank and an NBFC are both "financial" but report
fundamentally different P&Ls — a bank has CASA, an NBFC reports cost of
funds against borrowings. An IT services firm and a cement company are
both "non-financial" but the line items between revenue and EBITDA are
unrecognisably different. Eight templates is the smallest set that covers
the Nifty 500 without forcing accounting fiction.

Usage:
    from app.templates import classify, TemplateCode
    template = classify("Auto Components")   # → 'MANUFACTURING'
    template = classify("Banks - Private Sector")  # → 'BANK'
"""
from __future__ import annotations
from typing import Final


# ── Template codes ─────────────────────────────────────────────────────────
# Use strings (not an Enum) so they serialise trivially across JSON and SQL.
class TemplateCode:
    BANK          : Final = "BANK"
    NBFC          : Final = "NBFC"
    INSURANCE     : Final = "INSURANCE"
    IT_SERVICES   : Final = "IT_SERVICES"
    MANUFACTURING : Final = "MANUFACTURING"
    CONSUMER      : Final = "CONSUMER"
    PHARMA        : Final = "PHARMA"
    ENERGY        : Final = "ENERGY"

ALL_TEMPLATES = (
    TemplateCode.BANK,
    TemplateCode.NBFC,
    TemplateCode.INSURANCE,
    TemplateCode.IT_SERVICES,
    TemplateCode.MANUFACTURING,
    TemplateCode.CONSUMER,
    TemplateCode.PHARMA,
    TemplateCode.ENERGY,
)

# Whether the template uses a financial-firm P&L (Net Interest Income–based)
# or a non-financial P&L (Revenue / EBIT–based). Used by the valuation router.
FINANCIAL_TEMPLATES = {TemplateCode.BANK, TemplateCode.NBFC, TemplateCode.INSURANCE}


# ── Keyword rules ──────────────────────────────────────────────────────────
# Each rule is (keyword, template). The classifier walks the list in order
# and returns the FIRST match — so put more specific keywords above general
# ones. All keywords are matched case-insensitively as substrings.
#
# Sources of sector strings these handle:
#   - yfinance industry field (messy, US-flavoured but mostly recognisable)
#   - NSE sectoral classification (clean)
#   - Screener.in sector labels
#   - Hand-entered values in the seed data
_RULES: list[tuple[str, str]] = [

    # ── INSURANCE (must come BEFORE generic "financial") ────────────────────
    ("insurance",                   TemplateCode.INSURANCE),
    ("life insurer",                TemplateCode.INSURANCE),
    ("general insurance",           TemplateCode.INSURANCE),
    ("reinsurance",                 TemplateCode.INSURANCE),

    # ── BANK (must come BEFORE generic "financial") ─────────────────────────
    ("private sector bank",         TemplateCode.BANK),
    ("public sector bank",          TemplateCode.BANK),
    ("small finance bank",          TemplateCode.BANK),
    ("payments bank",               TemplateCode.BANK),
    ("regional bank",               TemplateCode.BANK),
    # Match plain "bank" / "banks" but not "investment bank" (those are NBFCs/cap markets)
    ("banks ",                      TemplateCode.BANK),
    (" bank",                       TemplateCode.BANK),

    # ── NBFC ────────────────────────────────────────────────────────────────
    ("nbfc",                        TemplateCode.NBFC),
    ("non-banking finance",         TemplateCode.NBFC),
    ("housing finance",             TemplateCode.NBFC),
    ("microfinance",                TemplateCode.NBFC),
    ("gold loan",                   TemplateCode.NBFC),
    ("vehicle finance",             TemplateCode.NBFC),
    ("consumer finance",            TemplateCode.NBFC),
    ("asset management",            TemplateCode.NBFC),    # AMCs (could split later)
    ("capital markets",             TemplateCode.NBFC),    # Brokers (could split later)
    ("diversified financials",      TemplateCode.NBFC),
    ("financial services",          TemplateCode.NBFC),    # Catch-all for finance
    ("credit services",             TemplateCode.NBFC),

    # ── IT_SERVICES ─────────────────────────────────────────────────────────
    ("information technology",      TemplateCode.IT_SERVICES),
    ("it services",                 TemplateCode.IT_SERVICES),
    ("it consulting",               TemplateCode.IT_SERVICES),
    ("software",                    TemplateCode.IT_SERVICES),
    ("internet",                    TemplateCode.IT_SERVICES),
    ("technology",                  TemplateCode.IT_SERVICES),
    ("computer hardware",           TemplateCode.IT_SERVICES),

    # ── PHARMA ──────────────────────────────────────────────────────────────
    ("pharmaceutical",              TemplateCode.PHARMA),
    ("pharma",                      TemplateCode.PHARMA),
    ("drug manufacturer",           TemplateCode.PHARMA),
    ("biotechnology",               TemplateCode.PHARMA),
    ("biotech",                     TemplateCode.PHARMA),
    ("medical device",              TemplateCode.PHARMA),
    ("healthcare",                  TemplateCode.PHARMA),
    ("health care",                 TemplateCode.PHARMA),
    ("hospital",                    TemplateCode.PHARMA),
    ("diagnostic",                  TemplateCode.PHARMA),

    # ── ENERGY ──────────────────────────────────────────────────────────────
    ("oil & gas",                   TemplateCode.ENERGY),
    ("oil and gas",                 TemplateCode.ENERGY),
    ("petroleum",                   TemplateCode.ENERGY),
    ("refinery",                    TemplateCode.ENERGY),
    ("power",                       TemplateCode.ENERGY),
    ("electric utilit",             TemplateCode.ENERGY),
    ("utilities",                   TemplateCode.ENERGY),
    ("renewable",                   TemplateCode.ENERGY),
    ("coal",                        TemplateCode.ENERGY),
    ("energy",                      TemplateCode.ENERGY),
    ("gas distribution",            TemplateCode.ENERGY),

    # ── CONSUMER ────────────────────────────────────────────────────────────
    ("fast moving consumer",        TemplateCode.CONSUMER),
    ("fmcg",                        TemplateCode.CONSUMER),
    ("packaged food",               TemplateCode.CONSUMER),
    ("beverages",                   TemplateCode.CONSUMER),
    ("tobacco",                     TemplateCode.CONSUMER),
    ("personal product",            TemplateCode.CONSUMER),
    ("household product",           TemplateCode.CONSUMER),
    ("consumer durable",            TemplateCode.CONSUMER),
    ("consumer goods",              TemplateCode.CONSUMER),
    ("consumer service",            TemplateCode.CONSUMER),
    ("retail",                      TemplateCode.CONSUMER),
    ("restaurant",                  TemplateCode.CONSUMER),
    ("apparel",                     TemplateCode.CONSUMER),
    ("luxury",                      TemplateCode.CONSUMER),
    ("hotel",                       TemplateCode.CONSUMER),
    ("media",                       TemplateCode.CONSUMER),
    ("entertainment",               TemplateCode.CONSUMER),
    ("leisure",                     TemplateCode.CONSUMER),

    # ── MANUFACTURING (broad catch-all for industrials) ─────────────────────
    ("automobile",                  TemplateCode.MANUFACTURING),
    ("auto component",              TemplateCode.MANUFACTURING),
    ("auto part",                   TemplateCode.MANUFACTURING),
    ("auto",                        TemplateCode.MANUFACTURING),
    ("capital goods",               TemplateCode.MANUFACTURING),
    ("industrial",                  TemplateCode.MANUFACTURING),
    ("machinery",                   TemplateCode.MANUFACTURING),
    ("electrical equipment",        TemplateCode.MANUFACTURING),
    ("aerospace",                   TemplateCode.MANUFACTURING),
    ("defence",                     TemplateCode.MANUFACTURING),
    ("defense",                     TemplateCode.MANUFACTURING),
    ("cement",                      TemplateCode.MANUFACTURING),
    ("construction material",       TemplateCode.MANUFACTURING),
    ("construction",                TemplateCode.MANUFACTURING),
    ("infrastructure",              TemplateCode.MANUFACTURING),
    ("realty",                      TemplateCode.MANUFACTURING),    # accountancy similar to mfg
    ("real estate",                 TemplateCode.MANUFACTURING),
    ("metal",                       TemplateCode.MANUFACTURING),
    ("mining",                      TemplateCode.MANUFACTURING),
    ("steel",                       TemplateCode.MANUFACTURING),
    ("aluminum",                    TemplateCode.MANUFACTURING),
    ("aluminium",                   TemplateCode.MANUFACTURING),
    ("chemical",                    TemplateCode.MANUFACTURING),
    ("fertili",                     TemplateCode.MANUFACTURING),    # fertilizer / fertiliser
    ("agrochemical",                TemplateCode.MANUFACTURING),
    ("paper",                       TemplateCode.MANUFACTURING),
    ("textile",                     TemplateCode.MANUFACTURING),
    ("rubber",                      TemplateCode.MANUFACTURING),
    ("plastic",                     TemplateCode.MANUFACTURING),
    ("glass",                       TemplateCode.MANUFACTURING),
    ("packaging",                   TemplateCode.MANUFACTURING),
    ("telecom",                     TemplateCode.MANUFACTURING),    # capex-heavy, mfg-like
    ("logistic",                    TemplateCode.MANUFACTURING),
    ("transportation",              TemplateCode.MANUFACTURING),
    ("shipping",                    TemplateCode.MANUFACTURING),
    ("aviation",                    TemplateCode.MANUFACTURING),
    ("airline",                     TemplateCode.MANUFACTURING),
    ("conglomerate",                TemplateCode.MANUFACTURING),
    ("diversified",                 TemplateCode.MANUFACTURING),
]


def classify(sector: str | None) -> str:
    """Return the template code for a given sector string.

    Matching is case-insensitive substring matching, in rule-order priority.
    Returns MANUFACTURING as a safe default if nothing matches — log that case
    so unrecognised sectors can be audited.

    Examples:
        >>> classify("Banks - Private Sector")
        'BANK'
        >>> classify("Auto Components")
        'MANUFACTURING'
        >>> classify("Pharmaceuticals")
        'PHARMA'
        >>> classify(None)
        'MANUFACTURING'
    """
    if not sector:
        return TemplateCode.MANUFACTURING

    s = sector.lower()
    for keyword, template in _RULES:
        if keyword in s:
            return template

    # Unrecognised → safe default. Caller can log this for audit.
    return TemplateCode.MANUFACTURING


def is_financial(template_code: str) -> bool:
    """True iff the template uses a financial-firm P&L (NII-based)."""
    return template_code in FINANCIAL_TEMPLATES


# ── Self-test: run `python -m app.templates` to verify the classifier ──────
if __name__ == "__main__":
    test_cases = [
        # (input,                              expected)
        ("Gold Loan NBFC",                     TemplateCode.NBFC),
        ("Diversified NBFC",                   TemplateCode.NBFC),
        ("Banks - Private Sector",             TemplateCode.BANK),
        ("Banks - Public Sector",              TemplateCode.BANK),
        ("Small Finance Bank",                 TemplateCode.BANK),
        ("Insurance - Life",                   TemplateCode.INSURANCE),
        ("General Insurance",                  TemplateCode.INSURANCE),
        ("Information Technology Services",    TemplateCode.IT_SERVICES),
        ("Software - Application",             TemplateCode.IT_SERVICES),
        ("Pharmaceuticals",                    TemplateCode.PHARMA),
        ("Drug Manufacturers - General",       TemplateCode.PHARMA),
        ("Hospitals",                          TemplateCode.PHARMA),
        ("Oil & Gas Integrated",               TemplateCode.ENERGY),
        ("Electric Utilities",                 TemplateCode.ENERGY),
        ("Renewable Energy",                   TemplateCode.ENERGY),
        ("FMCG",                               TemplateCode.CONSUMER),
        ("Consumer Durables",                  TemplateCode.CONSUMER),
        ("Apparel Retail",                     TemplateCode.CONSUMER),
        ("Restaurants",                        TemplateCode.CONSUMER),
        ("Media & Entertainment",              TemplateCode.CONSUMER),
        ("Auto Components",                    TemplateCode.MANUFACTURING),
        ("Automobiles",                        TemplateCode.MANUFACTURING),
        ("Cement",                             TemplateCode.MANUFACTURING),
        ("Steel",                              TemplateCode.MANUFACTURING),
        ("Specialty Chemicals",                TemplateCode.MANUFACTURING),
        ("Telecom Services",                   TemplateCode.MANUFACTURING),
        ("Real Estate",                        TemplateCode.MANUFACTURING),
        ("Industrial Conglomerates",           TemplateCode.MANUFACTURING),
        ("Asset Management",                   TemplateCode.NBFC),
        ("Capital Markets",                    TemplateCode.NBFC),
        ("Housing Finance",                    TemplateCode.NBFC),
        ("Financial Services",                 TemplateCode.NBFC),
        # Edge cases
        (None,                                 TemplateCode.MANUFACTURING),
        ("",                                   TemplateCode.MANUFACTURING),
        ("Something completely unknown",       TemplateCode.MANUFACTURING),
    ]

    passed = failed = 0
    for sector, expected in test_cases:
        actual = classify(sector)
        ok = actual == expected
        passed += ok
        failed += not ok
        marker = "✓" if ok else "✗"
        print(f"  {marker} {sector!r:<45s} → {actual:<14s} (expected {expected})")
    print(f"\n{passed}/{passed + failed} passed.")
