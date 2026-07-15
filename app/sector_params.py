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

import logging
import time

log = logging.getLogger("sector_params")

# ── Market-wide constants ──────────────────────────────────────────────────
RISK_FREE: float = 0.069     # fallback; refresh_risk_free() sets the LIVE 10Y
ERP: float = 0.050           # equity risk premium (see header)

_RF_TS: float = 0.0          # last successful/attempted refresh (monotonic-ish)


def refresh_risk_free(db=None, ttl: float = 1800.0, force: bool = False) -> float:
    """Point RISK_FREE at the LIVE 10-year G-sec from the macro store (RBI
    DBIE seed + refresh overlay). Called at the top of every valuation recompute
    — batch AND on-demand (web) — so the whole model suite discounts at today's
    curve instead of a hardcoded 6.9%. TTL-cached (default 30 min) so calling it
    per request is cheap; pass force=True to bypass. Clamped to a sane band and
    left at the last value when the store is unreadable — a valuation must never
    crash on a macro hiccup — but now LOGS when a value is dropped so a units
    regression can't pin the rate silently forever."""
    global RISK_FREE, _RF_TS
    now = time.time()
    if not force and _RF_TS and (now - _RF_TS) < ttl:
        return RISK_FREE
    _RF_TS = now
    try:
        from app import macro_data
        pts = macro_data.series(db, macro_data.GSEC_10Y)
        if not pts:
            log.warning("refresh_risk_free: 10Y G-sec series empty; keeping %.4f", RISK_FREE)
            return RISK_FREE
        rf = pts[-1][1] / 100.0
        if 0.045 <= rf <= 0.10:
            RISK_FREE = round(rf, 4)
        else:
            log.warning("refresh_risk_free: 10Y G-sec %.5f out of band [0.045,0.10]; "
                        "keeping %.4f (possible units regression in the macro store)",
                        rf, RISK_FREE)
    except Exception as e:
        log.warning("refresh_risk_free: %s: %s; keeping %.4f", type(e).__name__, e, RISK_FREE)
    return RISK_FREE


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
    "CONSUMER":      _P(beta=0.58, terminal_growth=0.055, mature_roic=0.22, mature_roe=0.30, exit_pe=42, exit_ev_ebitda=28, exit_pb=None),
    "CONSUMER_DISC": _P(beta=0.80, terminal_growth=0.055, mature_roic=0.18, mature_roe=0.22, exit_pe=38, exit_ev_ebitda=22, exit_pb=None),
    "PHARMA":        _P(beta=0.64, terminal_growth=0.050, mature_roic=0.18, mature_roe=0.20, exit_pe=30, exit_ev_ebitda=18, exit_pb=None),
    "AUTO":          _P(beta=1.10, terminal_growth=0.050, mature_roic=0.16, mature_roe=0.18, exit_pe=24, exit_ev_ebitda=13, exit_pb=None),
    "METAL":         _P(beta=1.30, terminal_growth=0.040, mature_roic=0.12, mature_roe=0.13, exit_pe=11, exit_ev_ebitda=6,  exit_pb=None),
    "CEMENT":        _P(beta=1.05, terminal_growth=0.050, mature_roic=0.14, mature_roe=0.15, exit_pe=24, exit_ev_ebitda=13, exit_pb=None),
    "ENERGY":        _P(beta=1.00, terminal_growth=0.040, mature_roic=0.12, mature_roe=0.14, exit_pe=12, exit_ev_ebitda=7,  exit_pb=None),
    # Aviation — brutally fuel-cyclical, capital-heavy (aircraft), thin through-
    # cycle returns and volatile earnings, so LOW multiples. Was silently falling
    # through to MANUFACTURING and getting rich multiples + absurd upside.
    "AVIATION":      _P(beta=1.35, terminal_growth=0.050, mature_roic=0.11, mature_roe=0.14, exit_pe=12, exit_ev_ebitda=7,  exit_pb=None),
    "UTILITIES":     _P(beta=0.75, terminal_growth=0.045, mature_roic=0.11, mature_roe=0.13, exit_pe=15, exit_ev_ebitda=9,  exit_pb=None),
    "TELECOM":       _P(beta=0.90, terminal_growth=0.050, mature_roic=0.13, mature_roe=0.15, exit_pe=32, exit_ev_ebitda=9,  exit_pb=None),
    "MANUFACTURING": _P(beta=1.00, terminal_growth=0.050, mature_roic=0.15, mature_roe=0.16, exit_pe=28, exit_ev_ebitda=15, exit_pb=None),
    # Specialty + commodity chemicals / fertilizers. Higher return + growth than
    # generic manufacturing (specialty franchises), but cyclical input costs, so
    # a premium-but-not-consumer exit multiple.
    "CHEMICALS":     _P(beta=1.05, terminal_growth=0.050, mature_roic=0.16, mature_roe=0.17, exit_pe=26, exit_ev_ebitda=15, exit_pb=None),
    # Real-estate developers: rate-sensitive, cyclical, lumpy earnings (NAV-driven).
    # High beta, lower steady-state return, conservative exit multiple.
    "REALTY":        _P(beta=1.25, terminal_growth=0.050, mature_roic=0.12, mature_roe=0.13, exit_pe=20, exit_ev_ebitda=14, exit_pb=None),
    # Commodity-cyclicals that were falling through to MANUFACTURING's rich 28x
    # P/E: paper (pulp price, capex-heavy) and sugar (cane/sugar price, ethanol,
    # heavily regulated) are cheap, thin, cyclical → low multiples + through-cycle.
    "PAPER":         _P(beta=1.05, terminal_growth=0.045, mature_roic=0.11, mature_roe=0.12, exit_pe=12, exit_ev_ebitda=6,  exit_pb=None),
    "SUGAR":         _P(beta=1.00, terminal_growth=0.040, mature_roic=0.10, mature_roe=0.12, exit_pe=11, exit_ev_ebitda=6,  exit_pb=None),
    # Cables & wires / electricals: copper is largely passed through, so margins
    # ride on the value-add — smooth them through-cycle. The organised players
    # (Polycab/KEI) are quality growth industrials, so multiples stay reasonable
    # (NOT a cheap commodity); the DCF does the name-level work.
    "CABLES":        _P(beta=1.00, terminal_growth=0.055, mature_roic=0.18, mature_roe=0.18, exit_pe=27, exit_ev_ebitda=15, exit_pb=None),
    # Capital goods / engineering — order-book driven, working-capital-heavy; the
    # quality names (L&T, ABB, Siemens, Cummins) command decent multiples.
    "CAPITAL_GOODS": _P(beta=1.10, terminal_growth=0.055, mature_roic=0.17, mature_roe=0.18, exit_pe=32, exit_ev_ebitda=22, exit_pb=None),
    # Construction / infra / EPC — LOW margins, very working-capital-heavy, lumpy
    # execution → CHEAP multiples (was getting MANUFACTURING's 28x). Through-cycle.
    "CONSTRUCTION":  _P(beta=1.20, terminal_growth=0.050, mature_roic=0.13, mature_roe=0.14, exit_pe=15, exit_ev_ebitda=9,  exit_pb=None),
    # Defence — structural order-book growth, high returns, indigenisation tailwind
    # → the PSUs (HAL/BEL/BDL) sustain premium multiples.
    "DEFENCE":       _P(beta=0.95, terminal_growth=0.060, mature_roic=0.20, mature_roe=0.22, exit_pe=34, exit_ev_ebitda=24, exit_pb=None),
    # Textiles — commodity, cyclical (cotton/yarn), thin returns → cheap + through-cycle.
    "TEXTILES":      _P(beta=1.10, terminal_growth=0.040, mature_roic=0.11, mature_roe=0.12, exit_pe=13, exit_ev_ebitda=7,  exit_pb=None),
    # Logistics / transport (ex-airlines) — asset-moderate, GDP-linked.
    "LOGISTICS":     _P(beta=1.00, terminal_growth=0.055, mature_roic=0.14, mature_roe=0.15, exit_pe=26, exit_ev_ebitda=13, exit_pb=None),
    # ── Financials (RI / P-B-vs-ROE) ────────────────────────────────────────
    # Betas trimmed to align Ke with observed: India's large private banks run
    # 2-5yr betas ~0.9-1.0 and sell-side Ke ~11.5%, not the 12.2% a 1.05 beta
    # implied. NBFCs carry more cyclicality, so a smaller trim.
    "BANK":          _P(beta=0.95, terminal_growth=0.055, mature_roic=0.15, mature_roe=0.165, exit_pe=16, exit_ev_ebitda=None, exit_pb=2.4),
    "NBFC":          _P(beta=1.05, terminal_growth=0.055, mature_roic=0.16, mature_roe=0.18, exit_pe=18, exit_ev_ebitda=None, exit_pb=3.0),
    "INSURANCE":     _P(beta=0.90, terminal_growth=0.055, mature_roic=0.15, mature_roe=0.16,  exit_pe=24, exit_ev_ebitda=None, exit_pb=2.2),
}

# A safe default if classification ever misses.
DEFAULT_SECTOR = "MANUFACTURING"

# Ticker-level overrides for names the keyword/template classifier gets wrong.
# FEDFINA: "Fedbank Financial Services" trips the name-based BANK template, but
# the business is an NBFC (Federal Bank's lending subsidiary).
TICKER_OVERRIDES: dict[str, str] = {
    "FEDFINA": "NBFC",
    "INDIGO": "AVIATION",   # sector arrives blank from the vendor → override
}


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
    ("refiner", "ENERGY"), ("gas distribut", "ENERGY"), ("coal", "ENERGY"),
    ("oil well", "ENERGY"), ("oilfield", "ENERGY"), ("energy", "ENERGY"),
    # Metals / mining
    ("steel", "METAL"), ("aluminium", "METAL"), ("aluminum", "METAL"),
    ("metal", "METAL"), ("mining", "METAL"), ("zinc", "METAL"),
    # Cement / building materials
    ("cement", "CEMENT"), ("construction material", "CEMENT"), ("building material", "CEMENT"),
    # Commodity-cyclicals that must NOT get MANUFACTURING's rich multiples.
    ("paper", "PAPER"), ("newsprint", "PAPER"), ("pulp", "PAPER"),
    ("sugar", "SUGAR"),
    # Cables & wires (copper pass-through). "cable" catches "Cables"/"Wires &
    # Cables"; kept off broad "electrical equipment" (that's capital goods).
    ("cables", "CABLES"), ("wires & cables", "CABLES"), ("wire & cable", "CABLES"),
    # Chemicals / fertilizers (specialty + commodity). Placed AFTER energy so
    # "petroleum"/"refiner" still win for oil names; "petrochemical" → CHEMICALS.
    ("specialty chemical", "CHEMICALS"), ("agrochemical", "CHEMICALS"),
    ("fertiliz", "CHEMICALS"), ("fertilis", "CHEMICALS"), ("chemical", "CHEMICALS"),
    # Aviation / airlines — fuel-cyclical, capital-heavy; keep off MANUFACTURING.
    ("airline", "AVIATION"), ("aviation", "AVIATION"), ("airport", "AVIATION"),
    # Defence / aerospace — order-book, indigenisation tailwind (HAL/BEL/BDL).
    ("aerospace", "DEFENCE"), ("defence", "DEFENCE"), ("defense", "DEFENCE"),
    # Construction / infra / EPC — cheap, WC-heavy, lumpy. Building RAW MATERIALS
    # stay CEMENT (matched above); pure construction/EPC → CONSTRUCTION.
    ("construction - raw material", "CEMENT"),
    ("construction", "CONSTRUCTION"), ("engineering & construction", "CONSTRUCTION"),
    # Capital goods / engineering (order-book). AFTER construction so "constr. &
    # agric. machinery" reads as machinery only if not already construction.
    ("capital goods", "CAPITAL_GOODS"), ("agric. machinery", "CAPITAL_GOODS"),
    ("industrial machinery", "CAPITAL_GOODS"), ("machinery", "CAPITAL_GOODS"),
    ("heavy electrical equipment", "CAPITAL_GOODS"),
    # NB: "electronic instr & controls" and bare "electrical equipment" are NOT
    # mapped — the vendor applies them to auto ancillaries (Sharda Motor, LGB) and
    # consumer durables, so they stay MANUFACTURING rather than mis-bucket.
    # Textiles — commodity-cyclical.
    ("textile", "TEXTILES"),
    # Logistics / transport (ex-airlines, matched above). Placed AFTER aviation so
    # "transportation - airlines" stays AVIATION.
    ("logistic", "LOGISTICS"), ("trucking", "LOGISTICS"), ("courier", "LOGISTICS"),
    ("water transportation", "LOGISTICS"), ("transportation", "LOGISTICS"),
    # Food processing is a consumer staple; jewelry is discretionary; computer
    # services are IT; investment-services (brokers) are financials (fee-annuity
    # → the engine's fee-financial gate then reads them LOW CONF).
    ("food processing", "CONSUMER"), ("packaged food", "CONSUMER"),
    ("jewel", "CONSUMER_DISC"),
    ("computer services", "IT_SERVICES"), ("computer networks", "IT_SERVICES"),
    ("investment services", "NBFC"), ("investment banking", "NBFC"),
    # Real-estate developers
    ("real estate", "REALTY"), ("realty", "REALTY"),
    # Autos
    ("automobile", "AUTO"), ("auto component", "AUTO"), ("auto part", "AUTO"),
    ("two wheeler", "AUTO"), ("two-wheeler", "AUTO"),
    ("tyre", "AUTO"), ("tires", "AUTO"), ("auto", "AUTO"),
    # Consumer (staples vs discretionary)
    ("fast moving consumer", "CONSUMER"), ("fmcg", "CONSUMER"), ("packaged food", "CONSUMER"),
    ("beverages", "CONSUMER"), ("tobacco", "CONSUMER"), ("personal product", "CONSUMER"),
    # "household prod" catches the vendor's abbreviated "Personal & Household
    # Prods." (P&G Hygiene, Bajaj Consumer, Cupid) that "household product" missed.
    ("household prod", "CONSUMER"), ("consumer staple", "CONSUMER"),
    ("retail", "CONSUMER_DISC"), ("apparel", "CONSUMER_DISC"), ("luxury", "CONSUMER_DISC"),
    ("footwear", "CONSUMER_DISC"), ("restaurant", "CONSUMER_DISC"),
    ("hotel", "CONSUMER_DISC"), ("leisure", "CONSUMER_DISC"),
    # Media & publishing (Navneet, DB Corp) and consumer-durable appliances
    # (Orient Electric, Electronics Mart) — discretionary, were falling to MFG.
    ("media", "CONSUMER_DISC"), ("entertainment", "CONSUMER_DISC"),
    ("printing & publishing", "CONSUMER_DISC"), ("publishing", "CONSUMER_DISC"),
    ("appliance", "CONSUMER_DISC"),
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
