# EquityVerdict — Security Audit (read-only)
**Date:** 2026-07-19 · **Scope:** backend HEAD f774831 (FastAPI, AWS Mumbai: EC2+Docker+RDS+Caddy) + frontend HEAD cbdab47 (React/Vite/Vercel) · **Method:** static analysis + read-only DB/endpoint introspection + live probes of api.equityverdict.com (GET/HEAD/OPTIONS only; no intrusion testing against real users). No secret values reproduced. SEC-* IDs align with the companion full audit (`AUDIT_2026-07/`) for merge.

> **Note on the brief:** the task described Railway infra + a live "Claude thesis" LLM endpoint. Neither is current — infra is AWS Mumbai and the platform is AI-free (the LLM path is retired to a dormant, unreachable branch, tracked as SEC-09). Findings reflect the live system, not the brief.

## Verdict
**Genuinely well-secured for its stage. No S0/S1 security findings.** The homegrown auth is done correctly (constant-time compares, prod refuses to boot without a fixed secret), multi-tenancy is IDOR-proof, all SQL is parameterised, the SSRF guard is robust, admin routes fail closed, and the CSP/header set is tight. The real work is **abuse-hardening** (revocation, per-route cost caps, closing a signup email-bomb) and **hygiene** (one CVE-bearing dep, unpinned deps, a stale-comment TLS shortcut). **Safe to expose publicly from a breach/takeover standpoint; before *charging*, close the P2 abuse items** (a shared box with no per-route cost cap is a cheap DoS/spend target). Legal/licensing gating is out of scope here — see the compliance appendix.

---

## Attack-surface map

**Auth model.** Bearer token = `base64url({uid,email,exp}) . hex(HMAC-SHA256(payload, SECRET))`, 30-day expiry (auth.py:86-114). Passwords PBKDF2-HMAC-SHA256, 260k iterations, per-user salt (auth.py:55-73). `SECRET` from `AUTH_SECRET`; **prod refuses to start if unset** (auth.py:38-46) — no per-worker random-secret divergence. Single uvicorn worker → the in-memory limiter is coherent.

**Endpoint auth tiers** (123 routes):
- **Public, read:** `/api/health`, `/api/companies[/{t}]` + ~11 per-ticker sub-routes (quarterly/insights/annual_pl/ratios_live/news/forensics/financials/metrics/profile), `/api/factors`, `/api/peer_universe`, `/api/screen/technical`, `/api/results`, `/api/ownership`, `/api/market/*`, `/api/live`, `/api/macro/*`, `/api/mutual-funds/*`, `/api/ipo/*`, `/api/backtest`, `/api/baskets`, `/api/compare`, `/api/bse/*`, `/api/documents`, `/api/logo/*`, `/api/intraday/*`.
- **Public, heavy-compute (⚠ SEC-02):** `POST /api/companies/{t}/valuation`, `/onepager` (ReportLab PDF), `/api/strategy/backtest`, `/api/screen/technical`.
- **Auth required** (`Depends(get_current_user)`): `/api/auth/me`, `/api/watchlist/*`, `/api/portfolio/*`, `/api/scenarios` (save/list/delete), `/api/screens/*`, `DELETE /api/auth/account`, Dhan holdings sync.
- **Admin** (`Depends(require_admin)`, fail-closed on unset ADMIN_EMAILS): `/api/admin/*` (~18: users, errors, coverage, run-backfill, recompute-valuations, macro refresh/upload, ingest/*, dhan-totp, auth-events, segment-financials), `/api-usage`.
- **Unauthenticated auth flows:** `/api/auth/{signup,verify,resend-code,login}` — throttled 10/min/IP.

**User-data tables (PII):** `User` (email, name, PBKDF2 hash), `WatchlistItem`, `PortfolioHolding` (holdings + buy dates), `SavedScenario`, `SavedScreen`, `AuthEvent` (email+IP+UA, indefinite), `kv_store` (`pending_signup:*` holds a hashed code + PBKDF2 hash transiently, 30-min TTL).

**Server-fetches-user/vendor-URL (SSRF surface):** `transcript_nlp.py` fetches transcript URLs from vendor/stored data — **SSRF-guarded** (see strengths). `logo_routes.py` fetches logos. No endpoint fetches a URL supplied directly in a request body.

**File-parsing surface:** broker CSV/PDF import (frontend `pdfImport`/`brokerImport` parse client-side; backend receives structured holdings), `bse_results_ingester`, `pdfplumber` on stored filings.

**Dependencies:** backend — fastapi 0.115.0, uvicorn 0.30.6, sqlalchemy 2.0.35, pydantic 2.9.2, pg8000 1.31.2, **requests 2.32.3 (⚠ SEC-05)**, reportlab 4.2.5, boto3 1.34.162, cryptography (unpinned), pdfplumber/httpx/dnspython/bse (unpinned). Frontend — React 19, Vite, pdfjs-dist ^4.10.38 (past CVE-2024-4367).

---

## Findings

### SEC-01 Session tokens cannot be revoked; password change does not invalidate them
- **Severity:** S2 **Likelihood:** Med **Effort:** M **Priority:** P2 · **Status:** New
- **Location:** app/auth.py:86-114; app/auth_routes.py:224-232 (no logout/revoke)
- **Evidence:** Stateless 30-day HMAC token `{uid,email,exp}`; no server session store, no `jti`, no `pwd_changed_at`/token-version claim (verified: `create_token` payload is exactly uid/email/exp, auth.py:88-90). Revocation is possible only via account deletion or rotating the global AUTH_SECRET (which nukes every session).
- **Why it matters:** A token captured via SEC-07 (localStorage), a shared device, or a leak grants 30 days of access to portfolio/holdings/PII with no way to cut it off — and changing the password does not help.
- **Fix:** Add a `tv` (token-version) or `pwd_changed_at` claim, stored on `User`; reject tokens older than the stored value; bump it on password change and expose "sign out all devices." Shorten TTL 30d→~7d with silent refresh.
- **Verification:** Bump a user's token-version in the DB; the old token 401s, a fresh login works.

### SEC-02 Unauthenticated heavy-compute endpoints — cost-of-abuse / DoS on a 2 GB box
- **Severity:** S2 **Likelihood:** Med **Effort:** S **Priority:** P2 · **Status:** New
- **Location:** app/main.py:695 `POST /api/companies/{t}/valuation`; :720 `/onepager`; `/api/strategy/backtest`; `/api/screen/technical`. Limiter main.py:120-190.
- **Evidence:** These are public and guarded only by the 240 req/min/IP *general* bucket. `/onepager` runs full financials + engine + a multi-page ReportLab PDF per call; a single uvicorn worker on a t3.small (2 vCPU/2 GB). 240 heavy builds/min/IP, or a handful of rotating IPs, saturate CPU/RAM — the same memory-pressure profile that already caused a swap-death outage (per the perf lane, `/api/factors` alone is 15 s cold).
- **Why it matters:** A cheap, unauthenticated way to degrade or knock over the API for all users; no attacker account needed.
- **Fix:** Add a tight per-route bucket (5–10/min/IP) for `/onepager` and `/valuation`; consider requiring auth for the PDF; cache `/onepager` output per (ticker, valuation vintage).
- **Verification:** Loop `POST /onepager`; confirm 429 after the cap and that `/api/companies` p95 stays flat during the loop.

### SEC-03 Signup path email-bombs any address; only /resend-code is throttled
- **Severity:** S2 **Likelihood:** Med **Effort:** S **Priority:** P2 · **Status:** New (verification flow is new at HEAD f774831)
- **Location:** app/auth_routes.py:72-115 (signup sends on every call) vs :201-221 (`resend_code` has a 60s/address cooldown)
- **Evidence:** `signup` calls `mailer.send_email` on every request for any not-yet-registered address; the per-address `sent_at` cooldown exists **only** in `resend_code`. The auth limiter (10/min/IP) caps rate per source IP, so an attacker can send ~10 verification emails/min to a chosen victim per IP (more across IPs).
- **Why it matters:** Turns signup into an email-bombing tool aimed at arbitrary third parties, and burns the GoDaddy/Titan SMTP send quota and sender reputation (already fragile — 500 sends/day cap, reputation freshly established).
- **Fix:** Apply the same per-address `sent_at` cooldown + an hourly per-address cap to the signup send path; optionally a global hourly send ceiling.
- **Verification:** Two signups within 60 s for one address → the second returns `pending_verification` **without** a second email leaving the server.

### SEC-04 CORS defaults to wildcard in code (latent; correctly locked in prod)
- **Severity:** S3 **Likelihood:** Low **Effort:** XS **Priority:** P3 · **Status:** Partially fixed (June item)
- **Location:** app/main.py:231-239
- **Evidence:** `origins = os.getenv("FRONTEND_ORIGIN", "*")` → `allow_origins=["*"]` when unset. **Live probe** with `Origin: https://evil.example` returned no `Access-Control-Allow-Origin` header → prod env is set correctly. `allow_credentials=False` + Bearer (not cookie) auth further limit impact even if it regressed.
- **Fix:** Fail closed — default to `https://equityverdict.com` (or raise at boot) when the env var is unset, so a future misconfig can't silently open CORS.
- **Verification:** Unset FRONTEND_ORIGIN in a scratch env; boot refuses or defaults to the branded origin, not `*`.

### SEC-05 requests 2.32.3 (CVE-2024-47081) + several unpinned deps
- **Severity:** S3 **Likelihood:** Low **Effort:** XS **Priority:** P3 · **Status:** New
- **Location:** requirements.txt
- **Evidence:** `requests==2.32.3` is affected by CVE-2024-47081 (.netrc credential leak on maliciously-crafted URLs), fixed in 2.32.4 — low practical exposure here (no `.netrc` in the image, API keys passed via headers not URLs, and the only user-influenced fetch is SSRF-guarded). `pdfplumber`, `httpx`, `dnspython`, `cryptography`, `bse` are unpinned → non-reproducible builds and silent transitive upgrades.
- **Fix:** `requests>=2.32.4`; pin every backend dependency (ideally hash-pin via pip-compile); add `pip-audit` + `npm audit` gates to CI.
- **Verification:** `pip-audit` clean; `pip freeze` matches a locked manifest.

### SEC-06 Encrypted backups derive their key with bare SHA-256, not a KDF
- **Severity:** S3 **Likelihood:** Low **Effort:** S **Priority:** P3 · **Status:** New
- **Location:** app/backup.py:40-47 (`_fernet`)
- **Evidence:** Weekly full-DB dumps (users, PBKDF2 hashes, emails, portfolios) are Fernet-encrypted to R2. The Fernet key = `base64(sha256(BACKUP_KEY))` — no salt, single iteration. If a ciphertext blob leaks and `BACKUP_KEY` is low-entropy, the key is offline brute-forceable.
- **Why it matters:** The backups are the single richest PII target and they leave the box; the encryption is only as strong as a fast unsalted hash over the passphrase.
- **Fix:** Derive the key via scrypt or PBKDF2 (≥200k) over a stored random salt; enforce a minimum `BACKUP_KEY` length at boot.
- **Verification:** New backups decrypt with the KDF path; old-format detection documented for restore.

### SEC-07 Auth token stored in localStorage (well-mitigated by CSP + no XSS sinks)
- **Severity:** S3 **Likelihood:** Low **Effort:** S **Priority:** P3 · **Status:** New
- **Location:** src/lib/auth.js:7-37
- **Evidence:** The Bearer token lives in `localStorage` (JS-readable). Strong mitigants verified: **zero** `dangerouslySetInnerHTML`/`innerHTML`/`eval` in `src/`, React auto-escaping, and a tight CSP (`script-src 'self'`, `connect-src 'self' https://api.equityverdict.com`, `object-src 'none'`, `frame-ancestors 'none'`). Residual risk is a future XSS or a malicious dependency exfiltrating the token, which SEC-01 then makes non-revocable for 30 days.
- **Fix:** Move to a `Secure; HttpOnly; SameSite=Strict` cookie (server-side change), or at minimum land SEC-01 revocation + a shorter TTL.
- **Verification:** Token no longer readable from `document`/JS; auth still works cross-navigations.

### SEC-08 Auth ledger IP taken from the spoofable leftmost XFF hop
- **Severity:** S3 **Likelihood:** Low **Effort:** XS **Priority:** P3 · **Status:** New
- **Location:** app/auth_routes.py:34-37 (`_record_event`)
- **Evidence:** The audit-ledger IP is `x-forwarded-for.split(",")[0]` (client-settable). The *rate limiter* correctly uses the trusted rightmost hop (main.py:145-156), but the ledger does not — so `AuthEvent.ip` / admin "last IP" is attacker-controllable. This is forensic poisoning, not a control bypass.
- **Fix:** Reuse the limiter's `_client_ip` trusted-hop logic in `_record_event`.
- **Verification:** Send a login with a forged XFF; the ledger records the trusted-hop IP, not the forged value.

### SEC-09 Dormant Anthropic LLM path (latent cost/prompt-injection if ever enabled)
- **Severity:** S4 **Likelihood:** Low **Effort:** XS **Priority:** P3 · **Status:** Still open (latent)
- **Location:** scheduler.py:530-543 (`run_transcript_ingest`); admin trigger app/admin_routes.py:318-324 (`/ingest/transcripts?llm=true`)
- **Evidence:** The LLM branch runs only if `ANTHROPIC_API_KEY` is set **and** `TRANSCRIPT_LLM ∈ {1,true,yes}` — neither is set in prod, and `transcript_ingester` documents the `with_llm` params as no-ops (llm_summary is never set). If a key is ever added, untrusted vendor/BSE transcript text would flow into an LLM with no injection guard and no spend cap. (Also a DPDP cross-border concern — companion CMP-07.)
- **Fix:** Delete the `with_llm` branch and the `llm` admin param (the platform is AI-free), or leave a hard `raise` behind the flag.
- **Verification:** Grep shows no reachable LLM call; the admin param 400s.

### SEC-10 Live-error responses return the raw exception string (without DEBUG)
- **Severity:** S4 **Likelihood:** Low **Effort:** XS **Priority:** P3 · **Status:** New
- **Location:** app/main.py:688-692 (`company_detail`), :832-836 (`onepager`)
- **Evidence:** On a 500 these return `{"error": str(e)}` unconditionally (only the *full traceback* is DEBUG-gated). A DB/driver message could surface internal detail (table/column names, driver internals). 404 paths verified clean.
- **Fix:** Return a generic "internal error" to the client unless `_debug_enabled()`; the real detail already goes to the self-owned error log.
- **Verification:** Force a handled 500; the client sees a generic message, the error log captures the detail.

### SEC-11 RDS connection encrypts but does not verify the server certificate
- **Severity:** S3 **Likelihood:** Low **Effort:** S **Priority:** P3 · **Status:** New (cross-lane from perf)
- **Location:** app/database.py:23-27
- **Evidence:** For `*.rds.amazonaws.com`, the pg8000 SSL context is built with `check_hostname = False` and `verify_mode = ssl.CERT_NONE` (traffic is encrypted but the server cert is not validated). The code comment already flags the intent to "tighten to verify-full by shipping the RDS CA bundle." Practical exposure is low: app→RDS traffic stays inside the AWS VPC/security group, so an active MITM would require in-VPC position.
- **Fix:** Ship the AWS RDS ap-south-1 CA bundle into the image and set `verify_mode = CERT_REQUIRED` + `check_hostname = True` (verify-full).
- **Verification:** Connection succeeds with the CA bundle; connection to a wrong-cert endpoint fails.

---

## Confirmed strengths (do not regress)
- **IDOR-proof multi-tenancy** — every user-scoped route derives `user_key = f"u{user.id}"` **server-side from the verified token**, never from a request field; single-object reads/deletes filter by `id` **AND** `user_key` (e.g. screens_routes.py:64 `filter_by(id=screen_id, user_key=uk)`). Changing an ID in a request cannot reach another user's data.
- **Parameterised ORM throughout** — no f-string/`.format`/`%` SQL `execute()` anywhere in `app/` (verified by grep). No injection surface.
- **Robust SSRF guard** — transcript_nlp.py:29-59 resolves DNS and rejects the fetch unless **every** resolved IP is a routable public address, blocking loopback / RFC-1918 / link-local (incl. `169.254.169.254` metadata) / ULA / reserved / multicast, re-validated on each redirect hop.
- **Auth done right** — PBKDF2-HMAC-SHA256 260k iterations, constant-time `hmac.compare_digest` for both password and token-signature checks, prod **refuses to boot** without a fixed `AUTH_SECRET` (no per-worker secret divergence).
- **Real rate limiter** — sliding 60 s window, **trusted rightmost-hop** XFF parsing (spoofing the leftmost hop cannot mint fresh buckets), 10/min on `/api/auth/*`, memory bounded at 50k keys with opportunistic sweep. (Minor: the code comment still says "Railway's single edge proxy"; the value `TRUSTED_PROXY_HOPS=1` remains correct for the single Caddy hop on AWS — update the comment.)
- **Admin fail-closed** — `require_admin` 403s when `ADMIN_EMAILS` is unset (admin_routes.py:25-27).
- **Email verification** — 5-attempt lockout, constant-time code compare, `/resend-code` always returns 200 (no account enumeration).
- **Tight edge** — full security-header set + CSP (`script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`), HSTS at Caddy, `allow_credentials=False`.

## Ranked security backlog
| Priority | ID | Sev | Eff | Title |
|---|---|---|---|---|
| **P2** | SEC-01 | S2 | M | Token revocation / invalidate-on-password-change |
| **P2** | SEC-02 | S2 | S | Per-route cost cap on onepager/valuation (unauth DoS) |
| **P2** | SEC-03 | S2 | S | Signup email-bomb — add per-address send cooldown |
| P3 | SEC-05 | S3 | XS | requests≥2.32.4 + pin/audit all deps |
| P3 | SEC-11 | S3 | S | RDS TLS verify-full (ship CA bundle) |
| P3 | SEC-06 | S3 | S | Backup key via scrypt/PBKDF2 + salt |
| P3 | SEC-07 | S3 | S | Token → HttpOnly cookie (or SEC-01 + short TTL) |
| P3 | SEC-04 | S3 | XS | CORS fail-closed default |
| P3 | SEC-08 | S3 | XS | Ledger IP from trusted hop |
| P3 | SEC-09 | S4 | XS | Delete dormant LLM branch |
| P3 | SEC-10 | S4 | XS | Generic 500 body, no raw exception string |

## Quick Wins (high value ÷ low effort — do this week)
1. **SEC-03** (S) — per-address signup send cooldown. Stops third-party email-bombing and protects the fragile SMTP reputation. *Highest security ROI.*
2. **SEC-05** (XS) — bump `requests` to ≥2.32.4 and pin the rest; add `pip-audit`/`npm audit` to CI.
3. **SEC-02** (S) — a 5–10/min/IP bucket on `/onepager` + `/valuation`; removes the cheapest DoS lever.
4. **SEC-04** (XS) + **SEC-08** (XS) + **SEC-10** (XS) — one small hardening commit: CORS fail-closed, ledger trusted-hop IP, generic 500 body.
5. **SEC-09** (XS) — delete the dead LLM branch (closes a latent injection/spend/DPDP vector on an AI-free platform).

## Prior-audit reconciliation (security items)
| Prior item | Status | Evidence |
|---|---|---|
| AUDIT_2026-06 CORS `*` | **Partially fixed** | Prod locked (live probe: no ACAO for foreign origin); code default still `*` → SEC-04 |
| AUDIT_2026-06 Anthropic thesis cost/injection | **Mostly fixed** | thesis route retired to a stub; one dormant branch remains → SEC-09 |
| Internal D-series: SSRF guard | **Fixed** | transcript_nlp.py:29-59 validates every resolved IP + redirect hop |
| Internal D-series: AUTH_SECRET fail-fast | **Fixed** | auth.py:38-46 refuses ephemeral secret in prod |
| Internal D-series: admin query scaling / screener resilience | **Fixed** | present in current tree |
| COMPLIANCE §4 DPDP account deletion | **Fixed** | DELETE /api/auth/account with email re-confirm + cascade (auth_routes.py:244-264) |
| COMPLIANCE signup consent | **Fixed** | consent required at signup (auth_routes.py:86-87) |
| COMPLIANCE PBKDF2/HMAC/TLS/limits | **Confirmed** | 260k PBKDF2, HMAC tokens, TLS to RDS (verify-full pending → SEC-11), limiter live |

## Out-of-lane observations (for the companion Fable/compliance audit)
- `AuthEvent` (email + IP + UA) is retained indefinitely with no TTL/purge; backups (all PII) ship to R2 outside India (encrypted) — document a retention/purge policy and confirm the cross-border posture (DPDP).
- `src/App.jsx:359,398` reference `fonts.googleapis.com` but the CSP omits it from `style-src`/`font-src`, so those loads are CSP-blocked — privacy-positive, but they're dead external references (confirm the local `data:` font fallback renders).
- ~25 `.bak` files + 9 `probe_*.py` scratch scripts remain in the package tree — prune to shrink the reviewable surface.

## Bottom line
No takeover, IDOR, injection, SSRF, secret-exposure, or auth-bypass finding survived verification — the security fundamentals are solid. **Before charging users, close SEC-01/02/03** (the three P2 abuse items) and sweep the XS quick-wins; everything else is defense-in-depth. From a pure breach/exposure standpoint, it is safe to expose publicly today.
