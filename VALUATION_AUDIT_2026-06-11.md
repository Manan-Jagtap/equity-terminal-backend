# Valuation Audit — Full Universe, Old vs New Engine (2026-06-11)

Every covered company re-run through both engines on **live production inputs**
(statements-derived assumptions fetched from the prod API, old values reproduced
to <0.5% before trusting the comparison — reproduction passed for all 49).

## What changed in the engine

1. **Two-stage FCFF growth** (engines.py + engine.js, parity-tested 60/60).
   The old DCF faded growth from year 1, silently cutting year-1 growth by 1/N
   and pricing durable franchises as if their advantage started dying
   immediately — the single biggest reason the model printed AVOID across
   India's quality cohort. Now: hold the derived stage-1 rate for half the
   horizon (mirroring the RI model's design), then fade linearly to terminal.

2. **Competitive-advantage period (CAP)** (derive.py). The fade horizon is now
   earned from the data instead of a flat 8 years:
   - Non-financials: 14y if ROIC ≥ 1.5× sector mature AND growth ≥ 8%;
     11y if ROIC ≥ 1.2× sector OR growth ≥ 12%; else 8y.
   - Financials: 12y if ROE ≥ 15% with payout ≤ 35% (compounders);
     10y if ROE ≥ 13%; else 8y. (One-sided: a sub-Ke lender never gets extra
     years, where they would wrongly subtract value.)
   - Commodity cyclicals (METAL/ENERGY): never extended — their "moat" is the
     cycle.

3. **Mid-cycle growth normalization for semi-cyclicals** (AUTO, CEMENT):
   stage-1 growth capped at 12% (vs 18% secular / 8% commodity). Without it,
   M&M's SUV up-cycle compounded to a +114% MoS — a peak-cycle artifact.

4. Carried from the previous pass: net-worth fallback = share capital +
   reserves (never bare share capital), synthetic price → NO DATA (never a
   fabricated MoS), and the FCFF terminal value already uses the steady-state
   reinvestment rate.

## Name-by-name: old vs new

| Ticker | Sector | Price ₹ | Old IV ₹ | New IV ₹ | Old MoS | New MoS | Δ | CAP | New zone |
|---|---|--:|--:|--:|--:|--:|--:|--:|---|
| ITC | CONSUMER | 283 | 459 | 469 | +62.2% | +65.5% | +3pp | 8y | BUY zone |
| M&M | AUTO | 2,952 | 4,605 | 4,651 | +56.0% | +57.6% | +2pp | 11y | BUY zone |
| COALINDIA | ENERGY | 451 | 654 | 674 | +45.1% | +49.6% | +5pp | 8y | BUY zone |
| SBIN | BANK | 1,000 | 1,340 | 1,444 | +34.0% | +44.4% | +10pp | 12y | BUY zone |
| WIPRO | IT_SERVICES | 179 | 251 | 248 | +40.3% | +39.0% | -1pp | 8y | BUY zone |
| HCLTECH | IT_SERVICES | 1,132 | 1,455 | 1,519 | +28.6% | +34.2% | +6pp | 8y | BUY zone |
| TCS | IT_SERVICES | 2,153 | 2,749 | 2,791 | +27.7% | +29.6% | +2pp | 11y | BUY zone |
| INFY | IT_SERVICES | 1,144 | 1,424 | 1,469 | +24.5% | +28.4% | +4pp | 8y | BUY zone |
| ONGC | ENERGY | 252 | 320 | 316 | +27.3% | +25.6% | -2pp | 8y | BUY zone |
| BHARTIARTL | TELECOM | 1,775 | 1,512 | 2,111 | -14.8% | +19.0% | +34pp | 11y | BUY zone |
| DRREDDY | PHARMA | 1,271 | 1,361 | 1,387 | +7.0% | +9.1% | +2pp | 8y | ACCUMULATE zone |
| AXISBANK | BANK | 1,313 | 1,315 | 1,369 | +0.2% | +4.3% | +4pp | 10y | HOLD zone |
| NTPC | UTILITIES | 352 | 329 | 354 | -6.7% | +0.6% | +7pp | 8y | HOLD zone |
| GRASIM | CEMENT | 3,071 | 2,844 | 2,964 | -7.4% | -3.5% | +4pp | 11y | HOLD zone |
| TITAN | CONSUMER_DISC | 4,041 | 2,666 | 3,575 | -34.0% | -11.5% | +23pp | 11y | REDUCE zone |
| ICICIBANK | BANK | 1,290 | 1,053 | 1,140 | -18.4% | -11.7% | +7pp | 12y | REDUCE zone |
| LT | MANUFACTURING | 3,920 | 2,908 | 3,434 | -25.8% | -12.4% | +13pp | 11y | REDUCE zone |
| HEROMOTOCO | AUTO | 4,855 | 4,111 | 4,169 | -15.3% | -14.1% | +1pp | 11y | REDUCE zone |
| HDFCBANK | BANK | 747 | 620 | 627 | -17.0% | -16.0% | +1pp | 10y | REDUCE zone |
| KOTAKBANK | BANK | 388 | 311 | 321 | -19.8% | -17.4% | +2pp | 10y | REDUCE zone |
| POWERGRID | UTILITIES | 287 | 220 | 233 | -23.2% | -19.0% | +4pp | 8y | REDUCE zone |
| ADANIPORTS | MANUFACTURING | 1,821 | 1,145 | 1,475 | -37.1% | -19.0% | +18pp | 11y | REDUCE zone |
| CIPLA | PHARMA | 1,375 | 1,109 | 1,105 | -19.4% | -19.6% | ≈ | 8y | REDUCE zone |
| BAJAJFINSV | NBFC | 1,663 | 996 | 1,294 | -40.1% | -22.2% | +18pp | 12y | REDUCE zone |
| TECHM | IT_SERVICES | 1,480 | 1,098 | 1,107 | -25.9% | -25.3% | +1pp | 8y | AVOID zone |
| SHRIRAMFIN | NBFC | 896 | 635 | 658 | -29.1% | -26.6% | +3pp | 12y | AVOID zone |
| BAJAJ-AUTO | AUTO | 10,145 | 6,691 | 6,618 | -34.0% | -34.8% | -1pp | 11y | AVOID zone |
| BRITANNIA | CONSUMER | 5,176 | 3,256 | 3,331 | -37.1% | -35.6% | +1pp | 11y | AVOID zone |
| HINDUNILVR | CONSUMER | 2,169 | 1,259 | 1,258 | -41.9% | -42.0% | ≈ | 8y | AVOID zone |
| APOLLOHOSP | PHARMA | 8,492 | 3,899 | 4,746 | -54.1% | -44.1% | +10pp | 11y | AVOID zone |
| MARUTI | AUTO | 13,063 | 7,377 | 7,224 | -43.5% | -44.7% | -1pp | 11y | AVOID zone |
| SUNPHARMA | PHARMA | 1,783 | 920 | 952 | -48.4% | -46.6% | +2pp | 8y | AVOID zone |
| RELIANCE | ENERGY | 1,260 | 617 | 644 | -51.0% | -48.9% | +2pp | 8y | AVOID zone |
| TRENT | CONSUMER | 2,755 | 1,159 | 1,390 | -57.9% | -49.6% | +8pp | 11y | AVOID zone |
| TATACONSUM | CONSUMER | 1,108 | 509 | 556 | -54.1% | -49.9% | +4pp | 11y | AVOID zone |
| JIOFIN | NBFC | 231 | 115 | 115 | -50.2% | -50.2% | ≈ | 8y | AVOID zone |
| HINDALCO | METAL | 1,040 | 487 | 518 | -53.2% | -50.2% | +3pp | 8y | AVOID zone |
| JSWSTEEL | METAL | 1,269 | 592 | 626 | -53.3% | -50.6% | +3pp | 8y | AVOID zone |
| BAJFINANCE | NBFC | 884 | 395 | 429 | -55.3% | -51.4% | +4pp | 12y | AVOID zone |
| NESTLEIND | CONSUMER | 1,438 | 584 | 658 | -59.4% | -54.2% | +5pp | 14y | AVOID zone |
| ULTRACEMCO | CEMENT | 10,861 | 4,756 | 4,879 | -56.2% | -55.1% | +1pp | 11y | AVOID zone |
| TATASTEEL | METAL | 199 | 86 | 84 | -56.8% | -57.9% | -1pp | 8y | AVOID zone |
| ASIANPAINT | CONSUMER_DISC | 2,714 | 1,045 | 1,043 | -61.5% | -61.6% | ≈ | 8y | AVOID zone |
| BEL | MANUFACTURING | 408 | 139 | 151 | -65.9% | -62.9% | +3pp | 11y | AVOID zone |
| EICHERMOT | AUTO | 7,184 | 2,492 | 2,442 | -65.3% | -66.0% | -1pp | 11y | AVOID zone |
| HDFCLIFE | INSURANCE | 549 | 120 | 120 | -78.1% | -78.1% | ≈ | 8y | AVOID zone |
| ETERNAL | CONSUMER | 239 | 41 | 45 | -82.9% | -81.4% | +2pp | 11y | AVOID zone |
| SBILIFE | INSURANCE | 1,732 | 131 | 131 | -92.4% | -92.4% | ≈ | 8y | AVOID zone |
| ADANIENT | METAL | 2,927 | 181 | 204 | -93.8% | -93.0% | +1pp | 8y | AVOID zone |

*(MoS zone is indicative — final verdicts also gate on composite score and data
confidence. Insurers (HDFCLIFE, SBILIFE), JIOFIN, ETERNAL, RELIANCE and
ADANIENT remain LOW CONF by design: embedded-value businesses, near-zero
current earnings, and conglomerates need P/EV and SOTP models — see roadmap.)*

## Reading the result

- **The lift is selective, not a re-anchoring.** Quality compounders with real
  ROIC durability re-rate toward (not onto) market prices: TITAN −34%→−12%,
  L&T −26%→−12%, Bajaj Auto unchanged ~−35%, while HUL (−42%), Nestlé (−54%),
  Asian Paints (−62%), Apollo (−44%) stay firmly negative — the model still
  says India's most expensive defensives are priced beyond even a 14-year
  excess-return runway. That is a defensible independent view, not a bug.
- **Banks normalize.** The 12y CAP for compounding financials moves ICICI
  −18%→−12%, Axis +0%→+4%, SBI +34%→+44%; HDFC Bank stays −16% on genuinely
  depressed post-merger ROE.
- **Cyclicals are protected.** Metals/energy keep their 8y horizon and 8%
  growth cap; ONGC/Coal India barely move. Autos/cement now value mid-cycle
  economics (M&M +114%→+58%).
- **Largest verdict-zone moves to review on screen:** GRASIM (REDUCE→HOLD),
  BHARTIARTL (REDUCE→BUY zone), TITAN (AVOID→REDUCE), L&T (AVOID→REDUCE),
  NTPC (HOLD, now ≈fair), BAJAJFINSV (AVOID→REDUCE).
