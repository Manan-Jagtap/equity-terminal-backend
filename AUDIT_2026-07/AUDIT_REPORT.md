# EquityVerdict — Full-Scope Audit Report
**Date:** 2026-07-19 · **Method:** Lead + 6 specialist agents (Security, Data/Valuation, Code/Architecture, Frontend/UX, Performance; Compliance + Cleanup by Lead) · **Basis:** backend HEAD f774831, frontend HEAD cbdab47, live probing of api.equityverdict.com + equityverdict.com.

Full findings live in the per-domain appendices (SECURITY.md, COMPLIANCE.md, DATA_VALUATION.md, CODE_ARCHITECTURE.md, FRONTEND_UX.md, PERFORMANCE.md, CLEANUP_MANIFEST.md) + SYSTEM_MAP.md. This is the executive layer.

---

## 0. Read this first — the brief was written for a product that no longer exists
Three premises in the audit brief are stale (June Railway/yfinance/Claude era). Verified against live code:
- **Infra:** NOT Railway — it's **AWS Mumbai** (EC2+Docker+RDS+ECR+S3+Caddy). Railway compute deleted.
- **Data:** NOT yfinance for valuations — **Dhan** (prices) + **IndianAPI** (fundamentals). One flaky yfinance intraday path remains.
- **AI:** NO Claude/AI thesis — **fully retired and AI-free** (one dormant, inert LLM code path remains → delete it).

These aren't just corrections; the stale planning docs that still describe the old world (HANDOFF.md, DEPLOY_NOTES.md, COMPLIANCE.md) are themselves findings (ARC-06, CLN-08, CMP-06) — an operator following HANDOFF.md today would deploy to dead infrastructure.

## 1. Overall verdict: is it safe to charge users today?

**No — but the blockers are legal/licensing, not engineering.** The software is in genuinely good shape: no critical security holes, a valuation core that is mathematically correct to the decimal, and a coherent, well-built UI. What stops you charging is (a) two external launch-blockers you cannot fix in code — SEBI Research-Analyst registration and market-data redistribution licensing — and (b) **one live correctness bug publishing a wrong BUY recommendation right now (DAT-01)** that must be fixed before anyone pays for a verdict.

**Risk posture by domain:**
- **Security — strong.** Zero S0/S1. No IDOR, SQLi, auth bypass, or committed secrets; SSRF guard verified; admin fail-closed; strict CSP. Real items are abuse-hardening (rate limits, token revocation).
- **Compliance — the gating risk.** Two S0 launch-blockers (RA registration, data licensing) with no code workaround; plus missing ToS, a stale privacy policy, and an Investment-Adviser-adjacent feature. Engage a SEBI securities lawyer before charging.
- **Data & Valuation — sound core, one live S0.** The parity-tested DCF/RI engine is correct; the **out-of-parity SOTP model (alt_models.py) prints ~2× fair values and a flipped VEDL verdict on production now.** Plus a loose plausibility gate passing confident +170% BUYs.
- **Code & Architecture — clean, one real drift bug.** 209/209 tests green; layered design sound. But frontend/backend verdict logic has **already silently diverged** (TRIM), and the parity harness doesn't cover it.
- **Frontend/UX — strong, with accessibility + trust gaps.** No blocked flow; excellent empty states/skeletons. Gaps: no visible focus ring, thin ARIA, disclaimer coverage, and engine bugs surfacing as trust-killers (TCS "ROE 0.0%", "+11,800% MoS").
- **Performance & Reliability — good discipline, single-instance fragility.** Indexing solid, timeouts everywhere, graceful degradation. But a 15s uncached `/api/factors` can re-trigger the swap-death OOM, and scheduler death / backup failure are invisible.

## 2. The 10 things that matter most (in order)
1. **DAT-01 [S0, XS]** — SOTP divides by a hardcoded stale share count → VEDL shows **BUY +123% (₹575)** when the truth is ~15% *downside* (₹219); BAJAJFINSV/GRASIM ~2× overstated. **Live now. One-line fix.**
2. **CMP-01 [S0, external]** — Publishing paid BUY/AVOID verdicts + targets needs **SEBI RA registration**. Launch-blocker.
3. **CMP-02 [S0, external]** — **Market-data redistribution licence** (Dhan/IndianAPI are personal-use). No code workaround.
4. **PERF-01 [S2→P0, S]** — `/api/factors` 15s cold, no shared cache/stampede lock on the 2GB box → repeats the swap-death OOM class. Cheap fix (memoize + lock).
5. **DAT-02 [S1, M]** — Plausibility gate at +200% is too loose → confident **+173% BUY on REDINGTON**, +197% on SENCO (sector-misfit names). "High confidence" reflects data completeness, not model fit.
6. **ARC-01 [S2→P1, S]** — Frontend verdict logic has **already diverged** from backend (still emits "TRIM"); parity harness covers valuation math only, not verdicts.
7. **UX-01 + UX-03 [S2, S/XS]** — Most logged-in verdict views show no "not investment advice" disclaimer; TCS renders **ROE 0.0% in green** and Sectors shows **+11,800% MoS** — first-impression trust-killers.
8. **PERF-02 + PERF-03 [S2, S/M]** — Scheduler death, stalled ingests, and backup failure are **invisible**; backups are weekly with an **untested restore** on a single RDS instance.
9. **ARC-02 [S2, S]** — A dead **"AI Thesis"** tab still advertises AI research and says it's "being enabled for this account" on an explicitly AI-free platform.
10. **CMP-03 + CMP-04 [S1, S]** — Personalized "suggested weights" stray into Investment-Adviser territory; **no Terms of Service** exists.

## 3. Prioritised backlog (P0 → P3)

### P0 — do before charging anyone
| ID | Domain | Sev | Eff | Title | vs prior |
|---|---|---|---|---|---|
| DAT-01 | Data | S0 | XS | SOTP stale share count → wrong published verdicts (VEDL BUY→REDUCE) | New |
| CMP-01 | Compliance | S0 | L | SEBI RA registration for paid verdicts/targets | Still open |
| CMP-02 | Compliance | S0 | L | Market-data redistribution licence (Dhan/IndianAPI personal-use) | Still open |
| PERF-01 | Perf/Rel | S2 | S | Heavy list endpoints: no shared cache/stampede lock → OOM risk | Partially fixed |

### P1 — before external launch
| ID | Domain | Sev | Eff | Title | vs prior |
|---|---|---|---|---|---|
| DAT-02 | Data | S1 | M | Loose +200% plausibility gate → confident +170% BUYs on sector-misfits | Regressed |
| DAT-live-01 | Data | S1 | M | 18 NO-DATA marquee stubs (TATAMOTORS/EQUITASBNK) — ticker↔vendor alias | New (task #123) |
| ARC-01 | Code | S2 | S | FE/BE verdict logic diverged (TRIM), parity gap | Regressed |
| ARC-02 | Code | S2 | S | Dead "AI Thesis" tab still promises AI ("being enabled") | New |
| UX-01 | Frontend | S2 | S | Disclaimer absent on most logged-in verdict views | Partially fixed |
| UX-02 | Frontend | S2 | S | No visible keyboard focus indicator (WCAG 2.4.7) | New |
| UX-03 | Frontend | S2 | XS/M | Broken figures on primary views (ROE 0.0% green, +11,800% MoS) | New |
| UX-05 | Frontend | S2 | L | Hash routing + missing meta blocks SEO/public pages | New (task #117) |
| CMP-03 | Compliance | S1 | S | IA overlap — personalized "suggested weights" | Still open |
| CMP-04 | Compliance | S1 | S | No Terms of Service | Still open |
| PERF-02 | Perf/Rel | S2 | S–M | Scheduler death / staleness unobservable | New |
| PERF-03 | Perf/Rel | S2 | M | Weekly backups, untested restore, silent failure (single instance) | New |

### P2 — quality, trust, hardening
| ID | Domain | Sev | Eff | Title |
|---|---|---|---|---|
| SEC-01 | Security | S2 | M | Tokens non-revocable for 30d; password change doesn't invalidate |
| SEC-02 | Security | S2 | S | Unauth heavy-compute (onepager/valuation) DoS on t3.small |
| SEC-03 | Security | S2 | S | Signup path email-bombs any address (only resend throttled) |
| DAT-03 | Data | S2 | M | Alt-model (SOTP/insurer) inputs hardcoded, stale, unversioned |
| DAT-04 | Data | S3 | S | Per-company beta is dead — everyone gets the sector prior |
| DAT-05 | Data | S2 | S | Track record shows negative BUY−AVOID edge (31d) — caveat sample size |
| ARC-03 | Code | S2 | M | 20 silent `except: pass` mask ingest failures |
| ARC-04 | Code | S2 | M | Triple boot-migration (Alembic+create_all+hardcoded ALTER) foot-gun |
| ARC-05 | Code | S2 | M | Parity harness false confidence (static snapshot, partial surface) |
| ARC-06 | Code | S2 | S | HANDOFF.md/DEPLOY_NOTES.md describe retired Railway infra as live |
| ARC-07 | Code | S2 | M | Missing tests: valuation/ingestion HTTP paths, verdict parity |
| PERF-04 | Observ | S2 | S | errors_1h (sole alert) undercounts caught-and-returned 500s |
| PERF-05 | Perf | S3 | M | _all_latest_facts loads entire FinancialFact table into Python |
| PERF-06 | Perf | S3 | S | /api/peer_universe N+1 (~1000 queries/build) |
| PERF-07 | Perf | S3 | S | /api/results + /api/ownership uncached (1–1.5s every hit) |
| PERF-08 | Perf | S3 | M | No pagination on large list payloads |
| CMP-05 | Compliance | S2 | XS | Privacy policy stale; grievance to a personal Gmail |
| CMP-06 | Compliance | S3 | XS | COMPLIANCE.md misdescribes product (AI as live) |
| CMP-07 | Compliance | S2 | XS | Latent DPDP cross-border: dormant ANTHROPIC path |
| CMP-08 | Compliance | S2 | S | "Independent"/beta marketing vs reality (verify wording) |
| UX-04 | Frontend | S2 | XS | Low-contrast faint text (3.19:1) incl. disclaimer copy |
| UX-06 | Frontend | S3 | M | Modals lack dialog semantics/focus trap/restore |
| UX-07 | Frontend | S3 | XS | prefers-reduced-motion unsupported |
| UX-08 | Frontend | S3 | M | Charts no text alt; tables/tabs lack ARIA |
| UX-09 | Frontend | S3 | S | Company page 15 tabs overflow, weak affordance |
| UX-10 | Frontend | S3 | S | Mobile company page removes all global nav |
| UX-11 | Frontend | S3 | S | Mobile tables lack sticky first column (still open) |
| CLN-01 | Cleanup | S2 | XS | Frontend backend/ folder (dead duplicate) — owner priority |
| CLN-02 | Cleanup | S2 | XS | No .dockerignore → dev clutter ships in prod image |

### P3 — polish, defense-in-depth, cleanup
SEC-04 (CORS fail-closed), SEC-05 (requests CVE + pin deps), SEC-06 (backup KDF), SEC-07 (token in localStorage), SEC-08 (XFF ledger IP), SEC-09 (delete dormant LLM path), SEC-10 (500 error string), SEC-11 (database.py ssl CERT_NONE — add RDS CA verification), ARC-08 (eslint 105 errors, no CI gate), ARC-09 (god-modules/main.py inline routes), ARC-10 (document ops endpoints), UX-12 (aurora background-attachment:fixed), UX-13 (raw enum "CONSUMER_DISC"), UX-14 ("gold" copy), UX-15 (bad-ticker deep-link feedback), PERF-09 (client fan-out/virtualization), CLN-03..07 (probe scripts, .bak, shadowed onepager.py, fyHelpers.js, engine.js:393), CLN-09 (=SEC-05).

## 4. Quick Wins — high value, ≤½ day, do this week
1. **DAT-01** (XS) — one-line SOTP share-count fix → corrects VEDL/BAJAJFINSV/GRASIM/ONGC/LT. *Highest value in the whole report.*
2. **PERF-01** (S) — memoize `ranked_visible` + rebuild lock → kills the 15s×2 Alpha compute and the OOM stampede.
3. **PERF-07** (XS) — 5-min cache on `/api/results` + `/api/ownership`.
4. **UX-02** (S) — global `:focus-visible` ring. **UX-07** (XS) — reduced-motion media query. **UX-04** (XS) — lighten `faint`. **UX-01** (S) — shared `<Disclaimer/>` in the app shell. **UX-03** (XS) — N/M guard for ROE≤0 + MoS sanity badge. **UX-13/UX-14** (XS) — sector labels + "gold" copy.
5. **SEC-03** (S) — signup send cooldown (stops third-party email-bombing + SMTP-reputation damage). **SEC-05** (XS) — requests≥2.32.4 + pin deps.
6. **ARC-02** (S) — hide the dead AI Thesis tab / honest copy. **ARC-06** (S) — fix HANDOFF.md/DEPLOY_NOTES.md off Railway.
7. **CLN-01** (XS) — `git rm -r backend/` (frontend). **CLN-02** (XS) — add `.dockerignore`.
8. **PERF-02/03** (S) — alert on backup non-ok + scheduler heartbeat/staleness in `/api/health`.

## 5. Prior-audit reconciliation (headline)
| Prior finding | Status now |
|---|---|
| C1 DCF on yfinance/flat-1.0 beta | Partially fixed (stored statements; beta now sector-level not per-company → DAT-04) |
| C2 two intrinsics (page vs screener) | **Fixed** (single Valuation source; verified to the decimal) |
| C3 "TRIM" verdict | **Fixed backend / Regressed frontend** (recommend.js still emits it → ARC-01) |
| C4 consensus overlay defeats independence | **Fixed** (no analyst blend; consensus a separate labelled block) |
| C5 dead compute_valuations | **Fixed** |
| C6 HDFCBANK NBFC mis-template, ROE 68-92% | **Fixed** (BANK, ROE 12.97%) |
| C7 coarse taxonomy / dead sector P/E | **Fixed** (25+ valuation sectors) |
| §3 Anthropic thesis/extract/news | **Fixed** (deleted; stub + one dormant path → SEC-09/CMP-07) |
| §3 probe scripts ×9 / .bak ×25 | **Still open** (CLN-03/04) |
| CORS `*` | Partially fixed (locked in prod; code default still `*` → SEC-04) |
| DPDP account deletion / signup consent | **Fixed** (DELETE /api/auth/account; consent required) |
| Privacy policy / ToS | Partial (policy exists but stale → CMP-05; ToS still missing → CMP-04) |
| "No auth" (Critical) | **Fixed** (compulsory login + email verification) |
| "11 tabs / no skeletons / 800kB bundle / no ⌘K" | **Fixed** (left rail, skeletons, code-split, command palette) |
| Mobile sticky first column | **Still open** (UX-11) |

## 6. Suggested remediation sequencing
**Phase A — Correctness & legal truth (this week).** DAT-01 (the wrong live BUY) + PERF-01 (OOM risk) + the disclaimer/trust quick-wins (UX-01/03/04, ARC-02). Start the CMP-01/CMP-02 lawyer engagement in parallel — it has the longest lead time and gates revenue.
**Phase B — Trust & correctness depth (1–2 weeks).** DAT-02 (plausibility gate + distributor sector), DAT-04 (beta live or fix copy), ARC-01 (verdict parity harness), DAT-live-01 (ticker aliases), CMP-03/04/05 (ToS + IA reframe + privacy refresh).
**Phase C — Reliability & observability (1–2 weeks).** PERF-02/03/04 (scheduler heartbeat, RDS PITR + daily backup + restore test, error counting), SEC-01/02/03 (token revocation, expensive-route limits, signup cooldown).
**Phase D — Accessibility, SEO, perf polish.** UX-02/06/07/08/09/10/11 (WCAG pass), UX-05 (public SEO pages — the biggest single project; = task #117), PERF-05/06/07/08 (query/caching).
**Phase E — Cleanup & hardening.** CLN-01..09 (dead code, .dockerignore, doc refresh), SEC-04..11, ARC-08/09/10.

**Cheap-to-batch together:** all UX quick-wins (one CSS/shell pass); all doc refreshes (HANDOFF/DEPLOY_NOTES/COMPLIANCE/ARCHITECTURE); all cleanup removals (one commit each repo); dependency pinning + CI lint/audit gates.
