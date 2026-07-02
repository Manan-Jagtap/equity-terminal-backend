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
    "SBILIFE":    {"ev_per_share": 805.40, "roev": 0.197},   # IEV ₹80,790cr; IEV/sh disclosed; RoEV 19.7%
    "HDFCLIFE":   {"ev_per_share": 288.8,  "roev": 0.150},   # IEV ₹62,139cr ÷ ~215.2cr sh; operating RoEV 15%
    "ICICIPRULI": {"ev_per_share": 366.7,  "roev": 0.119},   # IEV ₹52,989cr ÷ ~144.5cr sh; RoEV 11.9%
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


def pev_value(ticker: str, a: dict) -> dict | None:
    """Appraisal fair value for a life insurer: embedded value per share × a
    justified P/EV, where justified P/EV = (RoEV − g)/(Ke − g) (Gordon growth on
    embedded value), clamped to a sane band. Returns None without seeded EV."""
    d = INSURER_EV.get((ticker or "").upper())
    if not d:
        return None
    ke = (a.get("risk_free") or 0.069) + (a.get("beta") or 0.90) * (a.get("erp") or 0.05)
    g = a.get("terminal_growth") or 0.05
    roev = d["roev"]
    denom = ke - g
    justified = (roev - g) / denom if denom > 0 else _PEV_MIN
    justified = max(_PEV_MIN, min(_PEV_MAX, justified))
    intrinsic = d["ev_per_share"] * justified
    return {
        "intrinsic": intrinsic,
        "method": "P/EV Appraisal",
        "components": [
            {"label": "Embedded value / share (₹)", "value": d["ev_per_share"]},
            {"label": f"Justified P/EV ({justified:.2f}x)", "value": intrinsic},
        ],
        "note": (f"P/EV appraisal: EV/share ₹{d['ev_per_share']:.0f} × justified {justified:.2f}x "
                 f"(RoEV {roev*100:.0f}%, Ke {ke*100:.1f}%). ILLUSTRATIVE embedded value — verify."),
    }


def alternative_intrinsic(co: dict, a: dict) -> dict | None:
    """Override intrinsic for names the single-engine blend can't value:
      · life insurers (_valuation_sector == INSURANCE) → P/EV appraisal
      · conglomerates with a SOTP preset               → Sum-of-the-Parts
    Returns None (leave the engine's blended value) for everything else. MEDIUM
    confidence by design — the inputs are illustrative."""
    ticker = (co.get("ticker") or "").upper()
    if a.get("_valuation_sector") == "INSURANCE":
        return pev_value(ticker, a)
    if ticker in SOTP_PRESETS:
        return sotp_value(ticker)
    return None
