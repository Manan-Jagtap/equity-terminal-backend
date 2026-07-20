# Archetype Methodology-Fitness Appendix (2026-07-20)

Judged against canonical practice (Damodaran: financial-service firms, cyclicals,
young companies; institutional SOTP/EV conventions). Per archetype: what the
engine does, whether it is the right model, and what is wrong.

## Financials — banks & NBFCs (126 names: BANK 34, NBFC 92)
**Model applied:** two-stage Residual Income on cost of equity, NPA-adjusted
book, Gordon P/B + ROE-scaled sector P/E cross-checks. **Correct family** —
no FCFF/WACC on lenders (the classic category error is absent for true
financials). Regulatory capital is respected implicitly via retention-driven
book growth. Gaps: terminal ROE hard-capped at the forecast (engine cannot
value a franchise the market prices above ~2.5–3× book → BAJFINANCE −40%,
feeding VAL-02); NBFC bucket is 50% no-call because brokers/AMCs/wealth are
dumped here with no fee-annuity model (VAL-05/06); 12 mistyped names actually
run the FCFF DCF (VAL-06) — the one live category-error surface.

## Life insurers (INSURANCE, ~14 names)
EV + VNB-multiple appraisal for the seeded trio (SBILIFE/HDFCLIFE/ICICIPRULI),
justified-P/EV fallback, LICI deliberately abstained. **Right method**; inputs
are hand-seeded annual constants (VAL-08) and the VNB multiple clamp [6,15] was
observed binding at 15× for SBILIFE — the clamp, not the model, set the number.

## General insurers (4 seeded)
Combined-ratio-contextualized justified P/B on live book. Right method; tiny
coverage; same preset-staleness caveat.

## Commodity cyclicals (METAL 42, ENERGY 22, PAPER/SUGAR/TEXTILES ~30)
Through-cycle margins (5y median), growth capped at 8%, no CAP extension,
low exit multiples. **Directionally correct normalization** (matches the
benchmark), but single-cycle windows (FY21-26 contains one boom) still leave
42% of METAL at AVOID with −50%+ MoS; mid-cycle *margin* normalization exists,
mid-cycle *price/realization* assumptions do not; TATASTEEL's WACC compresses
to 8.0% via debt weight (VAL-09). Verdict-mapping asymmetry (VAL-10) turns
honest conservatism into a wall of AVOIDs.

## Semi-cyclicals (AUTO 60, CEMENT 19, AVIATION)
12% growth cap, CEMENT EBIT rebuilt from EBITDA less normalized D&A, aviation
treated fuel-cyclical. Sensible; CEMENT still 52% AVOID — the 24× exit P/E and
capex-cycle ROIC depression under-level the leaders (UltraTech −55% in June's
table; unchanged mechanism).

## Mature compounders (CONSUMER 84, CONSUMER_DISC 66, IT_SERVICES 79, PHARMA 59)
Two-stage FCFF with quality-earned CAP (to 15y), company-ROIC reinvestment,
margin mean-reversion. **Structurally the right model for the *ordinary* names,
and demonstrably wrong for the long-duration elite** — the 98-name
`LC_compounder_understated` cohort plus DMART-class AVOIDs (VAL-02). This is
the archetype where the engine most needs a new model, not new parameters.

## Young / high-growth / loss-making (~116 names across sectors)
**No model** — ROE < 4% → LOW CONF, always (engines.py:622). Honest, and the
correct benchmark model (revenue → target margin → sales-to-capital with
survival weighting) is absent (VAL-05). No terminal-as-revenue-multiple sin
exists (good); the sin is abstention-forever.

## Holdcos / conglomerates (6 SOTP presets + BAJAJHLDNG/CHOLAHLDNG/…)
SOTP presets with live share count (DAT-01 fixed), +60% divergence gate
(DAT-03), data-driven segment engine available but empty in production (KV
store unseeded). Preset EVs are FY26 hand constants (VAL-08); holdco discounts
are baked into the seed values rather than modeled explicitly; the data-driven
`segment_sotp` values operating segments at EBIT × EV/EBITDA multiple — a basis
mismatch (VAL-08). Names without presets correctly abstain.

## Fee financials — brokers, AMCs, exchanges, ratings, wealth (32 names)
**No model** — hard-gated LOW CONF (engines.py:613). Honest; needs the
fee-annuity earnings-power model (VAL-05). Several also carry the type/sector
mismatch that routes them through FCFF mechanics before the gate saves the
output (VAL-06).

## PSUs / utilities / REITs-InvITs
UTILITIES has regulated-return-ish params (β 0.75, 15× P/E) but no explicit
regulated-return framework; PSU policy/payout risk is not modeled (COALINDIA
prints +84% BUY through the gate hole — a windfall-levy/payout-policy name at
face value, VAL-01 evidence). REITs/InvITs are not separately classified at
all — any in-universe trade as MANUFACTURING/REALTY defaults. Gap to note for
the archetype roadmap; DDM cross-check partially covers high-payout PSUs.
