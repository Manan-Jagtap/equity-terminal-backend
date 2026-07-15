# Equity Terminal → Institutional "Beast": Strategy, Competitive Analysis & Roadmap
*Researched and written July 2026. Companion to CHANGES_2026-07.md.*

---

## 0. The honest premise (read this first)

No platform — not Bloomberg, not a hedge fund's stack, not this one — can *guarantee* profit or "the highest amount of profit." Markets are competitive and partly random; anyone promising certainty is selling something. What is real and repeatable is an **edge from process**, and it comes from four multipliers, not one feature:

> **Long-run outcome ≈ (Selection edge) × (Risk management) × (Low costs & taxes) × (Behavioral discipline)**

- **Selection edge** — tilt toward what has historically paid: quality, momentum, low-volatility, sensible valuation, positive estimate revisions. In India these have delivered a measurable premium (below).
- **Risk management** — position sizing, drawdown control, diversification. This is what keeps you in the game; it matters *more* than stock picking for survival.
- **Costs & taxes** — turnover, STT, brokerage, and taxes silently eat alpha. A great strategy traded badly loses.
- **Discipline** — a written, rules-based system beats gut calls because it removes the two account-killers: fear and greed.

This terminal's job is to make all four *systematic and transparent*. Everything below serves that.

---

## 1. Where you stand vs. the world (competitive analysis)

| Platform | ~Price/yr | Best at | Weakness |
|---|---|---|---|
| **Bloomberg Terminal** | ~$32,000 | Real-time data, fixed income, IB Chat network, breadth | Cost; overkill for equity retail |
| **Capital IQ** | ~$30,000 | Company profiles, valuation, M&A/transactions, web access | Cost; heavy |
| **FactSet** | ~$12,000 | Buy-side fundamentals, Excel integration, quant tools, service | Cost |
| **Koyfin** | ~$3,600 ($299/mo) | Best all-round Bloomberg alternative; modern UI; CapIQ data | Not India-deep |
| **Screener.in** | ₹5,000 | 10-yr data depth + custom query screener | Thin UI, no valuation engine |
| **Trendlyne** | ₹2–6k | **DVM scores** (Durability/Valuation/Momentum), 1,400+ screen params, backtests, broker estimates | Scores are black-box |
| **Tickertape** | ₹2,399 | Clean UI, easy screening, MF+ETF coverage | Shallow history; data-accuracy gripes |
| **Tijori** | ₹3,500 | **Operational / alt-data** from annual reports (market share, store counts, segment mix) — 6,000+ metrics | Narrow beyond that |
| **Sensibull** | free–₹9.6k | Options: chain, IV, OI/PCR, payoff, strategy builder, virtual trading | F&O only |
| **Streak** | — | Algo/technical backtesting + automation | No fundamentals |

**Your differentiator (and it's genuinely rare):** a **transparent, independent valuation engine** (every assumption traceable), a **falsifiable public track record** (the model grades its own calls, nothing backfilled), and — as of this session — a **transparent multi-factor Alpha Score**, **total-return** accounting, and **SOTP / insurer-P/EV** models for names single-engine DCF can't touch. Trendlyne has DVM but it's a black box; you have DVM's spirit *with the math shown*. No India retail product combines transparent DCF + public track record + factor ranking. That is the moat to widen.

---

## 2. The full feature universe — and your gap analysis

Everything a best-in-class equity decision platform can offer, mapped to what you have (✅ have · 🟡 partial · ⬜ gap):

*Status refreshed 15 Jul 2026. ✅ = shipped & live. 🟡 = partial. ⬜ = open. 🔨 = actively building this program. 🔒 = externally gated (data source / owner credential).*

**Data & coverage** — fundamentals ✅ · prices ✅ · 5-yr OHLCV ✅ (Dhan) · Nifty 100 ✅ / Nifty 500 ✅ (501 visible) · second data vendor 🔒 (fundamentals single-source IndianAPI; Dhan cross-checks prices) · corporate actions ✅ · alt/operational data (Tijori-style) 🟡 (transcript KPI extractor)

**Screening & discovery** — screener ✅ · custom query builder 🟡 · **multi-factor Alpha rank ✅** · saved screens ✅ · thematic/basket discovery 🔨

**Fundamental analysis** — statements ✅ · ratios/KPIs ✅ · peer compare ✅ · forensic/quality flags ✅ · segment/SOTP ✅ · operating metrics 🟡 (transcript KPI chips)

**Valuation** — DCF/RI ✅ · reverse DCF ✅ · Monte Carlo + sensitivity ✅ · exit-multiple & Gordon cross-checks ✅ · **Dividend Discount Model ✅ (new)** · insurer P/EV ✅ · saved scenarios ✅ · editable 3-statement model 🟡 (live Excel model ✅; in-app editable 🔨)

**Quant / factor** — **Alpha Score ✅** · factor backtest sleeves 🔨 · factor exposure of a portfolio ✅ (X-ray) · smart-beta baskets 🔨

**Estimates & revisions** — consensus target ✅ · earnings-surprise (actuals) ✅ · estimate history/momentum 🔨 · beat/miss track 🔨 · forward consensus-EPS 🔒 (no clean vendor field)

**Ownership & flows** — shareholding ✅ · insider trades ✅ · FII/DII flows ✅ · promoter pledge trend ✅ · bulk/block deals ✅ (counts; named holders 🔒)

**Events & catalysts** — results calendar ✅ · dividend/split calendar 🔨 · concall/AGM dates ✅ · index inclusion events ⬜

**News & NLP** — news ✅ · AI research note ✅ · transcript/annual-report NLP summaries 🟡 (built; BSE-fetch dormant 🔒) · sentiment scoring 🔨

**Technical & charting** — momentum/RSI/SMA ✅ · interactive charts ✅ · drawing/annotations ⬜ · pattern/technical screener 🔨

**Portfolio & risk** — holdings + P&L ✅ · total return incl. dividends ✅ · value-weighted MoS ✅ · factor/beta exposure, VaR, drawdown ✅ · position-sizing tool ✅ (inverse-vol) · XIRR ✅ · tax-lot 🟡 · vs-benchmark 🔨

**Derivatives / options** — option chain ✅ (F&O-gated tab) · IV/greeks/OI/PCR ✅ · payoff & strategy builder 🔨 · *live chain data owner-gated (Dhan entitlement 401)* 🔒

**Backtesting** — track record ✅ (forward, live) · strategy/factor backtest 🔨 · rule builder 🔨

**Alerts & automation** — watchlist verdict/MoS/move alerts ✅ · Alpha-Score & signal alerts ✅ · scheduled digests 🟡

**Collaboration / AI / mobile** — ⌘K palette ✅ · shareable models ✅ (scenarios) · AI copilot 🟡 (thesis + ⌘K NL) · real mobile layout ✅ · broker connect (decision-support) 🔒

The buildable set now in flight (🔨): thematic baskets, factor backtester + rule builder, beat/miss + estimate momentum, dividend calendar, sentiment scoring, technical/pattern screener, options payoff/strategy, in-app editable model, vs-benchmark. Remaining true gaps are **externally gated** (🔒): second fundamentals vendor, forward consensus-EPS, transcript-fetch proxy, named block-deal holders, broker-connect.

---

## 3. The strategy playbook (the systematic "beast")

A written, rules-based system. The terminal now computes every input; discipline is yours.

### 3.1 What the evidence says (India, 18-yr NSE factor backtests)
- **Quality-Momentum: ~17.95% CAGR** — the strongest combination.
- **Multi-factor (blended): ~14.61% net CAGR vs Nifty 50 ~10.42% → ~+4.2% annual alpha**, Sharpe ~0.48.
- **Momentum: ~14% CAGR but −70% drawdowns** — powerful but violent; must be risk-managed.
- **Low-Volatility: beat the Nifty across *all* rolling 10-yr windows** with smaller drawdowns — the drawdown tamer.
- **Quality: ~13.5% CAGR with low drawdowns.**
- **Value: cyclical** — lagged post-2020 growth regimes, recovers in high-inflation/rising-rate regimes.

Takeaway encoded in the Alpha Score weights: **Quality 25% · Momentum 25% · Low-Vol 20% · Value 20% · Growth 10%.** Quality+momentum drive returns; low-vol controls the ride; value/growth diversify the regime risk.

### 3.2 Selection funnel (how to turn 100 names into 8–15)
1. **Universe:** the visible Nifty 100 (expanding).
2. **Alpha Score ≥ ~70** (top decile-ish) → the factor tailwind is with you.
3. **∩ Valuation gate:** independent MoS not deeply negative, and **confidence = HIGH** (the terminal already refuses to call weak data).
4. **∩ Catalyst (Phase 2):** positive estimate revision or recent earnings beat (adds timing).
5. **Diversify:** cap any one **sector** at ~25–30% of the book; avoid 3 names that are really one factor bet.

Names where **Alpha is high but the valuation verdict is AVOID** are the interesting tension: momentum/quality is strong but price is rich — a *watchlist-and-wait* candidate, not a buy. Names where **Alpha high AND verdict BUY/ACCUMULATE** are the highest-conviction sleeve.

### 3.3 Position sizing (survival math — the part most people skip)
- **Volatility-target / inverse-vol weighting:** size each position inversely to its realized volatility (the terminal computes it) so a jumpy small-cap and a steady large-cap contribute *similar* risk, not similar rupees.
- **Fractional Kelly (½ or ¼ Kelly), never full Kelly.** Half-Kelly cuts volatility ~50% while giving up only ~25% of growth — a great trade when your edge estimate is uncertain (it always is).
- **Hard caps:** no single position > ~15–20% of the book (25% absolute ceiling *regardless* of what any formula says); scale total exposure down in high-VIX regimes.
- **Drawdown throttle:** cut position sizes as portfolio drawdown deepens; add back as it recovers. This smooths the equity curve and prevents ruin.

### 3.4 Risk & exit rules (write them down before you buy)
- Pre-decide an **exit thesis**: valuation target hit, thesis broken (verdict flips to AVOID or forensic red flag), or a hard stop on position risk.
- **Rebalance on a cadence** (e.g., monthly/quarterly) not on impulse — momentum decays and factor ranks drift; disciplined rebalancing *is* the strategy.
- Respect **costs & taxes**: higher turnover = more STT/brokerage + short-term-gains tax. The 14.6% multi-factor CAGR above is *net* — turnover was controlled. Don't churn.
- **Regime awareness:** tilt toward low-vol/quality when breadth deteriorates; let momentum run in strong trends.

### 3.5 The weekly loop
Mon: scan **Ideas** (Alpha rank) → shortlist high-Alpha ∩ HIGH-confidence. Mid-week: read each shortlisted name's valuation, forensics, track-record cohort, concall. Rebalance on your cadence with inverse-vol sizing and caps. Log every decision (the track record does this for the model — do it for yourself).

> ⚠️ **Not advice.** This is an educational framework for making *your own* decisions. Past factor returns don't guarantee future results; drawdowns (−55% happened) are real; size so you can survive them. The terminal informs decisions; it does not place trades or move money.

---

## 4. The roadmap to institutional-grade

### ✅ Shipped — Valuation & data foundation
Total-return accounting (dividends + splits/bonuses) · Nifty 100 visibility (single-sourced) · REALTY/CHEMICALS sectors · onboarding hardening + IndianAPI quota guardrail · conglomerate **SOTP** + insurer **P/EV** (real FY26 EVs).

### ✅ Shipped — Systematic decision layer
Multi-factor **Alpha Score + Ideas** tab · **Portfolio X-ray + inverse-vol sizing** (factor exposure, sector HHI, risk-parity weights, flags) · **Alpha + consensus daily snapshots** (public factor track record + revision history, accruing) · **Catalyst factor** (estimate-revision momentum, 6th factor) · **Alpha public backtest** (Q1-vs-Q5) · **sector strength**.

### ✅ Shipped — Workflow & UX
**Saved DCF scenarios** (persist slider what-ifs) · **Interactive chart** (range, 50/200-DMA, volume, and a **fair-value line** at the model's intrinsic) · **Options tab** (Dhan chain: OI/IV/greeks/PCR/ATM) · **⌘K command navigation** · **watchlist Alpha/revision alerts** · **mobile bottom-nav + responsive**.

### ✅ Shipped — Dhan integration (REST-only, recorder-safe) — the big enabler
`app/dhan/` REST client + instrument map + price backfill + options endpoints. **Verified live:** token authenticates, 9,600 NSE equities mapped, historical OHLCV pulls succeed. This removes the biggest constraint — the IndianAPI price quota — and adds an options feed. See `DHAN_INTEGRATION.md`.

### ✅ Shipped 4 Jul 2026 — the Dhan dividend (see CHANGES_2026-07-04.md)
- **Daily Dhan top-up** — the 5-yr `HistoricalPrice` series now stays current (the one-off backfill had gone stale at 2026-05-29); wired into the daily EOD job, recorder-safe.
- **Second-source cross-check** — `/api/quality/cross-check` compares Dhan vs IndianAPI closes from the DB (zero vendor calls): STALE / DIVERGENT (split-shaped) alarms + a Data-health strip on the dashboard.
- **12-1 momentum + 252d vol** — the factor engine now reads the split-adjusted 5-yr series (the canonical momentum horizon), falling back to the 1-yr series for uncovered names.
- **Universe tiers** — official Nifty-500 membership (niftyindices.com CSVs, fetched 2026-07-04) is in the ingester; `UNIVERSE_TIER=nifty100|nifty250|nifty500` selects visibility. IndianAPI daily EOD stays pinned to the core 100; wider tiers are priced by the Dhan top-up + snapshot sync — no IndianAPI quota cost.
- **Shareable scenarios** — HMAC share tokens; a `?scenario=` deep link opens the company with the shared assumptions (the CapIQ collaboration primitive, done).
- **Portfolio risk block** — historical 95% VaR, 1-yr max drawdown, XIRR in the X-ray (each degrades to None on thin data — never a fabricated statistic).
- **Saved screens** — named screener presets, auth-scoped.

### 🔜 Immediate next (credential / config, not code)
1. **Options go-live** — the chain endpoint 401s (`/optionchain/expirylist`) even though `has_client_id` is true: fix the `DHAN_CLIENT_ID` value or the Dhan Data-plan entitlement, then the Options tab is fully live (`/api/dhan/status` probes this).
2. **Flip the tier** — after a broad `RUN_DHAN_BACKFILL`, set `UNIVERSE_TIER=nifty250` (later `nifty500`) on BOTH Railway services and onboard missing names. The classification gate stays.
3. **Refresh index membership before each flip** — re-pull the niftyindices CSVs. Drift is live: TATAMOTORS demerged into TMPV/TMCV; the Nifty-50 ingest set still lists TATAMOTORS and needs a successor-ticker decision.

### ⏳ Data/entitlement-gated (need a specific source, not more code)
- **Earnings-surprise (beat/miss)** — needs quarterly *actual* EPS captured at results and matched to the prior estimate. Neither IndianAPI (uncertain field) nor Dhan (no fundamentals) cleanly provides it; parked until a reliable EPS source.
- **Insider trades + FII/DII flow** — needs an insider/ownership-flow data source.
- **Transcript / annual-report NLP** — *built and deployed*, but dormant: BSE blocks the transcript-PDF fetch from Railway's IP. Unblock = a proxy or a stored-text pipeline, then it lights up.

### 🌅 Long-term / institutional moat
**Estimates database** (revision trends, estimate-momentum factor) · **entity graph** (cross-holdings, promoter networks — India-specific) · **AI research copilot** (natural-language "TCS DCF at 12% growth", auto-thesis) · **broker connect for decision-support** (surface orders you approve + execute — never auto-traded).

### The 3 I'd do next, in order
**(1) Options go-live (fix the Dhan chain 401) → (2) Nifty-250 tier flip (backfill, then `UNIVERSE_TIER`) → (3) the data-gated set** (earnings EPS, insider/flows, a transcript proxy) — everything else on the near-term list is now built and deployed-on-push.

---

## 5. What was built this session (Alpha Score) — how to use it
- New nav tab **Ideas** → the Nifty 100 ranked by **Alpha Score** with a per-name factor breakdown (Value/Quality/Momentum/Low-Vol/Growth), filterable by sector.
- Backend: `app/factors.py` (pure, tested) + `GET /api/factors` (cached 5 min, visible universe). Weights live in `FACTOR_WEIGHTS` — one place to tune the strategy.
- It reads existing precomputed valuations + 1-yr price series, so it costs **no API quota** and refreshes with the daily recompute.

*Sources: platform pricing/features — Koyfin, WallStreetPrep, WallStreetZen; India platforms — Finology, Winvesta, RandomDimes; factor evidence — BacktestIndia (18-yr NSE); risk sizing — QuantInsti, QuantifiedStrategies, EnlightenedStockTrading; signals — Sigtrix, Nasdaq, Stockopedia, AAII. Full links in the chat transcript.*
