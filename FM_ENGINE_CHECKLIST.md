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
- [ ] **NEXT — P/B and EV/EBITDA bands** alongside P/E (banks and cyclicals
      value better on book/EV); the statement lines already exist.
- [ ] **NEXT — Peer-relative valuation**: percentile within the sector's
      current multiple distribution, not just the name's own history.
- [ ] **NEEDS DATA — Forward multiples** (consensus FY+1 EPS): vendor
      forecasts blob has partial coverage; audit coverage before wiring.

## 2. Fundamental health & forensic discipline

- [x] **DONE — Forensics feed conviction.** Adapted Piotroski F, Sloan
      accruals, cash conversion, interest coverage, net-debt/EBITDA, leverage
      trend → composite + red flags; red flags cut ADD conviction and raise
      TRIM conviction. A juicy MoS can no longer outrank a bad balance sheet.
- [ ] **NEXT — Beneish M-score / Altman Z''** once receivables, working
      capital and SG&A lines are fully backfilled (partially ingested today).
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
- [ ] **NEXT — Estimate-revision momentum** (the `catalyst` signal already
      computed for Alpha) as a conviction input with its own calibrated weight.
- [ ] **NEEDS DATA — Bulk/block deals & insider trades** (NSE disclosures).
- [ ] **NEEDS DATA — FII/DII daily flows** (NSDL/exchange aggregates) for the
      macro block.

## 4. Momentum & technicals

- [x] **DONE — 12-1 momentum percentile + 50/200-DMA states** vote on entries
      and exits (entry patience below the 200-DMA, trend confirmation above).
- [ ] **NEXT — 52-week-high proximity** (strong Indian-market anomaly
      evidence) and **volume surge confirmation** — both computable today.
- [ ] **NEXT — Relative strength vs sector** (name vs its sector median), not
      just vs the whole universe.

## 5. Macro regime

- [x] **DONE — Regime read from our own data**: universe breadth (% above
      200/50-DMA), Nifty trend from the Dhan index series, sector relative
      strength (median 12-1 by sector), live commodity tape mapped to
      tail/headwinds. Risk-off halves starter tranches; leaders/laggards
      tilt conviction ±5.
- [ ] **NEXT — Breadth history** (persist the nightly breadth number →
      regime-change detection instead of a point-in-time label).
- [ ] **NEEDS DATA — Rates & currency** (10Y G-sec yield, USDINR, RBI repo):
      no licensed series in either vendor today; the single biggest macro gap.
- [ ] **NEEDS DATA — India VIX** for the risk-off trigger.

## 6. Calibration — the honest "training"

- [x] **DONE — IC calibration on our own history.** Every reconstructable
      signal (momentum, low-vol, P/E band, quality, growth, accruals) is
      computed point-in-time at monthly snapshots over 4 years across the
      full universe and scored by Spearman IC vs forward 6-month returns.
      Weights = priors scaled by realized IC, shrunk 50/50 toward priors.
      Re-run monthly; artifact inspectable at /api/admin/fm-engine.
- [x] **DONE — Point-in-time discipline**: statement signals only become
      "known" from 1 July after the fiscal year; no look-ahead.
- [ ] **NEXT — Walk-forward validation**: hold out the last year, report
      out-of-sample IC next to in-sample in the artifact.
- [ ] **NEXT — Conviction→outcome ledger**: snapshot every published action
      (like VerdictSnapshot) and grade the ENGINE's own calls in public —
      the Track Record page, but for the Fund Manager.
- [ ] **NEXT — Survivorship audit**: today's universe is today's members;
      names that fell out aren't in the panel. Quantify the bias.

## 7. Documents & news (the qualitative layer)

- [ ] **NEXT — News red-flag screen**: keyword classes over stored headlines
      (fraud, default, resignation, SEBI action, pledge invocation) → an
      event flag that caps ADD conviction pending review.
- [ ] **NEEDS DATA — Investor presentations & concall transcripts, daily**:
      QuarterlyDocument stores links/PDFs; needs a daily fetcher + summarizer
      to turn guidance changes into assumption updates.
- [ ] **NEEDS DATA — Annual-report deep parse** (MD&A, contingent
      liabilities, off-balance-sheet items) — extraction pipeline over the
      stored PDFs.

## 8. Portfolio construction

- [x] **DONE — Inverse-vol sizing, concentration flags, LTCG-aware trim
      sequencing, regime-aware tranche sizing.**
- [ ] **NEXT — Correlation-aware sizing** (pairwise return correlations from
      the 5-yr store) so two 3% adds in the same factor bucket don't read as
      diversification.
- [ ] **NEXT — Sector/factor exposure caps** with pre-trade warnings.
- [ ] **NEXT — Drawdown-conditional sizing** (breadth < 40% → smaller adds,
      already half-implemented via regime).

## 9. Honesty & explainability (the moat)

- [x] **DONE — Every action lists its evidence** (val sources used, quality
      composite, band percentile, flags) and SUSPECT models are disclosed in
      the UI, not hidden.
- [x] **DONE — Consensus-basis targets** when the model is set aside (never
      quote a fair value the evidence just rejected).
- [x] **DONE — Honest low conviction** when nothing is actually wrong.
- [ ] **NEXT — "What would change my mind"** line per action (the thresholds
      nearest to flipping the call).
- [ ] **NEXT — Confidence intervals on targets** (band + consensus dispersion
      instead of a single point).

---

*Engine: v4-triangulated. Weights: KVStore `fm_calibration_v1` (monthly).
Evidence: KVStore `fm_evidence_v1` (nightly). Trigger manually via
POST /api/admin/fm-engine/rebuild?calibrate=true.*
