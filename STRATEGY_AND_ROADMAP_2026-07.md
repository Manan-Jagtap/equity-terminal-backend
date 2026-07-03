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

**Data & coverage** — fundamentals ✅ · prices ✅ (1yr) · 5-yr OHLCV 🟡 · Nifty 100 ✅ / Nifty 500 ⬜ · second data vendor ⬜ · corporate actions ✅ (new) · alt/operational data (Tijori-style) ⬜

**Screening & discovery** — screener ✅ · custom query builder 🟡 · **multi-factor Alpha rank ✅ (new)** · saved screens ⬜ · thematic/basket discovery ⬜

**Fundamental analysis** — statements ✅ · ratios/KPIs ✅ · peer compare ✅ · forensic/quality flags ✅ · segment/SOTP ✅ (new) · operating metrics ⬜

**Valuation** — DCF/RI ✅ · reverse DCF ✅ · Monte Carlo + sensitivity ✅ · exit-multiple & Gordon cross-checks ✅ · **insurer P/EV ✅ (new)** · saved scenarios ⬜ · editable 3-statement model ⬜

**Quant / factor** — **Alpha Score ✅ (new)** · factor backtest sleeves ⬜ · factor exposure of a portfolio ⬜ · smart-beta baskets ⬜

**Estimates & revisions** — consensus target ✅ · **estimate-revision & earnings-surprise signals ⬜ (high value)** · estimate history/momentum ⬜ · beat/miss track ⬜

**Ownership & flows** — shareholding ✅ · **insider trades ⬜** · FII/DII flows ⬜ · promoter pledge trend 🟡 · bulk/block deals ⬜

**Events & catalysts** — results calendar ✅ · dividend/split calendar 🟡 (data now stored) · concall/AGM dates ⬜ · index inclusion events ⬜

**News & NLP** — news ✅ · AI research note ✅ · **transcript/annual-report NLP summaries ⬜** · sentiment scoring ⬜

**Technical & charting** — momentum/RSI/SMA ✅ (engine) · **interactive charts ⬜** · drawing/annotations ⬜ · pattern/technical screener ⬜

**Portfolio & risk** — holdings + P&L ✅ · **total return incl. dividends ✅ (new)** · value-weighted MoS ✅ · **factor/beta exposure, VaR, drawdown ⬜** · position-sizing tool 🟡 (framework below) · tax-lot / XIRR ⬜ · vs-benchmark 🟡 (basket now)

**Derivatives / options** — ⬜ entirely (Sensibull territory): option chain, IV, OI/PCR, payoff, strategy builder

**Backtesting** — track record ✅ (forward, live) · **strategy/factor backtest ⬜** · rule builder ⬜

**Alerts & automation** — watchlist verdict/MoS/move alerts ✅ · **Alpha-Score & signal alerts ⬜** · scheduled digests 🟡

**Collaboration / AI / mobile** — ⌘K palette ✅ · shareable models ⬜ · AI copilot 🟡 (thesis) · **real mobile layout ⬜** · broker connect (decision-support) ⬜

The pattern is clear: your **analysis core is best-in-class**; the gaps are **signals (revisions/flows), derivatives, charting, portfolio risk analytics, and mobile.** The roadmap below attacks them in ROI order.

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

### ✅ Phase 1 — shipped (foundation + valuation)
Total-return accounting (dividends + splits/bonuses) · Nifty 100 visibility (single-sourced) · REALTY/CHEMICALS sectors · onboarding hardening + API quota guardrail · conglomerate **SOTP** + insurer **P/EV** (real FY26 EVs) · **multi-factor Alpha Score + Ideas view**.

### ✅ Phase 2 — shipped (systematic decision layer)
- **Portfolio X-ray + inverse-vol position sizing** — book-level factor exposure, sector concentration (HHI), est. volatility, risk-parity suggested weights with concentration/low-Alpha flags.
- **Alpha + consensus daily snapshots** — the ledgers behind a public factor track record and estimate-revision signals (capture started; cannot be backfilled).
- **Catalyst factor** — estimate-revision momentum wired into the Alpha Score as a 6th factor (activates as consensus history accrues).
- **Alpha-Score public backtest** — forward return by Alpha bucket (Q1 vs Q5) from the snapshot ledger; grades the model in public like the Track Record.
- **Sector strength** — the ranking aggregated by sector, so you see where the factor tailwind is now.
- **⌘K command language** — type a destination ("SECTORS", "IDEAS", "TRACK") to jump there; keyboard-first, Bloomberg-style.

### Phase 3 — near-term, highest remaining ROI (a little data / plumbing)
1. **Earnings-surprise (beat/miss) signal** — actual-vs-estimate EPS at results; post-earnings drift persists ~3 months. Wire quarterly actual EPS to the estimate snapshots now accruing → a 7th input.
2. **Insider trades + FII/DII flow** — insider buying front-runs positive surprises. Needs the vendor's insider/ownership-flow endpoints.
3. **Alerts on Alpha / signals** — extend the watchlist alert engine to "entered top Alpha decile," "revision upgrade." Self-contained; builds on the new snapshots.
4. **Saved DCF scenarios + shareable links** — persist slider states per user (auth plumbing already exists) — the collaboration primitive CapIQ sells.

### Phase 4 — mid-term (differentiators, each needs a feed or a large build)
5. **Universe → Nifty 500** via a **batch EOD price feed (NSE bhavcopy)** — the one re-architecture that breaks the per-name-polling quota wall. Ship in sector tranches, gated by the confidence layer.
6. **Interactive charting** (price/volume, overlays, the factor & valuation bands you already compute).
7. **Options/derivatives analytics** — option chain, IV, OI/PCR, payoff, a basic strategy builder (Sensibull-lite) for hedging and income.
8. **Transcript / annual-report NLP** — quarter-over-quarter guidance & sentiment diffs (the ANTHROPIC key is already wired); plus Tijori-style operating-metric extraction.
9. **Real mobile layout** — bottom nav, sticky ticker column, positions-on-the-go.

### Phase 5 — long-term / institutional moat
**Estimates database** (revision trends, estimate-momentum factor) · **entity graph** (cross-holdings, promoter networks — India-specific, nobody does it well) · **second data vendor / staleness alarms** · **AI research copilot** (natural-language "TCS DCF at 12% growth," auto-thesis with citations) · **broker connect for decision-support** (surface orders to place — you approve and execute; never auto-traded).

### The 4 I'd do next, in order
**(1) Earnings-surprise signal → (2) Alerts on Alpha/revisions → (3) Insider/FII-DII flow → (4) Saved & shareable scenarios.** The first two are self-contained and build directly on the ledgers now accruing; the others need a vendor endpoint. Nifty 500 and options/charting/mobile are Phase 4 because each needs a new data feed or a large new build.

---

## 5. What was built this session (Alpha Score) — how to use it
- New nav tab **Ideas** → the Nifty 100 ranked by **Alpha Score** with a per-name factor breakdown (Value/Quality/Momentum/Low-Vol/Growth), filterable by sector.
- Backend: `app/factors.py` (pure, tested) + `GET /api/factors` (cached 5 min, visible universe). Weights live in `FACTOR_WEIGHTS` — one place to tune the strategy.
- It reads existing precomputed valuations + 1-yr price series, so it costs **no API quota** and refreshes with the daily recompute.

*Sources: platform pricing/features — Koyfin, WallStreetPrep, WallStreetZen; India platforms — Finology, Winvesta, RandomDimes; factor evidence — BacktestIndia (18-yr NSE); risk sizing — QuantInsti, QuantifiedStrategies, EnlightenedStockTrading; signals — Sigtrix, Nasdaq, Stockopedia, AAII. Full links in the chat transcript.*
