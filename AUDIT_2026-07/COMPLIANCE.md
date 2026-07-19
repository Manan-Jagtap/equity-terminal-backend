# Appendix — Compliance, Legal & Disclosures (Agent 2, run by Lead)

Audit date 2026-07-19. Product: **EquityVerdict** (rebranded from Equity Terminal),
a paid-intent Indian-markets equity-research terminal publishing security-specific
BUY/HOLD/AVOID verdicts, target/intrinsic values, and a 7-factor Alpha Score.

> This is an **issues map, not legal advice.** Engage a SEBI-registered securities
> lawyer before charging the public. Reconciles the prior `COMPLIANCE.md` (4 Jul 2026)
> against current code + live behaviour.

---

### [CMP-01] SEBI Research Analyst registration — the launch blocker (unchanged)
- **Domain:** Compliance
- **Severity:** S0  **Likelihood:** High  **Effort:** L  **Priority:** P0
- **Status vs prior audits:** Still open (COMPLIANCE.md §1)
- **Location:** whole product — every verdict/target surface (`src/components/Company.jsx`, `Screener.jsx`, `FundManager.jsx`, `export_routes.py` one-pager)
- **Evidence:** Live API `GET /api/companies/TCS` and the screener return security-specific verdicts + intrinsic ₹ targets to the public; the product intends to charge (landing CTA, account tiers). No RA registration exists.
- **Why it matters:** Under the SEBI (Research Analyst) Regulations 2014 (as amended through 2024–25), issuing recommendations/target prices on listed securities **for consideration** is a regulated activity requiring RA/Research-Entity registration (NISM-XV, net-worth, per-report disclosures, COI policy, 5-yr records, annual compliance audit, SCORES grievance redressal). Charging without it is very likely a violation and the single biggest barrier to monetising.
- **Recommended fix:** Pick a path *before* charging: (1) **Register** (owner clears NISM-XV or forms a research entity) — strongest, and "SEBI-registered" is a trust signal; (2) **De-verdict the public tier** — show data/models/factor scores/MoS but strip BUY/AVOID action verbs and explicit targets for public users until registration lands; (3) **Partner** with a registered RA that adopts the output. Consult a securities lawyer on §1+§2 in one sitting.
- **Verification:** Written legal opinion on the chosen path; if registering, RA number displayed in-product + on every report.

### [CMP-02] Market-data redistribution rights — no workaround (unchanged)
- **Domain:** Compliance
- **Severity:** S0  **Likelihood:** High  **Effort:** L  **Priority:** P0
- **Status vs prior audits:** Still open (COMPLIANCE.md §3)
- **Location:** `app/live_prices.py` (Dhan LTP redistributed to end users), `app/logo_routes.py`, `app/ingest/indianapi_ingester.py`
- **Evidence:** Dhan Data APIs are licensed to the account holder for personal use; the platform serves Dhan-originated live prices + option chains to third-party end users of a commercial product. IndianAPI commercial-display rights for the current plan are unconfirmed in writing.
- **Why it matters:** Redistributing exchange-originated data to end users of a paid product breaches Dhan's terms and the underlying NSE/BSE data policy. This is a contractual + exchange-policy exposure with **no code workaround** — it needs a licence.
- **Recommended fix:** Secure a **direct exchange data-vendor agreement** (NSE Data & Analytics / BSE) or a redistributor licence that explicitly covers display to your end users (EOD is cheaper than real-time); get IndianAPI's commercial-display grant in writing. Confirm "Nifty"/index-name trademark usage terms with NSE Indices.
- **Verification:** Signed data-redistribution licence(s) on file covering every feed shown to users.

### [CMP-03] Investment-Adviser overlap — Portfolio "suggested weights" (still present)
- **Domain:** Compliance
- **Severity:** S1  **Likelihood:** Med  **Effort:** S  **Priority:** P1
- **Status vs prior audits:** Still open (COMPLIANCE.md §2)
- **Location:** `src/components/Portfolio.jsx:440,448` — `suggested_weight` column; label "Suggested weights are inverse-volatility (risk-balanced), capped at 25%. A sizing aid, not investment advice."
- **Evidence:** The X-ray computes personalized inverse-vol position sizes on the **user's own holdings**. The disclaimer is present but the activity is personalized, portfolio-specific guidance.
- **Why it matters:** Personalized position sizing is Investment Adviser territory (stricter than RA). A disclaimer helps but does not by itself de-regulate the activity.
- **Recommended fix:** Before charging, either gate/drop the `suggested_weight` column for public users, or reframe it as a generic non-personalized illustration ("risk-parity reference weights for an equal-conviction book") decoupled from the user's actual holdings — or obtain IA cover.
- **Verification:** Legal sign-off that the reframed feature is outside IA scope, or the column is gated behind registration.

### [CMP-04] No Terms of Service exists (still missing)
- **Domain:** Compliance
- **Severity:** S1  **Likelihood:** High  **Effort:** S  **Priority:** P1
- **Status vs prior audits:** Still open (COMPLIANCE.md §4 flagged "none exist today")
- **Location:** `src/components/` — only `PrivacyPolicy.jsx` exists; no `TermsOfService`/`ToS` component; no acceptance record beyond the privacy-consent checkbox.
- **Evidence:** `grep -i terms/tos` in `src/` returns only privacy-policy references. Signup stores privacy consent but there is no ToS to accept.
- **Why it matters:** A paid product needs enforceable terms: limitation of liability, "not advice", acceptable-use, data-source disclaimers, governing law. Absent ToS materially raises liability if a user acts on a verdict and loses money.
- **Recommended fix:** Publish a ToS (liability cap, "research/educational only, not advice", no warranty on data accuracy, third-party data disclaimer, Indian governing law/jurisdiction), link it beside the Privacy Policy, and require acceptance at signup (extend the existing consent checkbox to cover both).
- **Verification:** ToS live, linked in signup + footer; consent row records ToS version accepted.

### [CMP-05] Privacy Policy is stale and routes grievances to a personal Gmail
- **Domain:** Compliance
- **Severity:** S2  **Likelihood:** High  **Effort:** XS  **Priority:** P1
- **Status vs prior audits:** Partially fixed (policy now exists — was missing in §4) but inaccurate
- **Location:** `src/components/PrivacyPolicy.jsx:31,55,77`
- **Evidence:** (a) Dated "Effective 4 July 2026", pre-AWS-migration; describes storage as generic "managed cloud infrastructure" — does not state data now resides in **AWS Mumbai (ap-south-1)**, a DPDP-positive fact worth stating. (b) Says deletion is by **emailing**, actioned "within 30 days" — but a self-serve `DELETE /api/auth/account` endpoint + UI now exists (understates capability). (c) Grievance/DPDP contact is **`mananjagtap27@gmail.com`** (a personal Gmail), not a branded role address, on a commercial financial product.
- **Why it matters:** DPDP Act 2023 requires accurate notice, a clear grievance channel, and (at scale) a named grievance officer. A personal Gmail as the sole data-principal contact is weak and unprofessional; stale storage/deletion claims misdescribe actual processing.
- **Recommended fix:** Update effective date; name AWS ap-south-1 as the storage region; describe both self-serve and email deletion (self-serve is immediate); switch the grievance contact to a branded address (e.g. `privacy@equityverdict.com` / `grievance@equityverdict.com`) and name a grievance officer before scale. Have a lawyer review alongside the ToS.
- **Verification:** Updated policy live; branded contact receives mail; deletion text matches the actual endpoint behaviour.

### [CMP-06] Stale AI-usage disclosure obligation resolved, but COMPLIANCE.md now misdescribes the product
- **Domain:** Compliance
- **Severity:** S3  **Likelihood:** Med  **Effort:** XS  **Priority:** P2
- **Status vs prior audits:** Fixed in product (AI retired) / doc regressed
- **Location:** `COMPLIANCE.md §5` (backend); reality: `app/thesis_routes.py` (retired stub), `app/news_routes.py:8`, `app/transcript_nlp.py:8` ("100% AI-free… no Anthropic call anymore").
- **Evidence:** The AI Thesis + transcript summaries the §5 obligation was written for are **retired** — the platform is AI-free, so the "disclose AI usage in research" obligation is currently moot. COMPLIANCE.md still presents them as live.
- **Why it matters:** The doc a lawyer/regulator would read misstates the product's AI posture (both ways: it claims AI features that no longer exist, and it doesn't note the *latent* re-introduction risk — see CMP-07/CLN residual path). Low legal risk today, but the reference doc must be trustworthy.
- **Recommended fix:** Rewrite `COMPLIANCE.md` to current reality: AI-free today; if any LLM feature is ever reintroduced, the AI-use-disclosure obligation (and DPDP cross-border processing, CMP-07) re-attaches and must be handled before shipping.
- **Verification:** COMPLIANCE.md reflects AI-free status and the conditional re-attach rule.

### [CMP-07] Latent DPDP cross-border risk: dormant ANTHROPIC path in the scheduler
- **Domain:** Compliance (data residency) / Cleanup
- **Severity:** S2  **Likelihood:** Low  **Effort:** XS  **Priority:** P2
- **Status vs prior audits:** New
- **Location:** `scheduler.py:~532-539` — `with_llm = bool(os.getenv("ANTHROPIC_API_KEY"…)) and …`
- **Evidence:** A dormant LLM code path survives the AI-free strip; it activates if `ANTHROPIC_API_KEY` is ever set. `ANTHROPIC_API_KEY` is currently **unset** in prod (verified), so it is inert.
- **Why it matters:** The platform's DPDP posture is "no third-party processors outside India" (data resident in ap-south-1). If this env var is ever set — even accidentally — the scheduler would send ingested filing/transcript text to Anthropic (US), silently breaching the stated data-residency commitment, with no gate re-checking that promise.
- **Recommended fix:** Delete the dormant `with_llm` branch (aligns with the "AI-free, never reintroduce without sign-off" policy). If it must stay as a future option, guard it with an explicit, documented residency acknowledgement rather than a bare env-var check.
- **Verification:** `grep -rn ANTHROPIC scheduler.py` returns nothing (or only a guarded, documented path).

### [CMP-08] Marketing claim "Independent equity research" — verify against the engine
- **Domain:** Compliance (truth-in-advertising) / Data & Valuation
- **Severity:** S2  **Likelihood:** Med  **Effort:** S  **Priority:** P2
- **Status vs prior audits:** New (cross-lane with Agent 3)
- **Location:** `src/components/Landing.jsx:61` ("Independent equity research"), `:18` ("shows its work"), `:23` ("A track record that can't be faked")
- **Evidence:** Headline positioning is "independent". Whether the published intrinsic value is truly independent depends on whether the DCF blends analyst targets / clamps to an analyst band (Agent 3 is quantifying this). The LOW-CONF path uses a **consensus fallback** (shipped feature) — where a call is shown, it may be analyst-derived.
- **Why it matters:** If a material weight of the "independent" number is analyst consensus, "independent" overstates the product for a paid financial service (truth-in-advertising) and undercuts the core differentiator.
- **Recommended fix:** Pending Agent 3's quantification: if the primary intrinsic is analyst-independent (DCF 55% + exit EV/EBITDA + sector P/E), keep the claim but label the consensus-fallback path explicitly as "analyst consensus (model low-confidence)". If the primary number is analyst-anchored, soften "independent" or make the model genuinely independent. Only claim "can't be faked" if the track record is provably point-in-time (Agent 3 to confirm).
- **Verification:** Claim wording matches the engine's actual analyst dependence; consensus-fallback calls are labelled as such.

---

## Prior COMPLIANCE.md reconciliation (4 Jul 2026 → now)
| § | Prior item | Current status | Evidence |
|---|---|---|---|
| §1 | SEBI RA registration | **Still open** (P0) | verdicts+targets still public, no registration |
| §2 | IA overlap (suggested weights) | **Still open** (P1) | Portfolio.jsx:440,448 still renders it |
| §3 | Data-redistribution licensing | **Still open** (P0) | Dhan LTP still served to end users |
| §4 | DPDP: privacy policy | **Partially fixed** | PrivacyPolicy.jsx exists but stale (CMP-05) |
| §4 | DPDP: ToS | **Still open** (P1) | no ToS component (CMP-04) |
| §4 | DPDP: account deletion | **Fixed** | DELETE /api/auth/account + UI live (auth_routes.py:244) |
| §4 | DPDP: signup consent | **Fixed** | required consent checkbox at signup |
| §5 | AI-use disclosure | **Moot/Fixed** | AI retired; latent risk → CMP-07 |
| §6 | Disclaimers present | **Holds** | disclaimers across 10 views + PDF (export_routes.py:299) |
| §6 | No trade execution | **Holds** | no broker/PMS surface |

**Net:** the two S0 launch-blockers (RA registration + data licensing) are **both still open** and are the reason the honest answer to "safe to charge users today?" is **No** — neither has a code fix; both need external agreements/registration. Everything else in this lane is code/copy work of ≤1 day each.
