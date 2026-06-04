"""
app/onepager/brand.py — per-company brand colour + display metadata.

brand_color drives the entire one-pager theme (masthead rule, accents, KPI
highlights, peer-row highlight). Colours are approximations of each company's
primary brand colour, picked to read well as a print accent on warm paper.

subsector is a short descriptor shown under the ticker.
"""

BRAND = {
    "MUTHOOTFIN": {"color": "#8A1B2E", "subsector": "Gold Loan Financier"},
    "IIFL":       {"color": "#0B4DA2", "subsector": "Diversified Lending (Gold, Home, MFI)"},
    "FEDFINA":    {"color": "#13235B", "subsector": "Diversified NBFC (LAP, Gold, MSME)"},
    "MANAPPURAM": {"color": "#C8102E", "subsector": "Gold Loan Financier"},
    "BAJFINANCE": {"color": "#00355F", "subsector": "Diversified Retail NBFC"},
    "SHRIRAMFIN": {"color": "#C8102E", "subsector": "Vehicle & MSME Financier"},
    "CHOLAFIN":   {"color": "#004B8D", "subsector": "Vehicle & Home Financier"},
    "SBFC":       {"color": "#E03A3E", "subsector": "Secured MSME Lender"},
    "POONAWALLA": {"color": "#5B2A86", "subsector": "Diversified NBFC"},
    "LTF":        {"color": "#0072BC", "subsector": "Diversified Retail NBFC"},
    "ABCAPITAL":  {"color": "#C8102E", "subsector": "Diversified Financial Services"},
}

_DEFAULT = {"color": "#1F3A5F", "subsector": "NBFC"}


def brand_for(ticker: str) -> dict:
    return BRAND.get(ticker.upper(), _DEFAULT)
