"""
corporate_events.py — how a stock is presented after a corporate action.

A corporate action (demerger / merger / rename / symbol change) can leave a
ticker in a state the raw pipeline treats as a data GAP or an identity
MISMATCH — either of which would otherwise surface as a broken/empty company
page or a silently-blocked ingest. This registry is the single curated source
of truth that makes those cases render honestly and stay safe.

Two modes, chosen per entry so the SAFE default (no data mapping) is explicit:

  · NOTE-ONLY  (vendor_alias=None) — a display banner + status; the pipeline
    stores NOTHING new. Used when the vendor's successor listing is a DIFFERENT
    security (a NEW ISIN) than our ticker, so no vendor payload can be
    attributed to it without contamination. Example: TATAMOTORS — the vendor's
    "Tata Motors" now resolves to the newly-listed Commercial-Vehicles entity
    (NSE TMCV, ISIN INE1TAE01010), NOT our historical TATAMOTORS listing; the
    identity guard rightly blocks it, and this banner explains the empty page.

  · VERIFIED ALIAS  (vendor_alias set) — the post-action NSE symbol the identity
    guard MAY accept for this ticker. ONLY for an ISIN-CONTINUOUS rename (same
    security, new symbol) — NEVER a new-ISIN demerger. It is an explicit
    allow-list entry that widens the guard for THIS ticker alone, so every other
    name keeps full contamination protection (the DATA-01 / VAML·VISL guard).

Nothing here is mirrored to engine.js — it's a presentation/ingest concern
upstream of the parity-locked valuation engine, free to evolve.
"""
from __future__ import annotations


# ticker → {type, effective, note, vendor_alias}. Keep entries curated and
# each one justified in a comment; an alias must be ISIN-verified before it is
# set to anything but None.
CORPORATE_EVENTS: dict[str, dict] = {
    # Demerger, NOTE-ONLY. Tata Motors Ltd demerged its Commercial Vehicles and
    # Passenger Vehicles businesses into separately listed entities (2025). The
    # vendor's "Tata Motors" is the new CV listing (NSE TMCV, ISIN INE1TAE01010,
    # BSE 544569) — a DIFFERENT security from our TATAMOTORS ticker (new ISIN,
    # new codes), so it must not be mapped here. Data stays empty by design; the
    # banner explains why rather than letting the page read as broken.
    "TATAMOTORS": {
        "type": "demerger",
        "effective": "2025",
        "vendor_alias": None,
        "note": ("Tata Motors completed a demerger (2025) separating its "
                 "Commercial Vehicles and Passenger Vehicles businesses into "
                 "separately listed entities. Standalone financials for the "
                 "post-demerger listing are pending data-provider coverage — "
                 "figures here may be limited until the provider splits the "
                 "series."),
    },
    # Merger + rename, NOTE-ONLY (the NSE symbol already equals our ticker post-
    # rename, so VENDOR_QUERY_ALIAS resolves it and the identity guard passes —
    # no bypass needed; this entry is for the display banner only).
    "GUJENERGY": {
        "type": "merger+rename",
        "effective": "2026-07-01",
        "vendor_alias": None,
        "note": ("GSPC group consolidation: GSPC, GSPL and GSPC Energy merged "
                 "into Gujarat Gas (eff. 1 May 2026), renamed Gujarat Energy "
                 "(14 May 2026); NSE symbol changed GUJGASLTD → GUJENERGY "
                 "(1 Jul 2026)."),
    },
}


def for_ticker(ticker: str) -> dict | None:
    """Public corporate-event record for a ticker, or None. Shape returned to
    the API/UI: {type, effective, note} — the internal vendor_alias is omitted."""
    ev = CORPORATE_EVENTS.get((ticker or "").upper())
    if not ev:
        return None
    return {"type": ev["type"], "effective": ev["effective"], "note": ev["note"]}


def verified_vendor_alias(ticker: str) -> str | None:
    """The identity-guard-authorised post-action NSE symbol for a ticker, or
    None. Only an ISIN-continuous rename ever carries one; a new-ISIN demerger
    stays None so the guard keeps blocking the wrong-security payload."""
    ev = CORPORATE_EVENTS.get((ticker or "").upper())
    return (ev or {}).get("vendor_alias") or None


def affected_tickers() -> set[str]:
    """All tickers with a registered corporate event (for admin/status views)."""
    return set(CORPORATE_EVENTS)
