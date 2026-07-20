# Agent 3 — The Other Engines (Alpha, Backtests, Fund Manager construction, Risk, Gems/Forensics, Tax, Sentiment)

Scope: read-only audit of app/factors.py, signals.py, backtest.py, strategy_backtest.py,
manager_engine.py (+manager_calibration.py, scheduler wiring), portfolio_risk.py,
portfolio_routes.py (risk/tax/FM construction), portfolio_tax.py, tax_advisor.py,
hidden_gems.py, forensics.py, ownership_logic.py, sentiment.py, corporate_actions.py,
scorecard.py. The valuation engine itself (engines.py/derive.py/alt_models.py) is out of
scope; only its consumption by these engines is noted. All evidence is path:line.

---

### [ENG-01] `macro_data` never imported — macro regime crashes every run; FM EngineCall ledger and macro refresh silently dead
- Domain: Engines / Severity: **S1** / Likelihood: Certain (static) / Effort: Trivial (1-line import) / Priority: P0
- Location: /Users/manan_jagtap/Downloads/backend/app/manager_engine.py:952, 984, 1039 (uses `macro_data.*`); no `import`/`from` binds `macro_data` anywhere in the file (AST-verified). Crash site: line 1039 `"forecast": macro_data.macro_forecast(db)` inside `macro_regime()`'s return — NOT inside any try/except.
- Evidence:
  - `grep -n macro_data app/manager_engine.py` → only lines 952, 953, 975 (`from app.macro_data import macro_summary` — binds `macro_summary`, not `macro_data`), 984, 1039.
  - `snapshot_evidence()` (manager_engine.py:1123-1131) writes `fm_evidence_v1`, then calls `macro_regime(db)` → NameError → the function aborts BEFORE `_kv_put(MACRO_KEY, …)` (1131) and before the EngineCall ledger block (1136-1166).
  - scheduler.py:394-407 wraps the whole call in try/except and logs "FM evidence failed: NameError…" nightly at 11:15 UTC (scheduler.py:431-432); the on-demand rebuild path swallows it too (portfolio_routes.py:753-757, bare `except: db.rollback()`).
  - The functions exist in app/macro_data.py (`series`:97, `macro_forecast`:157, `activity_read`:171) — only the import is missing. Introduced by commit 33ee180 ("Macro: activity read + ICRA outlook → regime…"); file last touched 2026-07-15.
- Root cause: refactor added `macro_data.*` call sites but only imported `macro_summary`; the two earlier call sites (952/984) sit inside try/except so the NameError was masked until the un-wrapped 1039 was added, and even that is swallowed by callers.
- Why it matters (blast radius):
  1. `fm_macro_v1` has not been rewritten since the break — the FM applies **stale macro regime** (regime tilt ±6, sector leader/laggard ±5, rate-sensitive ±3, half-tranche sizing) from a weeks-old blob whose `as_of` predates 15 Jul.
  2. FII/DII flows leg (952-958) and real-economy activity leg (984) NameError inside their own try/except → **always None/{} even when macro_regime used to complete** — the "FIIs sold ₹X cr" PM line and the activity risk-on shade (1012-1013) have never fired.
  3. The **EngineCall public track-record ledger never appends** (1136-1166) — the engine's own accountability trail is frozen; /api/portfolio/engine-ledger stops accruing.
  4. Breadth history KV (1017-1031) also never updates → `breadth_trend` frozen.
- FM_ENGINE_CHECKLIST status impact: §3 "FII/DII daily flows — DONE" → **Broken**; §5 regime/breadth-history/VIX items → **Partial (stale)**; §5 "NEXT — feed activity into regime" is actually implemented (1012) but dead; §6 "Conviction→outcome ledger — DONE" → **Broken**.
- Recommended fix: add `from app import macro_data` at module top (or import inside `macro_regime`); after deploy, verify scheduler log prints `FM evidence: {...}` and `fm_macro_v1.as_of` is current, and EngineCall gains rows the next night.
- Verification: `python -c "import ast,..."` name-binding scan (done); post-fix: GET /api/portfolio/engine-ledger shows new dates; macro `as_of` fresh in /api/portfolio/analysis manager.macro.

### [ENG-02] Alpha "growth" factor is actually analyst consensus upside — mislabeled factor, double-counts the Street
- Domain: Engines / Severity: S2 / Likelihood: Certain / Effort: Small / Priority: P1
- Location: /Users/manan_jagtap/Downloads/backend/app/signals.py:138 (`"growth": v.analyst_upside`); consumed as the `growth` factor at factors.py:145,161 (10% weight, factors.py:28-29).
- Evidence: factors.py module docstring (line 16-17) says rows are assembled from "precomputed valuations + 1-yr price series + **insight growth**"; the actual feed is `Valuation.analyst_upside` — the same quantity whose CHANGE is the `catalyst` factor (signals.py:21-40) and which is a valuation-witness in the FM (`val_consensus`). A real revenue-growth parse exists (`_parse_growth`, manager_engine.py:88-110) but is not used here.
- Root cause: assembly shortcut; no field with true growth was available on Valuation, so analyst upside was substituted without renaming.
- Why it matters: the published 7-factor breakdown shows a "growth" leg that is actually "analyst thinks it's cheap" — correlated with `catalyst` and with `value` (both target-derived), silently tilting the Alpha Score toward consensus and away from the documented quality/momentum evidence base. Users interpreting the factor sheet are misled.
- Recommended fix: feed `_parse_growth(CompanyInsight.growth)` (as hidden_gems does via the evidence blob) or rename the factor to `street_upside` and re-weight.
- Verification: after fix, spot-check 5 names' `factors.growth` vs vendor 3y sales CAGR; confirm rank correlation between growth and catalyst legs drops.

### [ENG-03] "Estimate-revision momentum" (catalyst) window is unbounded — measures revision-since-inception, not momentum
- Domain: Engines / Severity: S3 / Likelihood: High (grows with history) / Effort: Small / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/signals.py:35 (`latest, prior = h[-1], h[0]`); same pattern in sentiment.py:128 (`_catalyst_one`).
- Evidence: ConsensusSnapshot is written daily (signals.py:199-222); `h[0]` is the first snapshot ever taken, so the "revision" is latest-vs-first-ever. Today the ledger is young; in a year the factor becomes "upside changed since 2026", which is not estimate-revision momentum (the documented ~3-month drift analog, cf. surprise_by's explicit 100-day cap at signals.py:69-77).
- Root cause: no lookback window on the snapshot pair.
- Why it matters: factor semantics drift over time; catalyst contributions in Alpha (12% weight), FM conviction (±4, manager_engine.py:349-353) and sentiment (±15, sentiment.py:106-108) will increasingly reflect stale history; scores change meaning without any code change.
- Recommended fix: pick `prior` = latest snapshot ≥ N days old (e.g. 60-90d), mirroring surprise_by's staleness cap.
- Verification: unit test with 6 months of synthetic snapshots; catalyst should reflect only the trailing window.

### [ENG-04] Factor track record (alpha_backtest) mixes horizons and silently drops price-less names
- Domain: Engines / Severity: S3 / Likelihood: High / Effort: Small / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/factors.py:271-289 (`usable` filter; `fwd_ret` = price_now/price0); /Users/manan_jagtap/Downloads/backend/app/main.py:539-546 (rows built from FIRST AlphaSnapshot per name, `price_now` from MarketSnapshot).
- Evidence: (a) each name's forward return spans first-snapshot→today, so names onboarded later contribute shorter windows to the same bucket average — bucket means are not comparable, and Q1−Q5 mixes 30-day and 300-day returns; (b) `price_now` missing (delisted / dropped from MarketSnapshot) → the row fails the `usable` filter (factors.py:272) and vanishes from the ledger — survivorship creep in a page explicitly marketed as "honest and forward" (main.py:527-529); (c) returns are price-only (no dividend adjustment), unlike the verdict backtest which uses `ca.total_return`.
- Root cause: convenience join to current MarketSnapshot instead of last-known snapshot price.
- Why it matters: trust surface — the factor-efficacy page can flatter (or trash) the Alpha Score for mechanical reasons; a bucket dominated by young names reads as "no signal" noise.
- Recommended fix: annualize or bucket by cohort start-month; fall back to the name's LAST AlphaSnapshot price when MarketSnapshot has no row; state price-only basis in the note.
- Verification: recompute with a synthetic delisting; row should persist with its last known mark.

### [ENG-05] Verdict backtest freezes delisted/price-less open calls at exactly 0% return
- Domain: Engines / Severity: S3 / Likelihood: Medium (grows with universe churn) / Effort: Small / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/backtest.py:86-88 (`latest_price if latest_price else run_start["price"]` → ret = 0.0); latest_price from MarketSnapshot only (backtest.py:255, 268).
- Evidence: a company that leaves MarketSnapshot (delisting, coverage drop) has its final open call marked to its own entry price → 0% return with growing `days`. A BUY that went to zero shows 0%, an AVOID that cratered also shows 0% — both distort cohort means and win rates in the headline BUY−AVOID spread.
- Root cause: fallback chosen to avoid None propagation rather than using the last VerdictSnapshot price.
- Why it matters: the module's whole pitch is falsifiability ("no survivorship editing", backtest.py:12-13); freezing losers at 0% is unintentional survivorship-lite.
- Recommended fix: mark open calls to the company's most recent VerdictSnapshot price when MarketSnapshot is missing, and flag the call `stale_price: true`.
- Verification: unit test compress_calls with latest_price=None → expect last-snapshot mark, not entry price.

### [ENG-06] calibrate() scores HOLD calls as bearish — inconsistent with aggregate()'s "HOLD has no direction"
- Domain: Engines / Severity: S3 / Likelihood: Certain / Effort: Trivial / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/backtest.py:219-221 (`bullish = verdict in ("BUY","ACCUMULATE")`; everything else — including HOLD — "wins" when ret < 0) vs backtest.py:134-139 (aggregate: HOLD `wins = None`).
- Evidence: HOLD|band buckets get a hit_rate as if HOLD were a short call; once `ready` flips (≥30 calls/bucket, ≥90 days — backtest.py:197-198), `suggested_multiplier = 0.6+0.8*hit` (line 236) would de-rate HOLD confidence in any rising tape for a call that made no directional claim.
- Root cause: binary bullish/bearish split without excluding the no-direction cohort.
- Why it matters: the calibration is correctly gated/advisory today (honest — the #120 gating is well done), but the table is already displayed and will eventually drive live confidence; a wrong-by-construction bucket poisons it.
- Recommended fix: skip HOLD in calibrate() (or give it its own |ret|<threshold "stayed flat" definition).
- Verification: unit test — HOLD calls must not contribute wins/hit_rate.

### [ENG-07] model_trust_by_sector benchmarks BUY calls against their own median — average trust is pinned by construction; horizons are 185-720 days, not "6 months"
- Domain: Engines / Severity: S3 / Likelihood: Certain / Effort: Medium / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/manager_engine.py:607-615 (forward ret = latest MarketSnapshot price vs call price; `med` = median of the SAME aged-BUY-call sample; win = ret > med).
- Evidence: docstring (579-582) claims "beat the **universe median** forward 6-month return"; the code takes calls aged 185-720 days (585-590), marks them to TODAY (not entry+126td), and compares each to the median of that same call set — so overall hit-rate ≈ 50% and mean sector trust ≈ 0.3+0.5·0.9 ≈ 0.75 regardless of whether the model's BUYs are good in absolute terms. Sector trust only measures WHICH sectors' BUYs beat other sectors' BUYs, with older calls structurally advantaged in a trending market (longer holding window inflates |ret|).
- Root cause: no stored universe forward-return panel; convenience benchmark substituted.
- Why it matters: `sector_trust` scales the model's vote weight in triangulate (manager_engine.py:262) — the mechanism cannot detect "the model is globally unreliable", and sector rankings partly reflect call-age mix, not skill.
- Recommended fix: compute each call's forward return over a FIXED window from VerdictSnapshot/HistoricalPrice (entry date + ~126 trading days), and benchmark against the tracked-universe return over the SAME window (the data exists in VerdictSnapshot/HistoricalPrice).
- Verification: synthetic panel where all BUYs underperform the universe → all sector trusts should fall to the floor (0.3); today they would not.

### [ENG-08] Two different XIRRs for the same book: risk block uses `added_at`, totals use `buy_date`; uncovered cost flows have no terminal value
- Domain: Engines / Severity: S3 / Likelihood: High / Effort: Small / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_routes.py:487-488 (`added = h.added_at…; cashflows.append((added, -qty*avg_cost))`) vs 369-371 (totals XIRR uses `i["buy_date"]`); terminal value only sums priced holdings (489-493) while every holding's cost is an outflow (488).
- Evidence: `_risk_block` feeds risk_summary's xirr (portfolio_risk.py:76-111 — the bisection itself is correct: sign checks, bracket [-0.9999, 10], 30-day suppression); `/api/portfolio` totals.xirr uses real buy dates. A book imported via Dhan sync (no buy_date; note at 1358) held for years shows a wildly annualized risk-block XIRR from the app-add date, while an unpriced holding contributes cost with no offsetting terminal inflow → structurally depressed XIRR.
- Root cause: `_risk_block` predates the buy_date field; never reconciled.
- Why it matters: two contradictory "what did I actually earn" numbers on adjacent views erode exactly the trust the risk page exists to build.
- Recommended fix: reuse `_term_fields`' resolved date (buy_date, else added_at, flagged) in `_risk_block`, and drop unpriced holdings from the flow set (or mark coverage in the payload — `coverage` already exists in risk_summary:132).
- Verification: seed a holding with buy_date ≠ added_at; both endpoints must report the same XIRR.

### [ENG-09] Benchmark alpha overstated: NIFTY 50 price index vs portfolio return including dividends
- Domain: Engines / Severity: S3 / Likelihood: Certain / Effort: Small / Priority: P2
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_routes.py:260-268 (`port_now += v + div_income` vs `bench_now = c * (now/lvl)` from the Dhan NIFTY 50 close series, _nifty_series:210-233).
- Evidence: the portfolio leg is total-return (capital + `_dividend_income`, 272-288) while the benchmark leg is price-only — NIFTY TRI runs ~1.2-1.4%/yr above the price index, so "alpha" (268) carries a structural positive bias that compounds with holding period.
- Root cause: no TRI series in the Dhan feed; dividend asymmetry not disclosed.
- Why it matters: a flattering-by-construction alpha on a product that sells honesty; also inconsistent with backtest.py's care to label its universe benchmark basis (backtest.py:171-186).
- Recommended fix: either drop `div_income` from `port_now` (price-vs-price, label it) or add a fixed disclosed TRI adjustment (~+1.3%/yr) / ingest NIFTY 50 TRI; at minimum label "benchmark excludes dividends; portfolio includes them".
- Verification: payload `benchmark` block gains a `basis` field; alpha shifts down accordingly.

### [ENG-10] LTCG boundary off-by-a-hair: `days >= 365` calls exactly-12-months "long"; statute is MORE than 12 months. Deferral windows also inconsistent (120d vs 45d)
- Domain: Engines / Severity: S3 / Likelihood: Low (boundary dates only) / Effort: Trivial / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_routes.py:147-148, 166 (`_LT_DAYS = 365`, `"long" if days >= _LT_DAYS`); window inconsistency: portfolio_tax.py:28 (`LT_TIMING_WINDOW = 120`) vs tax_advisor.py:102 (`<= 45`).
- Evidence: Sec 2(42A)/112A: listed equity is long-term when held **more than** 12 months (month-based). `days >= 365` marks a 365-day hold long-term, but 12 months from 1-Mar-2024 (leap year) is 1-Mar-2025 = 365 days — not yet "more than 12 months"; exact-anniversary sales are also misclassified. Consequence: tax_block/tax_plan (both consume `term`) can advise a "tax-free" LTCG harvest on a lot that is legally still short-term (20%). Rates and set-off order themselves are CORRECT for the post-Jul-2024 regime: STCG 20%, LTCG 12.5% over ₹1.25L (portfolio_tax.py:25-27, tax_advisor.py:31-33); LT loss→LT gain only, ST loss→ST then LT (portfolio_tax.py:56-64) matches law; disclaimers present (portfolio_tax.py:111-114, tax_advisor.py:122-125).
- Root cause: day-count approximation of a month-based statute.
- Why it matters: precision matters most exactly at the boundary this feature exists to optimize ("wait N days to save 7.5pp").
- Recommended fix: month-arithmetic (`bd + relativedelta(months=12) < today`) or conservative `days > 366`; unify the two deferral windows.
- Verification: unit tests at 364/365/366 days incl. a leap-Feb start.

### [ENG-11] After-tax trim estimates always assume the full ₹1.25L exemption — dead conditional, double-counted vs the harvest plan
- Domain: Engines / Severity: S3 / Likelihood: Certain / Effort: Trivial / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_routes.py:1275 (`exemption_left = tax.get("ltcg_exemption_usable") is not None and 125000.0 or 125000.0` — evaluates to 125000.0 on every branch).
- Evidence: in the same report, tax_plan (1270) proposes harvesting up to ₹1.25L of LT gains tax-free AND each REVIEW trim's `after_tax_inr` (1277-1287) draws down a fresh ₹1.25L. Executing both underestimates tax on the trims by up to ₹15,625 (0.125 × 1.25L).
- Root cause: and/or one-liner bug; intended to subtract the harvest plan's earmarked exemption.
- Why it matters: the "after-tax proceeds" number is presented as the PM-grade figure; it can be optimistic exactly when the user follows all the suggestions.
- Recommended fix: `exemption_left = max(0, 125000.0 - (tax.get("ltcg_exemption_usable") or 0))`.
- Verification: book with ≥₹1.25L harvestable LT gains + an LT trim → trim tax_estimate should use zero remaining exemption.

### [ENG-12] Sloan accrual flag fires on |accruals| — big NEGATIVE accruals get "High accruals / earnings exceed operating cash by −X%"
- Domain: Engines / Severity: S3 / Likelihood: Medium / Effort: Small / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/forensics.py:251 (`_flag(abs(accr), 0.10, 0.25, higher_is_better=False)`) and 255-256 (red-flag note text uses signed `accr`).
- Evidence: a company with CFO ≫ PAT (accr = −0.30, conservatively stated earnings or heavy non-cash charges) is red-flagged "High accruals" with the self-contradictory note "Earnings exceed operating cash by −30% of assets". Sloan (1996) is a signal about high POSITIVE accruals; the composite then scores it 25/100 on the 20%-weight leg (346), dragging quality and — via manager_engine red flags (745) — FM conviction (−8/flag, manager_engine.py:321-323) and the hidden-gems hard gate (hidden_gems.py:201).
- Root cause: abs() used to catch "extreme either way" without a separate label/direction.
- Why it matters: punishes conservative accounting identically to aggressive accounting; can knock legitimately clean compounders out of Hidden Gems (any red flag is an absolute exclusion).
- Recommended fix: flag red only for accr > +0.10/+0.25; treat large negative accruals as amber "one-off/non-cash distortion — check" with correct wording.
- Verification: synthetic statements PAT=100, CFO=160, TA=200 → expect no "High accruals" red.
- Note: rest of forensics thresholds are defensible and cited inline — coverage 6×/3× green/amber (276), net-debt/EBITDA 1×/3× flag + red >4× (287-291), D/E trend +0.10 (299), Piotroski adapted 7/9 with renormalization disclosed (57-109), Altman Z'' EM constants and zones correct (139-163), Beneish coefficients correct with SGAI held neutral and disclosed (166-226; component fallbacks to 1.0 mean sparse data drifts M toward "clean" — inherent limitation, disclosed only via `sgai_neutral`).

### [ENG-13] Sentiment lexicon: no negation handling, ambiguous tokens, and cached news scores never expire
- Domain: Engines / Severity: S4 / Likelihood: Medium / Effort: Medium / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/sentiment.py:25-40 (lexicon), 53-69 (scorer), 72-95 (KV cache `news_sentiment_v1` written only when the News tab fetches; `_news_one` ignores the stored `as_of`).
- Evidence: "high" is bullish ("high debt", "high attrition" score +1); "cut" is bearish ("tax cut" +…); "fine"/"record" ambiguous; "no fraud alleged" scores bearish (no negation window). News leg staleness: score recorded on a News-tab visit persists indefinitely (line 92-94 reads without checking `as_of`) — a 6-month-old bearish headline still contributes ±12 to today's sentiment score.
- Root cause: deliberate AI-free lexicon (fine per platform doctrine) but with no age gate and a few high-frequency ambiguous tokens.
- Why it matters: bounded — the score is clearly decomposed (`parts`) and **advisory-only**: consumers are the screener columns (main.py:588-642) and the company page (main.py:736-767); grep confirms no valuation-path import (engines.py, ingest/compute_valuations.py contain no sentiment reference). Concall tone reaching FM conviction is a separate leg (transcript_ingester → manager_engine.py:356-359), not this score.
- Recommended fix: drop/qualify "high", "cut", "fine", "record"; add a simple 3-token negation window; expire news scores older than ~30 days in `_news_one`/`sentiment_by`.
- Verification: unit tests on "no fraud found", "tax cut boosts autos", "record loss"; stale-KV test.

### [ENG-14] Alpha Score robustness: rank-based (good) but mixed momentum horizons, series-source switching, single-factor names, and tie-order artifacts
- Domain: Engines / Severity: S4 / Likelihood: Medium / Effort: Small-Medium / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/factors.py:56-62 (12-1 falls back to 6-1 per-name), 32-42 (`_pct_ranks` — no tie-averaging; equal values get sequential percentiles in input order), 165-170 (weight renormalization over present factors — a name with only ROE ranks on one leg), signals.py:120-124 (Dhan vs PricePoint series switch when the Dhan stub reaches `max(200, len(PricePoint))`).
- Evidence/why it matters:
  - Normalization is percentile ranks — inherently winsorized/robust (good; no z-score outlier issue). Missing data does NOT silently become median/0: the leg is excluded and weights renormalize (factors.py:165-170) — honest, BUT there is no minimum-factor gate, so a thin name scored on 1-2 legs is directly comparable/rankable against fully-covered names (a lone 95th-pct ROE name can top the sheet).
  - Momentum mixes horizons within one cross-section: names with <253 closes are ranked on 6-1 against everyone else's 12-1 (factors.py:60-62) — the two aren't the same signal.
  - No look-ahead in the live score: momentum excludes the last 21 days (factors.py:45-53), and the track record buckets on FIRST-snapshot alpha (main.py:539-541) — clean.
  - Stability: 5-min shared cache (signals.py:152-174) is fine, but day-to-day a name can jump when its series source flips PricePoint→Dhan (different length → momentum def changes) or when ties re-order.
  - `technicals`/RSI (factors.py:79-124) use standard simple-average RSI (not Wilder smoothing) — label says "Wilder-style" (79); cosmetic mislabel.
- Recommended fix: require ≥3 populated factors for an alpha_score (else `alpha_score: null, reason`); tag `momentum_basis: "12-1"|"6-1"` per row; average tied ranks.
- Verification: run score_universe twice with shuffled row order → identical scores incl. ties.

### [ENG-15] FM construction: sizing is conviction-blind (flat 3%/1.5% tranche); suspect/LOW-conf handling in sizing is otherwise sound
- Domain: Engines / Severity: S4 / Likelihood: Certain (by design) / Effort: Medium / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_routes.py:1031-1036 and 1109-1115 (every ADD gets 3% of book, 1.5% in risk_off, regardless of conviction 5-95, name volatility, or LOW-confidence inputs); trims sized to inverse-vol targets (1026-1030 via factors.portfolio_xray suggested_weight).
- Evidence: conviction affects ORDER (1212 sort) and cash allocation order (1153) but never rupee size; the size_note itself says "scale on conviction" — delegated to the user. Reduced-conviction treatment of LOW-conf/SUSPECT valuation inputs (beyond the audited triangulate gates) IS present where it matters: LOW confidence → suspect → model leg excluded from the blend (manager_engine.py:239-241, 262-263), suspect-with-no-score capped at 42 (410-413), suspect names' levels quote consensus basis (portfolio_routes.py:1040-1045), and Hidden Gems hard-excludes them (hidden_gems.py:203). Gap: a SUSPECT name whose consensus+band legs are strong can still reach high conviction and receives the same 3% tranche as a fully-corroborated name.
- Diversification/banding verified: sector-cap ≥30% docks 5 (822-843), value-weighted return-correlation vs book ≥0.65 docks 4 / ≤0.35 credited (845-900, 60-obs minimum, correct Pearson on aligned dates), concentration flags at 25%/30% (factors.py:207-209, portfolio_routes.py:442-444+1223-1225), results-blackout caps conviction at 55 (1129-1137), rotation plan requires 80% funding coverage (1202). Rebalancing cadence: none scheduled — actions regenerate per page view off nightly evidence; EngineCall ledger is the daily cadence (currently dead — ENG-01).
- Recommended fix (optional, product call): scale tranche by conviction band (e.g. 2/3/4%) and by inverse name-vol; halve tranche for suspect-model names as it already does for risk_off.
- Verification: payload inspection — size_inr should vary across adds after the change.
- Checklist §8: inverse-vol sizing / corr-aware / sector caps / tax layer / LTCG sequencing — **all verified present** (DONE claims accurate).

### [ENG-16] Portfolio risk analytics: correct core math; strict date-intersection truncates history, portfolio vol proxy ignores correlation, current-qty applied to full lookback
- Domain: Engines / Severity: S4 / Likelihood: Medium / Effort: Small / Priority: P3
- Location: /Users/manan_jagtap/Downloads/backend/app/portfolio_risk.py:30-38 (strict intersection of all covered holdings' dates), 47-56 (VaR), 59-73 (MDD), 15-24 (current qty over past closes); factors.py:236 ("est_volatility" = value-weighted mean of per-name vols); portfolio_routes.py:463 (400-day window).
- Evidence:
  - Historical-sim 1-day 95% VaR: index `int(n*(1-c))-1` is the 5th-worst of 100 — one observation more conservative than the usual quantile; ≥60-return gate; floored at 0 — sound and honest (returns None on thin data, never fabricates).
  - Max drawdown: correct running-peak scan with dates.
  - XIRR bisection: sign/bracket/short-span guards correct (76-111); upper bracket 10 (1000%/yr) returns None beyond — fine.
  - Annualization: daily σ×√252 (factors.py:65-72) — correct convention.
  - Caveats: (a) one recently-listed holding truncates the WHOLE book's series to its window (intersection), understating MDD/VaR sample silently — `observations` is exposed but not explained; (b) `portfolio_xray.est_volatility` (weighted mean of standalone vols) ignores correlations → overstates diversified book vol; it feeds no decisions but is displayed; (c) VaR/MDD apply TODAY'S qty across the past year (standard "current book" risk, but worth labeling); (d) correlation matrix inputs elsewhere are handled properly (aligned common dates, ≥60 obs — portfolio_routes.py:865-874) and series are the vendor split-adjusted Dhan closes with `drop_bad_ticks` hygiene (portfolio_routes.py:464-479) — no survivor-price issue for held names.
- Recommended fix: per-holding windows with a labeled `history_from` (or drop the newest name from the series and say so); rename est_volatility → avg_position_vol or compute true portfolio σ from the aligned return matrix already built for correlations.
- Verification: add a 3-month-old listing to a 2-year book → MDD window should not collapse to 3 months.

### [ENG-17] Hidden Gems thresholds: disclosed and mostly defensible; cap tiers are ₹-static, not AMFI-rank-based
- Domain: Engines / Severity: S4 / Likelihood: Low / Effort: Small / Priority: P4
- Location: /Users/manan_jagtap/Downloads/backend/app/hidden_gems.py:38-51 (all thresholds), 195-214 (hard gates), 150-181 (rank blend).
- Evidence (each threshold): CAP_MAX ₹20,000 Cr / CAP_MIN ₹300 Cr (38-39) — "under-followed" proxy; note AMFI defines small/mid by RANK (101-250 mid), and ₹20k Cr straddles today's mid-cap boundary, so `cap_tier` labels (82-89) can disagree with official classification. QUAL_MIN 60 (40) — forensic composite floor; ROE_MIN 14% (41); GROWTH_MIN 10% rev CAGR or PAT_YOY_MIN 12% (42-43); MOS_FLOOR −15% (44); MOS sanity 75% → conviction −10 + reframed thesis, hard cap 100% → excluded as model artifact (49-50, 179-181, 213-214) — a good honesty mechanism consistent with the valuation audit's level-bias findings. All criteria are echoed in the API payload (302-307) with a no-promise note (308-310) and boilerplate risks (143-147). Market-cap units verified correct: `price × shares_outstanding` with shares stored in CRORES (ingester normalizes: app/ingest/indianapi_ingester.py:244-253 `sh/1e7` for absolute counts, bounded 0.1-100000) → ₹ Cr as compared. Exclusion of red-flag/pledge/suspect/LOW-conf names verified (201-203) — inherits ENG-12's abs-accrual false positives as its main practical distortion.
- Recommended fix: optional — align cap tiers to AMFI rank lists; otherwise document "₹-based tiers".
- Verification: n/a (documentation).

### [ENG-18] Strategy backtester & manager calibration: honest by design; residual caveats worth pinning
- Domain: Engines / Severity: S4 / Likelihood: Low / Effort: None-Small / Priority: P4
- Location: /Users/manan_jagtap/Downloads/backend/app/strategy_backtest.py; /Users/manan_jagtap/Downloads/backend/app/manager_calibration.py.
- Evidence (positive findings, for the record):
  - strategy_backtest: point-in-time signal windows (`cls[:j]`, 220-224), survivorship EXPLICITLY disclosed in the payload note plus `survivorship: true` flag (302-308), costs/taxes exclusion disclosed, NIFTY 50 benchmark with honest equal-weight fallback labeled (301). Caveats: trades assumed at the same close that generated the signal (222-237 — standard but optimistic), benchmark is price-index not TRI while stock legs are also price-only (consistent, but "vs NIFTY" understates the index's true total return), Sharpe mixes geometric CAGR with arithmetic period vol (276-284 — conventional).
  - manager_calibration: genuinely point-in-time (prices `cs[:idx]`, statements gated at 1-Jul post-FY — 81-84, 202-208), walk-forward holdout of last 10 snapshots reported as `ic_oos` and excluded from weights (256-276), survivorship counted and disclosed as "modestly optimistic" (296, 303-305), current-shares drift for the band series documented in-code (180-181). Caveats: monthly snapshots with 6-month forward windows → overlapping, autocorrelated IC series (std understated; n overstated ~6×); IC→weight scale `0.5+min(max(ic,0)*12,1.5)` (287) is an undocumented heuristic; negative-IC signals keep half their prior rather than zero (defensible shrinkage, worth stating in the note).
- Recommended fix: one-line notes for close-execution assumption and IC overlap; nothing structural.
- Verification: n/a.

### [ENG-19] Minor mechanical items (grouped)
- Domain: Engines / Severity: S4 / Likelihood: Certain / Effort: Trivial each / Priority: P4
- Locations & evidence:
  - backtest.py:46 — `db.query(models.Company).get(v.company_id)` inside the per-valuation loop: ~1 query per company per daily snapshot (N+1); preload like everything else in the function.
  - manager_engine.py:335 — f-string with no placeholder ("12-1 momentum in the top third…"); cosmetic.
  - manager_engine.py:767 — `per_share` unit heuristic `shares > 1e6` is dead-defensive given the ingester bounds shares to 0.1-100000 Cr (indianapi_ingester.py:252); harmless but confusing next to the crore-normalized store.
  - factors.py:79-91 — RSI labeled "Wilder-style" is a simple average (no Wilder smoothing); relabel.
  - strategy_backtest.py:166-168 — dead `if freq == "Q"… pass` branch.
  - portfolio_routes.py:316 — per-position "xirr" is a simple annualized total-return (fine) but shares a name with the true XIRR fields; consider `cagr`.
- Recommended fix: as noted per item.

---

## FM_ENGINE_CHECKLIST.md status corrections (evidence-based)
- §3 "FII/DII daily flows — DONE" → **Broken** (ENG-01: dead code path, never displayed).
- §5 regime/breadth-trend/VIX — **Partial**: computed correctly in code but the macro blob no longer refreshes (ENG-01).
- §5 "NEXT — feed activity indicators into regime" → actually **implemented** (manager_engine.py:1012) but dead (ENG-01).
- §6 "Conviction→outcome ledger — DONE" → **Broken** (ENG-01: EngineCall writes unreachable).
- §6 calibration items (IC, point-in-time, walk-forward, survivorship audit) → **Verified DONE** (ENG-18).
- §8 construction items (inverse-vol sizing, corr-aware, sector caps, tax layer, franchise lens) → **Verified DONE** (ENG-15).
- §9 explainability (evidence lists, consensus-basis targets, honest low conviction, flip lines, target ranges) → **Verified DONE** (portfolio_routes.py:968-982, 1040-1058; manager_engine.py:483-523).

---

## Summary (10 lines)
1. One S1 ship-stopper: `macro_data` is used but never imported in manager_engine.py — macro_regime() NameErrors on every run, so the FM's macro blob is stale, FII/DII+activity legs never fired, and the EngineCall public track-record ledger has been silently frozen (scheduler swallows it nightly). One-line fix.
2. The Alpha Score's "growth" factor is actually analyst consensus upside (signals.py:138) — mislabeled and double-counts the Street alongside catalyst; the 7-factor sheet misdescribes itself (S2).
3. Backtest engine is genuinely honest (append-only, no backfill, gated calibration) but has three integrity leaks: delisted open calls freeze at 0%, calibrate() scores HOLD as a short call, and the factor track record drops price-less names and mixes horizons.
4. model_trust_by_sector benchmarks BUY calls against their own median at variable 6-24-month horizons — it can rank sectors but cannot detect a globally bad model, contra its docstring.
5. Portfolio risk math (VaR/MDD/XIRR) is correct, but the risk-block XIRR uses added_at while totals use buy_date (two contradictory numbers), and the NIFTY benchmark is price-only vs a dividend-inclusive portfolio (alpha overstated).
6. Indian CG tax logic is regime-correct (20%/12.5%/₹1.25L, proper set-off order, real disclaimers) with boundary bugs: `days>=365` vs the statutory "more than 12 months", a dead conditional that always grants the trims a fresh ₹1.25L exemption, and 120d-vs-45d window inconsistency.
7. Forensics is well-built (correct Altman Z'', Beneish, adapted Piotroski, cited thresholds) except the Sloan accrual flag fires on |accr| — conservative CFO≫PAT books get red-flagged and hard-excluded from Hidden Gems.
8. Hidden Gems thresholds (₹300-20,000 Cr, quality≥60, ROE≥14%, growth≥10%/PAT≥12%, MoS −15%..+100% with 75% sanity dock) are all disclosed in the payload and defensible; cap-units verified correct.
9. Sentiment is confirmed advisory-only (screener/company page; never a valuation input) — but the lexicon lacks negation, "high/cut/fine" are noisy, and cached news scores never expire.
10. Strategy backtester and FM calibration are the strongest pieces: point-in-time, walk-forward, survivorship disclosed; residual caveats are close-execution assumption and overlapping-window IC autocorrelation.

Counts: S1×1 (ENG-01) · S2×1 (ENG-02) · S3×10 (ENG-03..12) · S4×7 (ENG-13..19).
