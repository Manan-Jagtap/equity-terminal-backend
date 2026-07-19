# Appendix — Frontend, UX & Accessibility (Agent 5)

Live walk (desktop 1280×720 + mobile 375×812, signed in as test account) vs source HEAD cbdab47; contrast computed from theme.js. All 16 views + company pages render, no crashes. **Headline:** strong, coherent product (disciplined Aurora theme, good empty states, skeletons, code-splitting, best-in-class valuation depth). Weaknesses in accessibility, SEO, disclaimer coverage, and a few trust-killing data figures. No fully-blocked core flow. Counts — S1:0 · S2:5 · S3:6 · S4:4.

---

### [UX-01] "Not investment advice" disclaimer absent on most logged-in verdict views
- **Domain:** Frontend/UX (+ Compliance)  **Severity:** S2  **Likelihood:** High  **Effort:** S  **Priority:** P1  **Status:** Partially fixed
- **Location:** MarketDashboard, Watchlist, Compare, Sectors, Results, TrackRecord, Baskets, Operations (0 disclaimer refs). Present only in Screener, Company (valuation-tab footer ~3086), Ideas, Portfolio.
- **Evidence:** Compulsory login → the strong Landing disclaimer is seen once logged-out and never again. Watchlist/Compare/Sectors/Track Record/Baskets publish verdicts/MoS with no disclaimer in view. Company sticky header shows VERDICT/FAIR VALUE/UPSIDE but disclaimer only on the Valuation tab, not the default Business tab.
- **Fix:** One shared `<Disclaimer/>` primitive in the App.jsx `<main>` shell so every view inherits it; keep the denser note on the valuation tab.

### [UX-02] No visible keyboard focus indicator (outline removed, no replacement)
- **Domain:** Frontend/UX — WCAG 2.4.7  **Severity:** S2  **Likelihood:** High  **Effort:** S  **Priority:** P1  **Status:** New
- **Location:** `outline:"none"` across AuthModal:73, CommandPalette:182, Screener:209-283, Portfolio:254,596, Compare:139, FundManager:183, MFPanel:178, ScenarioBar:80. Global `:focus`/`:focus-visible` → zero custom styles.
- **Evidence:** Search, all Screener filter dropdowns + MoS input, portfolio inputs, ⌘K/Auth inputs strip the outline with no replacement.
- **Fix:** Global `:focus-visible{outline:2px solid var(--accent);outline-offset:2px}` in index.html; remove ad-hoc outline:none.

### [UX-03] Trust: broken data figures on primary views (ROE 0.0% green, +11,800% MoS)
- **Domain:** Frontend/UX trust (root cause: data/engine)  **Severity:** S2  **Likelihood:** High  **Effort:** M (engine)/XS (UX guard)  **Priority:** P1  **Status:** New
- **Location:** Company header metric row; Sectors cheapest/richest; Screener default MoS-desc sort
- **Evidence:** TCS shows **ROE 0.0%** (should be ~50%) rendered in green (reads positive). Sectors: "CHEAPEST Rajesh Exports +11,800.7%" MoS, LIC +453%, Clean Max +1122%. Screener default sort surfaces a wall of +150–196% MoS small-caps first (Senco +196.7%).
- **Why it matters:** First-time trust dies on a blue-chip showing 0% ROE and an 11,800% margin of safety on the most-viewed screens. (Cross-lane: DAT-02/DAT-01.)
- **Fix:** Engine fixes ROE derivation + caps/flags implausible MoS. UX guard: render N/M (isMeaningfulMultiple exists) for ROE≤0; clamp/badge MoS above a sanity threshold as LOW CONF.

### [UX-04] Low-contrast `faint` text on sub-labels, hints, and disclaimer copy
- **Domain:** Frontend/UX — WCAG 1.4.3  **Severity:** S2  **Likelihood:** Med  **Effort:** XS  **Priority:** P2  **Status:** New
- **Location:** theme.js faint:#526180, vfaint:#39466B (primitives Stat sub, CommandPalette hints, AuthModal privacy note, FvRange:76, Landing compliance footer:99)
- **Evidence (measured):** faint on base #060A13 = **3.19:1** (FAIL); on panel2 = 2.80:1 (FAIL); vfaint on base = 2.13:1 (FAIL). Rest of palette fine (dim 6.08, text200 12.2, green 11.4, accent 12.5).
- **Fix:** Lighten faint to ~#6B7C9E+ (≥4.5:1 on base); reserve vfaint for non-text decoration.

### [UX-05] Hash routing + missing meta blocks SEO / crawlable public pages
- **Domain:** Frontend/UX — SEO  **Severity:** S2 (business)  **Likelihood:** High  **Effort:** L  **Priority:** P1  **Status:** New
- **Location:** App.jsx hash router (#/company/TCS), index.html, public/ (no robots.txt/sitemap.xml), vercel.json (no rewrites)
- **Evidence:** All routes serve one client-rendered index.html; deep links behind #/ fragments crawlers ignore + a compulsory login gate. index.html has og:title/description only — no og:image/url/type, no Twitter card, no canonical. No sitemap/robots.
- **Why it matters:** Zero organic discoverability; shares render no preview; the owner's public-SEO-pages goal cannot ship on this architecture.
- **Fix:** Prerendered/SSR public read-only company routes at real paths /company/TCS; add og:image, canonical, sitemap.xml, robots.txt. Meaningful project, not a config tweak — roadmap (= task #117).

### [UX-06] Modals lack dialog semantics, focus trap, focus restore
- **Domain:** Frontend/UX — WCAG 4.1.2/2.4.3  **Severity:** S3  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** AuthModal, CommandPalette, PrivacyPolicy — no role="dialog", aria-modal, inert background, or focus-trap
- **Evidence:** Autofocus + Esc work, but Tab escapes to the page behind; SR not told a dialog opened; focus not restored to trigger on close.
- **Fix:** role="dialog" aria-modal + accessible name + focus-trap (or inert on app root) + restore focus on close.

### [UX-07] prefers-reduced-motion unsupported (shimmer/spin/blink always run)
- **Domain:** Frontend/UX — WCAG 2.3.3 + vestibular  **Severity:** S3  **Effort:** XS  **Priority:** P2  **Status:** New
- **Location:** index.html @keyframes ev-shimmer/spin; App.jsx fadein/blink/spin; Skeleton.jsx; ScoreCard.jsx. No prefers-reduced-motion anywhere.
- **Fix:** Global `@media (prefers-reduced-motion: reduce){*{animation-duration:.001ms!important;animation-iteration-count:1!important;transition:none!important}}`.

### [UX-08] Charts have no text alternative; tables/tabs lack ARIA
- **Domain:** Frontend/UX — WCAG 1.1.1/1.3.1/4.1.2  **Severity:** S3  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** PriceChart/ChartTerminal/IndexChart (SVG/canvas, one aria-label total); primitives TH/MTable (no caption/scope); Company 15 tab buttons (no role=tab/aria-selected/tablist)
- **Fix:** role="img"+aria-label summary on charts; scope="col" in TH, caption on MTable; role="tablist"/"tab"+aria-selected on Company tabs.

### [UX-09] Company page: 15 tabs overflow horizontally, weak scroll affordance
- **Domain:** Frontend/UX  **Severity:** S3  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** Company.jsx tab bar (1280px: OWNERSHIP/NEWS/DOCS/AI THESIS/FORENSICS/VERDICT need horizontal scroll behind a thin scrollbar)
- **Fix:** Edge fade + right chevron, or wrap to two rows / overflow "More" menu.

### [UX-10] Mobile company page removes all global navigation
- **Domain:** Frontend/UX — Responsive  **Severity:** S3  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** App.jsx:518,652 — mobile header + bottom tab bar gated on view !== "company"
- **Evidence:** Mobile company page has no bottom nav/header/search — only "Back to screener." Dead-ends the deep-link flow.
- **Fix:** Keep the bottom tab bar (or a persistent search FAB) on mobile company pages.

### [UX-11] Mobile data tables lack a sticky first column
- **Domain:** Frontend/UX — Responsive  **Severity:** S3  **Effort:** S  **Priority:** P2  **Status:** Still open (prior audit)
- **Location:** primitives MTable overflow-x:auto; no position:sticky
- **Fix:** position:sticky;left:0 on the first td/th with opaque bg.

### [UX-12] background-attachment:fixed aurora is a scroll-repaint risk
- **Domain:** Frontend/UX — Perf  **Severity:** S4  **Effort:** S  **Priority:** P3  **Location:** theme.js:91 auroraBg
- **Fix:** Drop `fixed` or move aurora to a position:fixed behind-content layer.

### [UX-13] Raw enum sector labels leak into Sectors UI ("CONSUMER_DISC")
- **Domain:** Frontend/UX — Copy  **Severity:** S4  **Effort:** XS  **Priority:** P3  **Location:** Sectors.jsx
- **Fix:** Title-case + de-underscore display map.

### [UX-14] "highlighted in gold" copy vs mint-cyan accent (rebrand residue)
- **Domain:** Frontend/UX — Copy  **Severity:** S4  **Effort:** XS  **Priority:** P3  **Location:** Compare.jsx subtitle; token gold now renders #3EE6C1
- **Fix:** "gold" → "highlighted"/"accent" in user-facing copy.

### [UX-15] Bad/delisted deep-link silently shows a stale view
- **Domain:** Frontend/UX  **Severity:** S4  **Effort:** S  **Priority:** P3  **Location:** App.jsx:283-295
- **Fix:** "Ticker not found — back to screener" state.

---

## ErrorBoundary assessment
Well-built: getDerivedStateFromError catches render throws, recoverable "Something went wrong" + Retry, logs to console, auto-resets on resetKey={view}. Smart lazyReload auto-reloads once on stale-chunk failure after deploy, loop-guarded. Gaps: (a) doesn't reset on Company sub-tab change (resetKey is view, not sub-tab) — acceptable given manual Retry; (b) no telemetry hook (could POST to backend error_log.py).

## Positive / resolved (vs PRODUCT_REVIEW_2026-06)
Auth (was Critical "no auth") → Fixed (compulsory login + email verification + DPDP delete). "11 nav tabs exceed width" → Fixed (left rail + mobile bottom bar + More sheet). "No skeletons" → Fixed. "800kB single bundle" → Fixed (code-split). "Keyboard command language" → Fixed (⌘K parses "TCS DCF at 12% growth"). Empty states → Excellent. Still open: sticky first column mobile (UX-11); dark-only, no light/print theme.

## Quick Wins
1. Global :focus-visible + drop outline:none (UX-02). 2. prefers-reduced-motion media query (UX-07). 3. Lighten faint token (UX-04). 4. Shared <Disclaimer/> in main shell (UX-01). 5. N/M guard ROE≤0 + MoS sanity badge (UX-03 UX half). 6. Title-case sectors (UX-13), fix "gold" copy (UX-14). 7. robots.txt + og:image + canonical (partial UX-05).

## Cross-lane observations
- **Data/Engine:** ROE 0.0% on TCS; MoS up to +11,800% render on Sectors/Screener (UX-03) = engine bugs manifesting as trust-killers (→ DAT-01/02/04). KISSHT sector "Unknown"/NO CALL visible (→ DAT-live-01).
- **Compliance:** disclaimer coverage gap (UX-01) — Compliance sets severity.
- **Performance:** aurora background-attachment:fixed (UX-12); Fund Manager cold load ~13s (→ PERF-01).
