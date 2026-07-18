# Competitive Analysis & Roadmap — How We Beat Everyone

Researched against the leading Indian equity platforms (2026). The goal isn't to clone them — it's to identify our moat, defend it, and close the table-stakes gaps *in a way that leverages the moat*.

## The landscape

| Platform | Core strength | Price | What they're known for |
|---|---|---|---|
| **Screener.in** | 10-yr data depth + custom query builder | ₹10k/yr | The fundamentals gold standard; raw data + screening |
| **Tickertape** | Clean UI + "Investment Scorecard" | ₹2,399/yr | Beginner-friendly quality scores; stocks+MF+ETF |
| **Trendlyne** | DVM score (Durability/Valuation/Momentum), 1,400–3,000 screener params, alerts, backtests | ₹2,090/yr | Analyst targets, screeners, real-time alerts |
| **Tijori** | Operational/alt-data from annual reports (market share, revenue mix, store counts) — 6,000+ metrics | ₹3,500/yr | Segment & supply-chain granularity |
| **StockEdge** | Technical + fundamental scans | — | Scans, combo of TA + FA |

## Our moat — what NONE of them have

Every competitor gives you either **raw data** (Screener), a **black-box score** (Trendlyne DVM, Tickertape Scorecard), or **sell-side consensus targets**. **Not one of them gives a transparent, first-principles INDEPENDENT intrinsic value that you can open up, interrogate, and adjust.**

We do:
- A blended **independent fair value** (DCF + Residual Income + relative cross-checks) per stock, sector-aware.
- An **interactive DCF** — every assumption is a live slider; the number moves as you reason.
- **Honest "we can't model this"** — LOW CONF flags for life insurers (embedded value), conglomerates (need SOTP), and pre-profit names, instead of confidently-wrong numbers.
- An editable **Sum-of-the-Parts** for conglomerates, a **P/E-vs-own-history band**, and the model's verdict shown *beside* the Street's — never blended.

That combination is genuinely unique in the Indian retail market. A Trendlyne DVM "Valuation" score is a 1–100 black box; ours is an auditable ₹-per-share you can disagree with. **This is the hero. Everything else should orbit it.**

## The gaps (table stakes we don't yet have) — and how to close them *through* the moat

Ranked by impact. The trick: build each so it operates on OUR independent signals, not generic ratios — that's how a "me-too" feature becomes a differentiator.

### 1. Custom screener — highest priority
Screener.in and Trendlyne's core. We only have a fixed Nifty-50 dashboard. But our killer version screens on **outputs nobody else computes**: *"show me every stock >20% below our independent fair value, composite > 60, ROE > 15%, not LOW-CONF."* No competitor can screen on a transparent independent intrinsic value + margin of safety. This closes the biggest table-stakes gap AND is impossible for them to copy without our engine. (Pairs with expanding the universe beyond Nifty 50 — currently on hold.)

### 2. Forensic / accounting-quality red-flags
Tickertape's Scorecard and Trendlyne's DVM gloss over accounting integrity. Add **Beneish M-score** (earnings manipulation), **Altman Z** (bankruptcy risk), **Piotroski F-score** (fundamental momentum), plus **promoter pledge %, auditor changes, and accrual vs cash-flow quality**. We already have the statements to compute all of these. This turns our "Quality" pillar into a genuine forensic check — a trust feature that fits the "honest valuation" brand.

### 3. Watchlist + valuation alerts
Every competitor has watchlists; Trendlyne's edge is alerts. Ours alert on the proprietary signal: *"notify me when TCS trades below our fair value,"* or *"MoS crosses +25%,"* or *"verdict changes to BUY."* Needs lightweight user accounts (or local-storage watchlists to start, no login).

### 4. Side-by-side comparison
Compare 2–4 stocks across our fair value, MoS, verdict, the full ratio set, and the valuation bands — one screen. Cheap to build, high daily-use value.

### 5. Results / earnings tracking
Earnings calendar, results-vs-estimate surprise, and "what changed in the model after results." We already ingest forward estimates and quarterly data.

### 6. Institutional & MF ownership trends
Which mutual funds/FIIs own it and the quarter-on-quarter trend (Trendlyne/Tickertape have this). Partially in our Ownership tab — extend it.

### 7. Operational / segment data (Tijori's moat)
Market share, revenue mix, segment EBITDA. Hardest to get (needs annual-report extraction), but it's exactly what would make the conglomerate SOTP *auto-compute* instead of being a manual calculator. Long-term.

## Strategic recommendation

Don't try to out-Screener Screener.in on raw data depth or out-Tijori Tijori on segment granularity — those are years of data work. **Win on judgment, not data volume.** Our defensible position is: *"the only platform that shows you a transparent, independent fair value and is honest about what it can't value."*

Build order to be better than everyone:
1. **Custom screener on our independent signals** (closes the #1 gap, impossible to copy).
2. **Forensic red-flags** (deepens the trust moat).
3. **Watchlist + valuation alerts** (retention + the proprietary-signal hook).
4. **Comparison tool** + **results tracking** (daily-use polish).
5. Universe breadth, MF-ownership trends, segment data (longer-term, data-heavy).

Items 1–4 are all buildable on data we already ingest, and every one of them is *more* valuable specifically because it sits on top of the independent valuation engine the competitors don't have.
