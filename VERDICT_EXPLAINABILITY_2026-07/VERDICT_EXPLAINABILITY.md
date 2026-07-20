# Verdict Explainability & Thesis at Scale (2026-07-20)

Every confident verdict examined: an honest analyst rationale was attempted
for each, and the refusals — verdicts whose own numbers won't support coherent
prose — are the signal. **Read-only for engine code.** Corpus:
`thesis_corpus.csv` (all 1,001 rows: bull/bear/assumptions/what-changes-the-
call + explainability class + tensions + cross-check overlay).

> **COMPLIANCE GATE (mandatory):** everything here is an internal
> validation/QA artifact and a content foundation. NOTHING is cleared to
> publish. Any user-facing thesis must first pass the compliance pass:
> "educational, not investment advice" disclaimers, explicit disclosure that
> the text is **algorithmically generated**, and it does not resolve the open
> SEBI Research-Analyst-registration question. No ready-to-ship marketing
> prose exists in this corpus by design.

## Method — independence by construction
PASS 1 judges narrative coherence using ONLY the engine's own ingredients
(verdict, MoS, confidence, data tier, ROE/PE vs sector norms, net margin,
street consensus, the engine's own Alpha Score). Each named **tension** is a
fact honest prose cannot reconcile with the verdict ("street sees +31% upside
vs the avoid", "single-digit ROE under a buy call", "claims the market is
+79% wrong on a liquid name"). 0 tensions → Justified · 1 → Weakly-justified
· ≥2 or low-confidence-data-under-a-confident-call → Unjustifiable.
PASS 2 overlays the independent valuation cross-check
(`VALUATION_GROUNDTRUTH_2026-07/`) — kept out of pass 1 so the two methods
stay independent. Vendor zero-sentinel margins/ROEs treated as missing (two
false tensions on INFY/COALINDIA were caught and removed this way).

## Results — all 671 confident verdicts examined
| Class | n | share |
|---|--:|--:|
| Justified | 386 | 58% |
| Weakly-justified | 260 | 39% |
| Unjustifiable | 25 | 3.7% |

By verdict: BUY 15 justified / 13 weakly / 0 unjustifiable (post-sentinel-fix
— but ALL 13 weakly-justified BUYs carry the same single tension: "claims the
market is +60–97% wrong on a liquid name": NATCOPHARM, GODFRYPHLP, AWL, KKCL,
GENUSPOWER, COALINDIA, KPITTECH, BLS, SAMHI, CESC, INFY, WEBELSOLAR,
SONATSOFTW — the exact cohort the shipped VAL-01 corroboration gate now
arbitrates). AVOID: 151 justified / 152 weakly / 16 unjustifiable.
The 330 abstained names correctly received the one-line "insufficient data /
model fit — abstention is the correct output" (honest abstention confirmed;
none were given a manufactured thesis).

## The unjustifiable-verdict list (all 25)
| Ticker | Verdict | MoS | Alpha | Tensions | Cross-check |
|---|---|--:|--:|---|---|
| PATELENG | ACCUMULATE | +77% | 50.2 | single-digit ROE 6% under a buy call | claims market is +77% wrong on a liquid name | Disagree |
| REDINGTON | ACCUMULATE | +79% | 50.0 | net margin 1.1% — fragile economics for conviction upside | claims market is +79% wrong on a li | Disagree |
| BEML | AVOID | -86% | 32.1 | street sees +26% upside vs the avoid | prices a -86% collapse without a distress story in the d | Agree |
| BLACKBUCK | AVOID | -87% | 43.7 | street sees +27% upside vs the avoid | prices a -87% collapse without a distress story in the d | Agree |
| DLF | AVOID | -62% | 45.8 | street sees +27% upside vs the avoid | prices a -62% collapse without a distress story in the d | Dir-agree-magnitude-off |
| ELLEN | AVOID | -97% | 35.2 | street sees +103% upside vs the avoid | prices a -97% collapse without a distress story in the  | Dir-agree-magnitude-off |
| EMUDHRA | AVOID | -72% | 48.2 | street sees +123% upside vs the avoid | prices a -72% collapse without a distress story in the  | Agree |
| IGIL | AVOID | -90% | 39.6 | street sees +40% upside vs the avoid | prices a -90% collapse without a distress story in the d | Agree |
| IRCTC | AVOID | -77% | 36.2 | street sees +31% upside vs the avoid | prices a -77% collapse without a distress story in the d | Agree |
| JMFINANCIL | AVOID | -64% | 42.1 | street sees +29% upside vs the avoid | prices a -64% collapse without a distress story in the d | Adjacent |
| JUBLINGREA | AVOID | -68% | 38.8 | street sees +27% upside vs the avoid | prices a -68% collapse without a distress story in the d | Agree |
| KAYNES | AVOID | -73% | 22.9 | street sees +78% upside vs the avoid | prices a -73% collapse without a distress story in the d | Agree |
| MAHLIFE | AVOID | -95% | 53.7 | street sees +27% upside vs the avoid | prices a -95% collapse without a distress story in the d | Dir-agree-magnitude-off |
| MHRIL | AVOID | -61% | 42.4 | street sees +55% upside vs the avoid | prices a -61% collapse without a distress story in the d | Dir-agree-magnitude-off |
| PRSMJOHNSN | AVOID | -98% | 36.3 | street sees +31% upside vs the avoid | prices a -98% collapse without a distress story in the d | Agree |
| PTC | AVOID | -97% | 59.5 | street sees +31% upside vs the avoid | prices a -97% collapse without a distress story in the d | Disagree |
| SBFC | AVOID | -62% | 47.7 | street sees +40% upside vs the avoid | prices a -62% collapse without a distress story in the d | Agree |
| ZAGGLE | AVOID | -84% | 32.2 | street sees +177% upside vs the avoid | prices a -84% collapse without a distress story in the  | Dir-agree-magnitude-off |
| HINDZINC | REDUCE | -24% | 71.9 | street sees +25% upside vs the avoid | own Alpha Score 72 (strong) contradicts the avoid | Dir-agree-magnitude-off |
| HUDCO | REDUCE | -25% | 70.8 | street sees +35% upside vs the avoid | elite ROE 18% at ≤1.3× sector-normal P/E — 'avoid' prose | Disagree |
| HYUNDAI | REDUCE | -34% | 61.9 | street sees +31% upside vs the avoid | elite ROE 27% at ≤1.3× sector-normal P/E — 'avoid' prose | Engine-should-have-abstained |
| JAMNAAUTO | REDUCE | -15% | 62.9 | street sees +39% upside vs the avoid | elite ROE 20% at ≤1.3× sector-normal P/E — 'avoid' prose | Engine-should-have-abstained |
| NLCINDIA | REDUCE | -37% | 76.2 | street sees +31% upside vs the avoid | own Alpha Score 76 (strong) contradicts the avoid | Adjacent |
| POWERGRID | REDUCE | -17% | 70.0 | elite ROE 19% at ≤1.3× sector-normal P/E — 'avoid' prose won't write | own Alpha Score 70 (stro | Agree |
| SEAMECLTD | REDUCE | -14% | 74.2 | elite ROE 19% at ≤1.3× sector-normal P/E — 'avoid' prose won't write | own Alpha Score 74 (stro | Dir-agree-magnitude-off |

**Highest-confidence broken set (both independent methods object):**
REDINGTON, PATELENG (bullish calls neither method can defend — VAL-01 class);
JAMNAAUTO, HUDCO, HYUNDAI, PTC (bearish calls with elite ROE + street upside +
strong own-Alpha against them — VAL-02/VAL-10 leakage into live verdicts).
The 16 extreme-AVOID rows (−61%…−98% MoS with street +26%…+177% against) are
the "priced-for-collapse" cohort: my cross-check largely AGREES directionally
but at these magnitudes the honest thesis is REDUCE-with-wide-error-bars, not
a −90% AVOID — supporting CORR-5's post-fix re-banding.

## Contradiction report (engine-consistency findings)
- **EXPL-01 (S1-adjacent, cross-ref VAL-03/FM lane):** the Ideas/Alpha surface
  ranks names the valuation engine refuses to trust — **KISSHT is Alpha rank
  #1 (88.5) while its verdict is LOW CONF at +450% MoS**; 12 of the top-20
  Alpha ranks are LOW CONF/NO DATA names (SAATVIKGL, GESHIP, LICI, PFC,
  MAHABANK, CANBK…). Root cause: the value factor consumes RAW MoS with none
  of the plausibility gates. Fix spec: factors.py value leg must consume
  gate-filtered MoS (or cap factor credit at the gate thresholds) — one
  module, no gate loosening.
- **EXPL-02:** seven bearish verdicts carry the engine's own Alpha ≥ 70
  against them (LT, SEAMECLTD, VEDL, POWERGRID, HINDZINC, HUDCO, NLCINDIA) —
  the two engines disagree about the same company with no surfaced
  reconciliation. Fix spec: surface an explicit "engines disagree" state in
  the UI contract rather than letting two products tell opposite stories.
- **EXPL-03:** zero bullish verdicts carry a very weak Alpha (≤30) — the
  bullish side is at least internally consistent.

## ARC-02 thesis-feature wiring spec (Opus-executable, AI-FREE)
The dead AI-thesis tab becomes a **rules-based thesis composer** — no LLM
call, honoring the platform's AI-free constraint; this corpus is its
validation set.
1. **Module:** `app/thesis_composer.py` — pure function
   `compose(co, valuation_row, alpha_row, sentiment, consensus) -> {bull[],
   bear[], assumptions[], triggers[], tensions[], explainability}`. Port the
   tension rules + composition logic from this probe (the exact rules are in
   the corpus generator; keep the zero-sentinel guards).
2. **Route:** revive `GET /api/companies/{t}/thesis` (tombstone stays for
   incompatible clients) returning the composed blocks + explainability class
   + a machine-readable `compliance` block.
3. **Hard gates:** verdicts LOW CONF/NO DATA → the one-line abstention text,
   never a thesis; Unjustifiable class → the thesis is NOT rendered — the
   tensions are (turning the QA signal into product honesty); every response
   carries the educational/algorithmic-generation disclaimers; the tab ships
   only after the SEBI-RA compliance question is resolved (feature-flag
   `THESIS_TAB_ENABLED`, default off).
4. **Review loop:** admin endpoint listing Unjustifiable/tension-heavy names
   weekly (reuses the integrity-sweep pattern) — explainability regressions
   become an alertable signal like errors_1h.
5. **Regression:** freeze this corpus as fixtures; composer output for the
   1,001 names must match class-for-class (allowing data drift on values).

## Cross-references
Valuation cross-check: `VALUATION_GROUNDTRUTH_2026-07/` (CORR-1..5).
Platform audit: EXPL-01 complements the FM-interface findings; the alerting
gap (OPS-01) is why KISSHT-class contradictions go unnoticed. Prior valuation
audit: VAL-01/02/10 all re-confirmed here by a third independent method.
