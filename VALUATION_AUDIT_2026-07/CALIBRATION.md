# Distribution & Calibration Report (§3b) — 1,001 companies, 2026-07-20

Source: `matrix.csv` (full universe via `/api/companies`, gate cascade replayed
with the engine's own code) + `deep.csv` (103-name live recompute).

## 1. Verdict distribution

| Verdict | n | % | median MoS |
|---|--:|--:|--:|
| BUY | 28 | 2.8% | +51.6% |
| ACCUMULATE | 67 | 6.7% | +32.7% |
| HOLD | 122 | 12.2% | +10.7% |
| REDUCE | 135 | 13.5% | −22.4% |
| AVOID | 319 | 31.9% | −56.9% |
| LOW CONF | 296 | 29.6% | −60.0% |
| NO DATA | 34 | 3.4% | — |

Shape verdict: **not random — skewed.** Monotonic MoS ordering across the ladder
(internally coherent), but 68% of issued verdicts are negative and the tails are
extreme (a calibrated engine's BUYs shouldn't need +52% median upside). 143
AVOIDs sit below −60% MoS ("priced for collapse" cohort). Root causes: VAL-02
level bias + VAL-10 ladder asymmetry.

## 2. Abstention decomposition — honest vs broken (the owner's question)

330 no-calls; the gate replay attributes **every one** (0 unexplained):

| Cause | n | Honest? |
|---|--:|---|
| Young/loss-making/near-zero ROE — no model exists | 116 | Honest, but a **missing archetype model** (VAL-05) |
| High-ROE compounder priced >45% below market by own DCF | 98 | Honest gate, **model-fit failure underneath** (VAL-02) |
| Fee financial (broker/AMC/exchange/ratings) — no model | 32 | Honest, missing archetype model (VAL-05) |
| Implausible upside (mos > +100%) | 25 | Honest sanity gate |
| Un-ingested stubs (NO DATA, no intrinsic) | 34 | Honest; data backfill work |
| Data-thin (confidence < 0.5) | 11 | Honest |
| Lender divergence (fin, mos ≥ +80%) | 9 | Honest gate |
| Life insurer without seeded EV | 3 | Honest (LICI deliberate) |
| Conglomerate without SOTP preset | 1 | Honest |
| Alt-model divergence (> +60%) | 1 | Honest gate |
| **Unexplained (pipeline bug)** | **0** | — |

**Answer: ~100% of abstention is honest in mechanism. ~75% of it (246/330) is
still *avoidable* — not by loosening gates but by building the two missing
archetype models and fixing the compounder-level bias that the −45% gate papers
over.**

## 3. Of the confident calls, how many fail a sanity bound?

- 27 of 95 BUY/ACC (28%) sit in the +60…+100% gate hole (VAL-01), incl. AWL
  +91% and REDINGTON +79% at HIGH confidence.
- In the 103-name deep sample: 4 BUYs carry cross-method dispersion > 6×
  (AWL 15.1×, BLS 9.1×, CHALET 6.8×, CESC 6.5×) and 2 BUYs swing > 65% across
  the engine's own ±1% sensitivity grid — none of which affects confidence
  (VAL-04).
- 0 non-positive intrinsics; 0 confident calls on thin-tier data
  (`confident_call_thin_data` = 0) — the data-quality leg of confidence works.

## 4. Does confidence correlate with what it should?

| | full data | partial | thin |
|---|--:|--:|--:|
| high | 451 | 0 | 0 |
| medium | 509 | 2 | 0 |
| low | 17 | 1 | 21 |

Data leg: **yes** — no high-confidence name lacks full data. Method-agreement
and sensitivity legs: **absent entirely** (VAL-04); dispersion in the deep
sample is uncorrelated with confidence (BUYs at 15× dispersion read HIGH).

## 5. Archetype shape (verdict mix by valuation sector, n ≥ 15)

Bearish-skew leaders: CEMENT 52% AVOID, CHEMICALS 44%, METAL 42%, NBFC 38%
(+50% LOW CONF — half the lender universe is a no-call), PHARMA 37%.
BUY+ACC reaches double digits only in IT_SERVICES (24%), ENERGY (18%),
CONSUMER (17%), BANK (17%), UTILITIES (14%), REALTY (12%). Eight of sixteen
major sectors have ≤7% positive calls — an engine that cannot find value across
most of the economy is describing its own level bias, not the market.

## 6. Terminal-value dependence (deep sample)

Median TV share of primary-model value: **69.4%**; > 85% for 24/103 names
(RELIANCE, LT, DMART, NTPC, TITAN, BHARTIARTL, GRASIM…). No gate consumes this
today (VAL-07).

## 7. Reverse-DCF (engine's own structure, inverted; deep sample)

Largest market-implied vs model growth gaps: DMART market-implied stage-1
**41.4% vs model cap 18%** (AVOID −47%); ADANIENT 37.9% vs 7.2%; OFSS 38.9% vs
10.8%; JYOTICNC 42.0% vs 18%. Opposite tail: ARSSBL market-implied **−30%** vs
model +17.8% (model +176% above price — correctly gated). The growth caps are
doing the verdict-setting on premium growth names — the definition of a model
artifact (VAL-02).
