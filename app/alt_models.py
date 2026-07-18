"""
app/alt_models.py — valuation models for names the single-engine blend can't
value: conglomerate Sum-of-the-Parts and life-insurer P/EV appraisal.

These live OUTSIDE the parity-tested engine core (engines.blended / valuate ↔
src/lib/engine.js). recommend() calls alternative_intrinsic() to OVERRIDE the
intrinsic for these names; nothing here is mirrored to JavaScript, so the
bit-exact parity contract is completely untouched.

⚠️  ILLUSTRATIVE INPUTS. The IndianAPI feed provides neither segment financials
nor embedded value, so the segment EVs and embedded values below are hand-seeded
starting points. They drive a MEDIUM-confidence verdict (never HIGH), are clearly
labelled in the UI, and SHOULD BE VERIFIED against the latest disclosures before
being relied on. This is exactly how SOTP / appraisal value is done by hand — a
transparent calculator, not a fabricated precision.
"""
from __future__ import annotations


# ── Conglomerate Sum-of-the-Parts ────────────────────────────────────────────
# Segment EV and net debt in ₹ crore; shares in crore. Ported from the frontend
# SegmentSOTP presets so the backend base case and the editable panel agree.
SOTP_PRESETS: dict[str, dict] = {
    "RELIANCE": {"net_debt": 120788, "shares": 1601.78, "segments": [
        ("Jio — Digital Services", 1100000), ("Reliance Retail", 900000),
        ("O2C — Oil-to-Chemicals", 450000), ("Oil & Gas E&P + New Energy", 150000)]},
    "ADANIENT": {"net_debt": 97672, "shares": 130.2, "segments": [
        ("Adani Airports", 60000), ("Adani New Industries (ANIL)", 120000),
        ("Roads & Infrastructure", 40000), ("Data Centres (AdaniConneX)", 35000),
        ("Mining, IRM & Others", 80000)]},
    # Bajaj Finserv is a FINANCIAL HOLDCO — its value is its stakes, not a
    # consolidated-RI number, which mis-prices the look-through (exactly why banks
    # use SOTP). Segment values are the stakes NET of a ~12% holding-company
    # discount, ILLUSTRATIVE (verify vs the latest Bajaj Finance market cap +
    # insurance appraisals; editable in the SOTP panel). This is a HOLDCO, not an
    # operating bank — operating banks keep their RI (adding subs would double-count).
    "BAJAJFINSV": {"net_debt": 0, "shares": 159.5, "segments": [
        ("Bajaj Finance stake (~51%)", 220000),
        ("Bajaj Allianz Life Insurance", 33000),
        ("Bajaj Allianz General Insurance", 28000),
        ("Housing / windmill / other holdings", 17000)]},

    # ── Diversified operating conglomerates ─────────────────────────────────
    # Each below is a genuine multi-business group a single blended DCF/RI
    # mis-prices — the standard model reads only the consolidated P&L and misses
    # that the segments deserve different multiples. Segment EVs are ILLUSTRATIVE
    # (₹ crore): listed-subsidiary segments are the stake's market value (netted
    # for a holding-company discount where the group perennially trades at one),
    # operating segments are a reasonable multiple of segment earnings. VERIFY
    # against the latest disclosures — editable in the SOTP panel. MEDIUM conf.

    # L&T — operating conglomerate: E&C core + listed IT & financial-services
    # stakes. Financing debt sits INSIDE the L&T Finance stake value, so the
    # parent net_debt is ~0 for the SOTP.
    "LT": {"net_debt": 0, "shares": 137.5, "segments": [
        ("Core E&C + Hi-tech mfg + realty", 380000),
        ("LTIMindtree stake (~68%)", 92000),
        ("L&T Technology Services (~74%)", 35000),
        ("L&T Finance stake (~66%)", 28000),
        ("Hyderabad Metro / IDPL / other", 15000)]},

    # ITC — cigarettes crown jewel + FMCG-Others + paper + agri + IT + a residual
    # ~40% ITC Hotels stake (post-2025 demerger). Net cash.
    "ITC": {"net_debt": -10000, "shares": 1252.0, "segments": [
        ("Cigarettes", 400000),
        ("FMCG — Others (foods, personal care)", 70000),
        ("Paperboards & Packaging", 35000),
        ("Agri Business", 30000),
        ("ITC Infotech (IT)", 25000),
        ("ITC Hotels residual stake (~40%)", 15000)]},

    # Grasim — holdco + operating: listed UltraTech & AB Capital stakes (NET of a
    # ~20% holding-company discount, which Grasim persistently trades at) plus VSF,
    # chemicals and the ramping paints business (optionality). Standalone net debt.
    "GRASIM": {"net_debt": 15000, "shares": 68.0, "segments": [
        ("UltraTech Cement stake (~55%, net of holdco disc.)", 145000),
        ("Aditya Birla Capital stake (~50%, net of disc.)", 21000),
        ("Viscose Staple Fibre (VSF)", 30000),
        ("Chemicals (chlor-alkali)", 18000),
        ("Paints — Birla Opus (ramp-up optionality)", 22000),
        ("B2B e-commerce / other", 4000)]},

    # Vedanta — diversified resources: listed Hindustan Zinc stake + aluminium,
    # oil & gas (Cairn), power and iron ore. Carries real parent net debt.
    "VEDL": {"net_debt": 60000, "shares": 391.0, "segments": [
        ("Hindustan Zinc stake (~63%)", 120000),
        ("Aluminium (Vedanta Alu + BALCO)", 90000),
        ("Oil & Gas — Cairn", 35000),
        ("Power", 15000),
        ("Iron ore / steel / ferro / other", 25000)]},

    # Bajaj Holdings — pure investment holdco of the Bajaj Auto & Bajaj Finserv
    # stakes; it perennially trades at a DEEP (~40%+) holding-company discount, so
    # the stakes are seeded NET of that discount (else SOTP badly overstates it).
    "BAJAJHLDNG": {"net_debt": 0, "shares": 11.13, "segments": [
        ("Bajaj Auto stake (~35%, net of holdco disc.)", 50000),
        ("Bajaj Finserv stake (~41%, net of holdco disc.)", 74000),
        ("Other quoted/unquoted investments + cash", 15000)]},

    # Godrej Industries — operating conglomerate: listed Godrej Consumer, Godrej
    # Properties and Godrej Agrovet stakes (NET of a ~40% holdco discount it
    # persistently trades at) + chemicals + the Godrej Capital lending arm.
    "GODREJIND": {"net_debt": 8000, "shares": 33.7, "segments": [
        ("Godrej Consumer stake (~23%, net of disc.)", 17000),
        ("Godrej Properties stake (~47%, net of disc.)", 18000),
        ("Godrej Agrovet stake (~58%, net of disc.)", 5000),
        ("Chemicals + Godrej Capital + Vikhroli land", 18000)]},

    # Aditya Birla Capital — a diversified FINANCIAL conglomerate (a consolidated
    # RI/book number mis-prices the look-through, like other holdcos): NBFC lending
    # book + housing finance + the listed ABSL AMC stake + life & health insurance
    # + broking. Segment values NET of a ~20% holding-company discount. ILLUSTRATIVE.
    "ABCAPITAL": {"net_debt": 0, "shares": 260.0, "segments": [
        ("Aditya Birla Finance (NBFC book)", 42000),
        ("Housing finance", 7000),
        ("ABSL AMC stake (~46%, net of disc.)", 9000),
        ("Life insurance (ABSLI)", 12000),
        ("Health insurance + broking / other", 6000)]},

    # ONGC — upstream E&P: a growth-DCF mis-prices a depleting-reserve cyclical.
    # Sell-side convention (institutional upstream models): core business =
    # through-cycle standalone + OVL (overseas E&P arm) earnings × a LOW ~7x
    # multiple (commodity earnings deserve no growth premium; realization is
    # capped by windfall levies on the upside and floored by APM gas on the
    # downside), PLUS the listed downstream/gas stakes (HPCL ~54.9%, MRPL ~71.6%,
    # IOC ~14.2%, GAIL + Petronet LNG) at market value NET of a ~30% holding-
    # company discount. ILLUSTRATIVE values in ₹ cr — verify against latest
    # stake market values and through-cycle PAT.
    "ONGC": {"net_debt": 30000, "shares": 1258.0, "segments": [
        ("Core E&P (standalone + OVL, ~7x through-cycle PAT)", 280000),
        ("HPCL stake (~54.9%, net of 30% disc.)", 40000),
        ("IOC stake (~14.2%, net of disc.)", 19000),
        ("MRPL stake (~71.6%, net of disc.)", 11000),
        ("GAIL + Petronet LNG + other investments (net of disc.)", 9000)]},
}


def sotp_value(ticker: str) -> dict | None:
    """Per-share Sum-of-the-Parts fair value: Σ segment EV − net debt, ÷ shares.
    Returns None for names without a preset."""
    p = SOTP_PRESETS.get((ticker or "").upper())
    if not p or not p.get("shares"):
        return None
    total_ev = sum(ev for _, ev in p["segments"])
    equity = total_ev - (p.get("net_debt") or 0)
    if equity <= 0:
        return None
    per_share = equity / p["shares"]
    return {
        "intrinsic": per_share,
        "method": "Sum-of-the-Parts",
        "components": [{"label": n, "value": float(ev)} for n, ev in p["segments"]],
        "note": ("Sum-of-the-parts on ILLUSTRATIVE segment enterprise values "
                 "(editable in the DCF tab) — not ingested from financials; verify each segment."),
    }


# ── Life-insurer P/EV appraisal ──────────────────────────────────────────────
# ev_per_share = Indian Embedded Value ÷ shares (₹/share); roev = (operating)
# return on EV. FY26 figures (year ended 31 Mar 2026) — see CHANGES_2026-07.md
# for sources. Re-check each year when the insurers report; EV is a point-in-time
# actuarial number sensitive to market moves.
INSURER_EV: dict[str, dict] = {
    # vnb_per_share = latest-year Value of New Business ÷ shares; vnb_growth =
    # expected medium-term VNB CAGR (APE growth × margin drift — institutional
    # models run ~8–12% for the listed trio). Powers the appraisal-value leg.
    "SBILIFE":    {"ev_per_share": 805.40, "roev": 0.197, "vnb_per_share": 59.5, "vnb_growth": 0.11},   # IEV ₹80,790cr; VNB ~₹5,954cr ÷ ~100.1cr sh
    "HDFCLIFE":   {"ev_per_share": 288.8,  "roev": 0.150, "vnb_per_share": 18.4, "vnb_growth": 0.10},   # IEV ₹62,139cr; VNB ~₹3,960cr ÷ ~215.2cr sh
    "ICICIPRULI": {"ev_per_share": 366.7,  "roev": 0.119, "vnb_per_share": 16.5, "vnb_growth": 0.08},   # IEV ₹52,989cr; VNB ~₹2,390cr ÷ ~144.5cr sh
    # LICI intentionally OMITTED → stays LOW CONF. Its reported IEV (~₹7.9L cr)
    # materially overstates distributable shareholder value (90:10 participating-
    # surplus structure — most surplus accrues to policyholders), and a FY26 bonus
    # issue muddies per-share figures. A naive P/EV would badly mislead; LIC needs a
    # bespoke appraisal that splits shareholder vs policyholder value.
}

# Band for the justified price-to-embedded-value multiple (Indian listed life
# insurers have historically traded ~1.5–3x EV; clamp keeps a stray Ke/g out of
# absurd territory).
_PEV_MIN, _PEV_MAX = 1.0, 3.0


def _vnb_multiple(ke: float, g1: float, years: int = 10, g_term: float = 0.05) -> float:
    """Structural-value multiple: PV of a VNB stream growing at g1 for `years`,
    then at g_term forever, per ₹1 of current VNB. This is the 'VNB multiple'
    institutional life-insurance models apply (typically lands ~10–15x for the
    listed Indian franchises). Clamped to [6, 15] so a stray Ke/g pairing can't
    run the appraisal to absurdity."""
    m, v, df = 0.0, 1.0, 1.0
    for _ in range(years):
        v *= (1 + g1)
        df /= (1 + ke)
        m += v * df
    if ke > g_term:
        m += v * (1 + g_term) / (ke - g_term) * df
    return max(6.0, min(15.0, m))


def pev_value(ticker: str, a: dict) -> dict | None:
    """Appraisal fair value for a life insurer (the institutional two-piece):

        AV/share = Embedded Value/share  +  VNB/share × VNB multiple

    The first piece is the in-force book (what the actuary already counted);
    the second is the franchise — the discounted stream of future new-business
    value. Without a VNB seed, falls back to the justified-P/EV Gordon form
    ((RoEV − g)/(Ke − g) × EV). Either way the implied P/EV is clamped to the
    historical listed band. Returns None without seeded EV."""
    d = INSURER_EV.get((ticker or "").upper())
    if not d:
        return None
    ke = (a.get("risk_free") or 0.069) + (a.get("beta") or 0.90) * (a.get("erp") or 0.05)
    g = a.get("terminal_growth") or 0.05
    ev_ps, roev = d["ev_per_share"], d["roev"]

    vnb_ps = d.get("vnb_per_share")
    if vnb_ps:
        mult = _vnb_multiple(ke, d.get("vnb_growth", 0.10), g_term=g)
        structural = vnb_ps * mult
        intrinsic = ev_ps + structural
        # Keep the appraisal inside the observed P/EV band — the franchise leg
        # must augment the actuarial book, not detach from it.
        intrinsic = max(_PEV_MIN * ev_ps, min(_PEV_MAX * ev_ps, intrinsic))
        implied = intrinsic / ev_ps
        return {
            "intrinsic": intrinsic,
            "method": "EV + VNB Appraisal",
            "components": [
                {"label": "Embedded value / share (₹)", "value": ev_ps},
                {"label": f"Structural value: VNB ₹{vnb_ps:.0f} × {mult:.1f}x", "value": structural},
            ],
            "note": (f"Appraisal value: EV ₹{ev_ps:.0f} + VNB ₹{vnb_ps:.0f}/sh × {mult:.1f}x "
                     f"(VNB growth {d.get('vnb_growth', 0.10)*100:.0f}%, Ke {ke*100:.1f}%) → implied "
                     f"P/EV {implied:.2f}x (RoEV {roev*100:.0f}%). ILLUSTRATIVE EV/VNB — verify."),
        }

    denom = ke - g
    justified = (roev - g) / denom if denom > 0 else _PEV_MIN
    justified = max(_PEV_MIN, min(_PEV_MAX, justified))
    intrinsic = ev_ps * justified
    return {
        "intrinsic": intrinsic,
        "method": "P/EV Appraisal",
        "components": [
            {"label": "Embedded value / share (₹)", "value": ev_ps},
            {"label": f"Justified P/EV ({justified:.2f}x)", "value": intrinsic},
        ],
        "note": (f"P/EV appraisal: EV/share ₹{ev_ps:.0f} × justified {justified:.2f}x "
                 f"(RoEV {roev*100:.0f}%, Ke {ke*100:.1f}%). ILLUSTRATIVE embedded value — verify."),
    }


def alternative_intrinsic(co: dict, a: dict) -> dict | None:
    """Override intrinsic for names the single-engine blend can't value:
      · life insurers (_valuation_sector == INSURANCE) → P/EV appraisal
      · conglomerates with a SOTP preset               → Sum-of-the-Parts
    Returns None (leave the engine's blended value) for everything else. MEDIUM
    confidence by design — the inputs are illustrative."""
    # A DATA-DRIVEN segment SOTP (reported segment EBIT × sector multiples,
    # extracted from filings) OVERRIDES the illustrative preset when present.
    seg = a.get("_segment_sotp")
    if isinstance(seg, dict) and (seg.get("intrinsic") or 0) > 0:
        return seg
    ticker = (co.get("ticker") or "").upper()
    if a.get("_valuation_sector") == "INSURANCE":
        return pev_value(ticker, a)
    if ticker in SOTP_PRESETS:
        return sotp_value(ticker)
    return None
