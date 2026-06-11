# Equity Terminal — Full Product Review (11 June 2026)

Reviewed live at equity-terminal-one.vercel.app against the production API, after
the June 2026 engineering pass (engine v2, track record, Excel export, sectors,
portfolio, documents, global search, AI notes).

## Overall rating: 7.5 / 10

For what it is — a transparent, independent-valuation research terminal for the
Indian market built by one person — this is remarkably strong. The 7.5 is earned
by the valuation engine's transparency (every assumption traceable to source
data), the falsifiable Track Record, and a coherent, professional interface.
What separates it from an 9: universe breadth (51 names vs thousands), no
authentication (blocks real users), single data vendor, and three structurally
unmodelled segments (insurers, conglomerates, true holdcos).

| Dimension | Score | One-line basis |
|---|--:|---|
| UI/UX design | 8.0 | Distinctive institutional gold-on-ink aesthetic, consistent across 11 views; dense but legible |
| Information architecture | 7.0 | 11 flat nav tabs is at its limit; Company page concentrates 9 sub-tabs well |
| Feature depth | 8.0 | Valuation depth is genuinely best-in-class for India retail; breadth still narrow vs the giants |
| Data quality | 7.0 | Strong trust layer (confidence gating, basis-mismatch traps); single-vendor + corporate-action risk remains |
| Performance | 7.5 | Precomputed valuations make the screener instant; 800 kB single bundle, no code-splitting |
| Mobile responsiveness | 6.0 | Responsive hooks exist and tables scroll, but 11 nav tabs wrap awkwardly <500px; no bottom nav |
| Professionalism / investor readiness | 7.5 | One-pagers, Excel models, public track record = credibility; needs auth + disclaimers before external users |

## Investor perspective (the person putting money to work)

What works: a single screen answers "is this cheap and why" — independent fair
value, margin of safety, a verdict that is *gated by data confidence* (the
terminal says LOW CONF/NO DATA instead of bluffing — rarer than it should be),
analyst consensus kept honestly separate, watchlist alerts on verdict changes,
and now a portfolio with value-weighted MoS. The Track Record page is the
single most trust-building feature: the model grades its own calls in public,
nothing backfilled.

What's missing for an investor: total-return thinking (the track record and
P&L are *price* returns — dividends are ignored, which understates ITC/Coal
India-type calls materially), no benchmark comparison (vs NIFTY TR), no
transaction history/tax lots in the portfolio, and no mobile-first experience
for checking positions on the go.

## Equity research analyst perspective (the professional)

What works: assumption provenance (`_drivers` shows exactly how every growth,
margin and reinvestment number was derived), the two-stage CAP framework is
defensible and documented, JS/Python engine parity is *tested*, reverse DCF
answers the right question, sensitivity + Monte Carlo are standard-grade, the
Excel export ships the actual model schedule (not screenshots), and concall
transcripts/annual reports are now one click away.

What an analyst would flag: scenarios aren't persistable (slider work is lost
on navigation); no editable three-statement model (exports are outputs, not a
driver-linked workbook); estimates have no revision history (consensus is a
point-in-time number); segment data exists in presets but SOTP is frontend-only;
insurer valuation (P/EV) absent so HDFCLIFE/SBILIFE are honestly-but-unhelpfully
LOW CONF; gold-loan/jewellery working-capital debt (TITAN) distorts WACC weights.

## UI/UX issues (current, after this pass's fixes)

- 11 top-level tabs + search exceed comfortable nav width; group into Research /
  Markets / Tools dropdowns or a left rail. (Medium)
- No loading skeletons — tabs flash a spinner then jump to dense content. (Low)
- Tables on <500 px rely on horizontal scroll with no sticky first column —
  ticker scrolls out of view. (Medium)
- Number alignment is excellent; but MoS green/red is the only encoding —
  add the same bar/heat treatment the screener has to Sectors/Portfolio. (Low)
- No dark/light toggle; print/PDF of screens (other than the one-pager) comes
  out illegible on white printers. (Low)

## Functional bugs (known, open)

- FEDFINA is classified BANK (name contains "…bank") and valued on bank
  parameters; it is an NBFC. Impact small (beta 0.95 vs 1.10). (Medium)
- `valuation.js` still contains a second, divergent DCF implementation; only
  `fundamentals/isFinancial` are imported. Dead code that will bite a future
  contributor. (Medium)
- Dead components linger (`FinancialStatements/Fundamentals/Technical/Verdict
  .jsx`, unused DCFTab block in Company.jsx). (Low)
- Track Record marks open calls to the latest *stored* price; if intraday
  refresh fails silently the mark can be a day old with no staleness flag. (Low)

## Data issues

- **Price returns, not total returns** in track record and portfolio P&L —
  dividends excluded. Most material data-correctness gap right now. (High)
- Corporate actions handled by heuristics, not an adjustment engine; the
  KOTAKBANK/BAJFINANCE split incidents were caught by the trust layer, but
  detection ≠ adjustment. (High)
- Single vendor (IndianAPI) for fundamentals AND prices; BSE scraping is the
  only secondary source. One outage = blind terminal. (High)
- Insurers lack EV/VNB data; conglomerates lack segment financials in the DB —
  both are LOW CONF by design rather than mis-valued, which is correct but
  leaves 4-5 large names without a usable answer. (Medium)
- Documents/insights refresh weekly; news is fresher than filings context. (Low)
- FY-2026 statement labels depend on the vendor's fiscal-year convention; spot
  checks pass but there is no automated FY-alignment audit. (Medium)

## Security concerns

- **No authentication on write endpoints** — watchlist and portfolio accept
  writes from anyone on the open internet, keyed to user_key="default".
  Fine for a personal tool; must be fixed before sharing the URL. (Critical
  when multi-user; today: High)
- CORS is `*` (FRONTEND_ORIGIN env exists but is unset). One env var to fix. (High)
- 500 handlers return traceback excerpts (`/api/companies/{t}`, onepager) —
  information disclosure; gate behind a DEBUG env. (Medium)
- No rate limiting; the xlsx/onepager endpoints are CPU-heavier and abusable. (Medium)
- Secrets hygiene is good (env vars only); the GitHub PAT pasted in chat during
  deployment should be revoked. (Housekeeping)
- Dependencies pinned in requirements.txt; no automated vulnerability scanning. (Low)

## To be Bloomberg / Capital IQ / FactSet-like

1. **Keyboard-first command language**: extend ⌘K from search to commands —
   "TCS DCF", "ITC NEWS", "SECTORS IT" jump straight to a tab. This is the
   single most Bloomberg-feeling upgrade and is now cheap to add.
2. **Estimates database**: store consensus snapshots (you already snapshot
   verdicts) → revision trends, beat/miss history, estimate momentum factor.
3. **Entity graph**: cross-holdings (Bajaj twins, Tata group), promoter
   networks, subsidiary maps — India-specific and none of the local
   competitors do it well.
4. **Total-return engine**: dividend-adjusted return series powering track
   record, portfolio, and a vs-NIFTY-TR benchmark line.
5. **Universe to Nifty 500**: the engine and ingestion generalize; constraint
   is API quota and parser hardening. Ship in sector tranches with the
   data-quality gate marking weak names LOW CONF.
6. **Saved scenarios + shared links**: persist DCF slider states per user,
   shareable URL → the collaboration primitive CapIQ sells.
7. **Auth + workspaces** (Supabase/Clerk): prerequisite for everything
   multi-user; unlocks per-user watchlists/portfolios that already have
   user_key plumbing.
8. **Transcript NLP**: transcripts are now linked; with an ANTHROPIC_API_KEY
   the thesis pipeline can be extended to summarize guidance/sentiment per
   quarter and diff quarter-over-quarter language.

## Priority ranking

**Critical**
- Auth on write endpoints + lock CORS via FRONTEND_ORIGIN (before any sharing)
- Corporate-action adjustment engine (splits/bonuses) — the #1 silent-wrong-number risk

**High**
- Total returns (dividends) in track record + portfolio
- Second price/fundamentals source or at least staleness alarms
- Universe expansion to Nifty 500 (sector tranches)
- Insurer P/EV model; SOTP backend for RELIANCE/ADANIENT
- Remove traceback disclosure; add rate limiting

**Medium**
- Nav IA regrouping; sticky first column on mobile tables
- FEDFINA → NBFC classification override; delete dead second engine + dead components
- Saved DCF scenarios; estimates revision history
- FY-alignment automated audit; code-splitting the 800 kB bundle

**Low**
- Loading skeletons, light/print theme, multi-watchlists, keyboard command
  language beyond search, transcript NLP summaries

## What was added in this pass (for the record)

Must-haves now complete (10/10): screener, financial statements, ratio
analysis, peer comparison, earnings data, shareholding pattern, valuation
models, news, **Excel export (screener + full per-company model workbook)**,
**global fast search (⌘K)**. Differentiators: **AI research notes** (validated
Claude pipeline, needs ANTHROPIC_API_KEY on Railway), **earnings-call
transcripts + annual reports + credit ratings per company**, broker consensus
aggregation (targets/ratings; full broker-PDF aggregation not legally
feasible), **sector dashboards**, **portfolio tracking**, custom watchlists
with alerts (pre-existing), plus the **Track Record** public backtest no
competitor offers.
