# Fund Manager Engine — Improvement Checklist

The running scorecard for making this the most honest, evidence-disciplined
fund-manager engine available. Every item is either **DONE** (in production),
**NEXT** (buildable with data we already have), or **NEEDS DATA** (blocked on a
source we don't ingest yet — with the source named). Updated as the engine
evolves; treat it as the engine's own product roadmap.

Doctrine (non-negotiable): educational decision support, never SEBI-registered
advice. When evidence is thin the engine says NO CALL / low conviction — it
never manufactures certainty. Honesty is the moat.

---

## 1. Valuation — never trust one model

- [x] **DONE — Triangulation.** Model fair value is cross-examined against the
      analyst consensus target and the name's own 5-year trailing-P/E band.
      A model outvoted by both witnesses is SET ASIDE and the action says so.
- [x] **DONE — Structural suspect gates.** Fair value ≤ 0, MoS ≤ −75%,
      MoS ≥ +300%, or self-reported LOW confidence → the model doesn't vote.
- [x] **DONE — Model self-trust.** The model's own VerdictSnapshot ledger is
      scored (BUY calls vs universe median, 6-month forward) per valuation
      sector; its vote is weighted by its realized hit-rate.
- [x] **DONE — P/B band** alongside P/E (own 5-yr book-multiple history);
      EV/EBITDA deferred until the debt-bridge lines are audited.
- [x] **DONE — Peer-relative valuation**: the name's P/E percentile within
      its sector's current distribution, blended into the band witness.
- [ ] **NEEDS DATA — Forward multiples** (consensus FY+1 EPS): vendor
      forecasts blob has partial coverage; audit coverage before wiring.

## 2. Fundamental health & forensic discipline

- [x] **DONE — Forensics feed conviction.** Adapted Piotroski F, Sloan
      accruals, cash conversion, interest coverage, net-debt/EBITDA, leverage
      trend → composite + red flags; red flags cut ADD conviction and raise
      TRIM conviction. A juicy MoS can no longer outrank a bad balance sheet.
- [x] **DONE — Beneish M-score + Altman Z''** (already computed in
      forensics; their red flags flow into conviction with the rest).
- [ ] **NEEDS DATA — Promoter pledge %** (NSE/BSE disclosures; not in either
      vendor today). The single most predictive Indian-market red flag we lack.
- [ ] **NEEDS DATA — Auditor changes / qualifications** (annual-report
      parsing; QuarterlyDocument store has the PDFs — needs an extractor).
- [ ] **NEEDS DATA — Related-party transaction trends** (annual report notes).

## 3. Flow, results & the Street

- [x] **DONE — Institutional/promoter deltas** (quarterly shareholding) and
      **results momentum** (PAT YoY, FY EPS surprise vs Street) vote on every
      action, both directions.
- [x] **DONE — Analyst view in conviction** (was display-only before):
      consensus upside is a first-class valuation witness.
- [x] **DONE — Estimate-revision momentum** (`catalyst`) votes on every
      action with its own weight.
- [ ] **NEEDS DATA — Bulk/block deals & insider trades** (NSE disclosures).
- [ ] **NEEDS DATA — FII/DII daily flows** (NSDL/exchange aggregates) for the
      macro block.

## 4. Momentum & technicals

- [x] **DONE — 12-1 momentum percentile + 50/200-DMA states** vote on entries
      and exits (entry patience below the 200-DMA, trend confirmation above).
- [x] **DONE — 52-week-high proximity + volume-surge confirmation** vote
      on entries.
- [x] **DONE — Relative strength vs sector** (12-1 vs the sector median)
      votes both directions.

## 5. Macro regime

- [x] **DONE — Regime read from our own data**: universe breadth (% above
      200/50-DMA), Nifty trend from the Dhan index series, sector relative
      strength (median 12-1 by sector), live commodity tape mapped to
      tail/headwinds. Risk-off halves starter tranches; leaders/laggards
      tilt conviction ±5.
- [x] **DONE — Breadth history** persisted nightly; the macro note now says
      whether breadth is improving or deteriorating, not just where it is.
- [x] **DONE — Rates & currency**: seeded from the owner's RBI DBIE exports
      (148 series / ~29k points: 10Y G-sec, repo/CRR/SLR, T-bills, CPI/WPI/IIP,
      USDINR, FX reserves, M3/credit, trade, BoP, GDP, HPI). The engine reads a
      distilled rates block (stance, 10Y drift, CPI YoY, USDINR, flows) with
      as-of dates on every number; policy stance tilts rate-sensitive sectors
      ±3 and hot-CPI tightening blocks risk_on. Refresh: weekly TE/MoSPI pulls
      once TRADINGECONOMICS_KEY / MOSPI_KEY are set on Railway, or re-upload a
      DBIE export at POST /api/admin/macro/upload.
- [x] **DONE — India VIX** (via the Dhan index series, if carried; verified
      at runtime): elevated VIX (>90th pctile of its year) blocks risk_on.

## 6. Calibration — the honest "training"

- [x] **DONE — IC calibration on our own history.** Every reconstructable
      signal (momentum, low-vol, P/E band, quality, growth, accruals) is
      computed point-in-time at monthly snapshots over 4 years across the
      full universe and scored by Spearman IC vs forward 6-month returns.
      Weights = priors scaled by realized IC, shrunk 50/50 toward priors.
      Re-run monthly; artifact inspectable at /api/admin/fm-engine.
- [x] **DONE — Point-in-time discipline**: statement signals only become
      "known" from 1 July after the fiscal year; no look-ahead.
- [x] **DONE — Walk-forward validation**: the last 10 monthly snapshots are
      held out; `ic_oos` sits next to the training IC in the artifact and
      never touches the weights.
- [x] **DONE — Conviction→outcome ledger**: the engine's nightly top ideas
      land in `engine_calls` and are graded in the open at
      GET /api/portfolio/engine-ledger (recorded daily, never backfilled).
- [x] **DONE — Survivorship audit**: the artifact counts full-history vs
      thin-history names and the note flags ICs as modestly optimistic.

## 7. Documents & news (the qualitative layer)

- [x] **DONE — News red-flag screen**: headline keyword classes (fraud,
      probe, default, auditor, NCLT, …) cap ADD conviction and sharpen TRIMs.
      Budget-guarded; silently empty while the vendor quota is exhausted.
- [ ] **NEEDS DATA — Investor presentations & concall transcripts, daily**:
      QuarterlyDocument stores links/PDFs; needs a daily fetcher + summarizer
      to turn guidance changes into assumption updates.
- [ ] **NEEDS DATA — Annual-report deep parse** (MD&A, contingent
      liabilities, off-balance-sheet items) — extraction pipeline over the
      stored PDFs.

## 8. Portfolio construction

- [x] **DONE — Inverse-vol sizing, concentration flags, LTCG-aware trim
      sequencing, regime-aware tranche sizing.**
- [x] **DONE — Correlation-aware sizing**: each candidate's value-weighted return
      correlation vs the book; ≥65% co-movement is called out as duplicated
      risk (and docked), ≤35% is credited as genuine diversification.
- [x] **DONE — Sector-cap warnings**: adds into a ≥30% sector are called
      out and docked. Factor-exposure caps remain NEXT.
- [ ] **NEXT — Drawdown-conditional sizing** (breadth < 40% → smaller adds,
      already half-implemented via regime).

## 9. Honesty & explainability (the moat)

- [x] **DONE — Every action lists its evidence** (val sources used, quality
      composite, band percentile, flags) and SUSPECT models are disclosed in
      the UI, not hidden.
- [x] **DONE — Consensus-basis targets** when the model is set aside (never
      quote a fair value the evidence just rejected).
- [x] **DONE — Honest low conviction** when nothing is actually wrong.
- [x] **DONE — "Would change this call"** line on every action — the 1-2
      pieces of evidence nearest to flipping it.
- [x] **DONE — Target ranges**: every target quotes the corridor spanned by
      the analyst low/high and the surviving model fair value.

---

*Engine: v4-triangulated. Macro store: seed `app/data/macro_seed.json.gz` + KVStore `macro_updates_v1` overlay (admin: GET/POST /api/admin/macro*).
*Weights: KVStore `fm_calibration_v1` (monthly).
Evidence: KVStore `fm_evidence_v1` (nightly). Trigger manually via
POST /api/admin/fm-engine/rebuild?calibrate=true.*

---

## Named sources for every NEEDS-DATA item

Ranked: official/free first, then licensed. "Official" means the primary
regulator/exchange record — always preferred for a product that sells honesty.

| Data gap | Best source (official/free) | Licensed / API alternative |
|---|---|---|
| **Promoter pledge %** | BSE/NSE quarterly **Shareholding Pattern** filings (XBRL, free) carry the pledged-shares table; event-level: **SEBI SAST Reg. 31** pledge disclosures on both exchange sites | CMIE Prowess, Capitaline, Trendlyne API |
| **Auditor changes / qualifications** | **BSE/NSE Corporate Announcements** feed (auditor resignation/appointment is a mandatory LODR filing, free); **MCA** AOC-4 filings for audit reports | Prime Database, CMIE Prowess |
| **Related-party transactions** | **LODR Reg. 23(9)** half-yearly RPT disclosures filed on BSE/NSE (free PDFs); annual-report notes (we already store the PDFs) | CMIE Prowess, Capitaline |
| **Forward multiples (FY+1 consensus EPS)** | Audit our own vendor's `forecasts` blob first (partial coverage, already licensed) | **Refinitiv I/B/E/S** (gold standard), Bloomberg, FactSet; India-affordable: Trendlyne API, MarketsMojo |
| **Bulk/block deals** | **NSE + BSE daily bulk/block deal CSVs** (official, free, same-evening) | — (official is best) |
| **Insider trades** | **SEBI PIT Reg. 7(2)** disclosures on BSE/NSE (official, free) | Trendlyne, StockEdge aggregations |
| **FII/DII daily flows** | **NSE FII/DII provisional daily** (free); **NSDL FPI Monitor** (fpi.nsdl.co.in, official) + CDSL equivalents | Moneycontrol/ETMarkets aggregate (check licence before scraping) |
| **10Y G-sec, repo, USDINR** | **RBI DBIE** (data.rbi.org.in — official database, free downloads/API); **FBIL** (fbil.org.in) for benchmark G-sec curve and reference rates; USDINR reference rate from RBI | Refinitiv, Bloomberg |
| **India VIX (if Dhan doesn't carry it)** | **NSE indices** historical download (official, free) | any licensed NSE data vendor |
| **Concall transcripts & investor presentations** | **BSE/NSE announcements** — SEBI LODR now mandates transcript upload within 5 working days (official, free PDFs; we already store links in QuarterlyDocument) | AlphaStreet, Trendlyne; screener.in collates but check licence |
| **Annual-report deep parse** | We already have the PDFs (exchange filings); the gap is an **extraction pipeline** (pdfplumber is already a dependency), not a source | Stratosphere/Tijori-style parsed data, CMIE |

Notes:
- Exchange/regulator sources (BSE, NSE, SEBI, RBI, NSDL, MCA, FBIL) are
  authoritative and free, but need scraper-grade reliability work (rate
  limits, format drift) — budget engineering time, not licence fees.
- For anything scraped, check the site's terms; NSE in particular
  rate-limits aggressively. A nightly pull with backoff is the norm.
- Priority order by evidence value for this engine:
  (1) promoter pledge, (2) FII/DII flows + G-sec yield, (3) concall
  transcripts, (4) insider/bulk deals, (5) forward EPS, (6) RPT/auditor.
