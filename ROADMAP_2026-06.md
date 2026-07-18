# EquityVerdict — Best-in-Class Roadmap (June 2026)

What it takes to compete with Bloomberg / Tickertape / Tijori, ordered by impact-per-effort.
Current state: independent blended valuation engine (DCF/RI + cross-checks, parity-tested JS port),
Nifty-50-grade ingestion (IndianAPI + BSE), screener, compare, watchlist alerts, one-pagers.

---

## Tier 1 — Accuracy & automation of the valuation engine (the moat)

1. **Sum-of-the-parts engine.** RELIANCE and ADANIENT are flagged LOW CONF by design.
   You already have `SegmentSOTP.jsx` presets — promote SOTP to a first-class backend model:
   segment revenue/EBITDA from annual-report segments → per-segment multiple or mini-DCF →
   holdco discount slider. Removes the biggest "model can't value it" hole.
2. **P/EV model for insurers.** Life insurers (SBI Life, HDFC Life, LIC) are structurally
   mispriced by RI/P-B. Ingest Embedded Value + VNB from investor presentations (they're in
   every quarterly deck) and value at justified P/EV = f(RoEV, Ke, g). Turns a permanent
   LOW CONF into a real verdict.
3. **Quarterly-driven assumption refresh.** `derive.py` uses annual statements; wire the BSE
   quarterly ingester to update TTM revenue/PAT/margins so drivers move within weeks of
   results, not a year later. Schedule: results season → re-derive → `compute_valuations.run()`.
4. **Scheduled automation on Railway.** Cron the full chain: nightly prices + `refresh_mos()`
   (already cheap), weekly full `compute_valuations`, quarterly statement re-ingest. Add a
   `/api/health/data-freshness` endpoint surfacing max staleness per dataset in the UI footer.
5. **Backtest the verdicts.** You have history; store verdict snapshots monthly and plot
   verdict-cohort forward returns (BUY vs AVOID spread). This is the single most convincing
   credibility feature a terminal can show — nobody at Tickertape/Tijori does it honestly.
6. **Per-company beta refinement.** Sector betas are fine, but offer a 2y weekly regression
   beta vs Nifty (you have OHLC) blended 50/50 with sector — Bloomberg-style adjusted beta.
7. **Scenario persistence.** DCF tab sliders are ephemeral. Let users save named scenarios
   (bull/base/bear) server-side per ticker and show them on the screener as a range band.

## Tier 2 — Coverage & data depth

8. **Universe expansion Nifty 50 → 500.** The engine generalizes; the constraint is ingestion
   QPS and parser robustness. Batch by sector, add a per-ticker data-quality report so weak
   names ship as LOW CONF instead of blocking the rollout.
9. **Shareholding-pattern history + pledge data** (BSE publishes both quarterly): promoter
   pledge is the #1 India-specific risk flag and a cheap win for the Risk pillar.
10. **Concall transcripts + guidance extraction.** You already have a Claude pipeline for
    theses; point it at earnings-call PDFs to extract guidance numbers and feed
    `rev_growth` as a cross-check on the derived driver (flag big divergences).
11. **Corporate-action engine.** Splits/bonuses are your documented top data hazard
    (Bajaj/Kotak cases). Ingest BSE corporate actions and auto-adjust per-share history;
    delete the heuristic "price diverges from seeded basis" patch once done.

## Tier 3 — Product surface

12. **Portfolio import & X-ray** (holdings upload → weighted valuation, sector tilts,
    aggregate MoS) — the stickiest retail feature Tickertape charges for.
13. **Alerts v2**: verdict transitions already exist — add MoS-threshold crossings, results
    surprises, and 52-week structure breaks; deliver by email (Railway worker).
14. **Excel/CSV export everywhere** (screener, statements, DCF schedule) — analyst table stakes.
15. **Code-split the frontend** (782 kB main chunk): lazy-load DCF tab, Compare, Ownership.
16. **Auth + saved workspaces** once portfolios/scenarios exist (Supabase/Clerk is enough).

## Engineering hygiene (carry-over from this audit)

- Remove dead modules: `app/onepager.py` (shadowed), `app/bse_results_ingester.py` (duplicate
  of `app/ingest/`), FE `FinancialStatements/Fundamentals/Technical/Verdict.jsx`, unused
  `DCFTab` block in `Company.jsx`, and the `*.bak.*` files (use git history instead).
- Reconcile FEDFINA scrip code: `544010` (ingesters) vs `544027` (`bse/scrip_codes.py`).
- `valuation.js` still contains a second, divergent DCF implementation; only
  `fundamentals/isFinancial` are live. Either delete the rest or fold it into `engine.js`
  to keep ONE model (the parity-tested one).
- Logo cache: add TTL so transient upstream failures aren't cached forever.
- Screener inner-join silently drops companies without a MarketSnapshot — make that an
  explicit "awaiting data" state instead.
- `main.py` returns traceback excerpts on 500 — gate behind an env flag before real users.
