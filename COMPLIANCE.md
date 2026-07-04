# Compliance Review — before commercial public launch (4 July 2026)

> Written by an AI assistant. This is an issues map, NOT legal advice. Engage a
> securities lawyer before charging the public. Items are ranked by severity.

---

## 🔴 1. SEBI Research Analyst Regulations, 2014 — the launch blocker

The platform publishes **security-specific BUY / ACCUMULATE / HOLD / REDUCE /
AVOID verdicts and target values (intrinsic ₹) to the public**, and the plan is
to charge for access. Under the RA Regulations (as amended through 2024-25),
issuing research reports / recommendations on listed securities **for
consideration** is a regulated activity requiring **registration as a Research
Analyst / Research Entity** (NISM-XV certification, net-worth criteria, a
compliance regime: prescribed disclosures on every report, conflict-of-interest
policy, 5-year record keeping, annual compliance audit, grievance redressal via
SCORES, and — per recent SEBI guidance — disclosure of AI usage in research).

**Charging subscriptions for these verdicts without RA registration would very
likely be a violation.** Realistic paths:
1. **Register** (you personally clear NISM-XV, or form a research entity) and
   run the platform as a registered RA product — the strongest commercial
   position ("SEBI-registered" is itself a trust signal).
2. **Reframe the public product as an analytics TOOL**: show data, models,
   factor scores and margin-of-safety numbers but strip action verbs
   (BUY/AVOID) for public users until registration lands. Weaker but launches
   sooner; still a gray zone since SEBI's 2025 stance reads "research services"
   broadly.
3. **Partner** with an existing registered RA entity that adopts the platform's
   output under its registration.

## 🔴 2. Investment Adviser overlap — the Portfolio X-ray's "suggested weights"

The X-ray computes **personalized position-size suggestions on the user's own
holdings** (inverse-vol "suggested weight" per position). Personalized,
portfolio-specific guidance is Investment Adviser (IA) territory, a separate
and stricter registration than RA. Before launch either (a) drop/gate the
suggested-weights column, (b) reframe as a generic educational illustration
not tied to action ("risk-parity reference weights"), or (c) obtain IA cover.
The in-app disclaimer ("a sizing aid, not investment advice") helps but does
not by itself de-regulate the activity.

## 🔴 3. Market-data licensing — both feeds are personal-use

- **Dhan Data APIs**: licensed to the account holder for their own use.
  Redistributing exchange-originated prices/option chains to third-party users
  of a commercial platform breaches both Dhan's terms and the underlying
  NSE/BSE data policy that flows through them.
- **IndianAPI**: verify the plan tier's redistribution/commercial-display
  rights in writing before launch.
- For a paid public product you generally need either a **direct exchange
  data-vendor agreement** (NSE Data & Analytics / BSE) or a redistributor
  license that explicitly covers display to your end users. EOD data is
  cheaper to license than real-time but is NOT automatically free for
  commercial redistribution.
- Index names ("Nifty 50" etc.) are NSE Indices trademarks; descriptive use is
  common but a commercial product should confirm usage terms.

## 🟡 4. DPDP Act 2023 (personal data)

The platform stores emails, names, password hashes, portfolios and watchlists.
Before public launch:
- Publish a **privacy policy** + **terms of service** (none exist today).
- Signup must present notice + consent for the processing purpose.
- Provide **account deletion** (no endpoint exists today — build
  DELETE /api/auth/account cascading watchlist/portfolio/scenarios/screens).
- Breach-notification and grievance-officer obligations apply at scale.
Current safeguards (PBKDF2 260k, HMAC sessions, TLS, rate limits) are a good
baseline for "reasonable security safeguards".

## 🟡 5. AI-generated research content

The AI Thesis and transcript summaries are LLM-generated. SEBI's recent
circulars put responsibility for AI-assisted research squarely on the
registered entity, and expect **explicit AI-use disclosure**. The thesis
output should carry a visible "AI-generated, machine-validated against source
financials, may contain errors" label (partially present; make it uniform),
and the RA registration (path 1 above) must disclose AI usage.

## 🟢 6. What's already in decent shape

- "Not investment advice / educational" disclaimers exist across the app and
  (as of today) on the public landing page and every high-stakes panel.
- The platform never executes trades or handles client funds — keeps it out of
  broker/PMS regulation entirely.
- No dark patterns, no performance guarantees, and the public track record
  shows losses as prominently as wins (regulators specifically punish
  cherry-picked performance marketing).
- Verdict language is already confidence-gated and refuses calls on weak data
  — materially better than industry practice if/when SEBB scrutiny arrives.

## Launch sequencing recommendation

1. Consult a securities lawyer on §1/§2 (one consultation covers both).
2. Decide RA path (register vs. de-verdict the public tier vs. partner).
3. Secure data redistribution rights (§3) — this one has no workaround.
4. Ship privacy policy + ToS + account deletion (§4) — code work, ~a day.
5. Then commercialize.
