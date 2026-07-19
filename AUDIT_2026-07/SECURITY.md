# Appendix — Security & Abuse (Agent 1)

**Verdict:** genuinely well-secured. No S0/S1. Live probes confirm prod is correctly configured (401 on protected routes, full security-header set, CORS locked against foreign origin, `*` code-default not active in prod). Counts — S0:0 · S1:0 · S2:3 · S3:5 · S4:2.

---

### [SEC-01] Session tokens cannot be revoked; password change does not invalidate them
- **Domain:** Security  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** app/auth.py:86-114; app/auth_routes.py:224-232 (no logout); src/lib/auth.js
- **Evidence:** Stateless 30-day HMAC tokens `{uid,email,exp}`; no server session store, no `jti`, no `pwd_changed_at`/token-version claim. Revocation only via account deletion or global AUTH_SECRET rotation (nukes all sessions).
- **Why it matters:** A captured token grants 30 days of access to portfolio/watchlist/PII with no way to cut it off. Compounds SEC-07.
- **Fix:** Add `tv`/`pwd_changed_at` claim on User; reject stale tokens; bump on password change + "sign out all devices"; shorten TTL 30d→~7d with silent refresh.
- **Verification:** Bump user token-version in DB; old token 401s, fresh login works.

### [SEC-02] Unauthenticated heavy-compute endpoints — cost-of-abuse / DoS on a t3.small
- **Domain:** Security  **Severity:** S2  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** app/main.py:695 POST /api/companies/{t}/valuation; :720 /onepager (reportlab); /api/strategy/backtest; /api/screen/technical. Limiter main.py:121-190.
- **Evidence:** Public, guarded only by 240 req/min/IP general limit. onepager runs full financials+engine+multipage PDF per call; single uvicorn worker (deploy/aws/Dockerfile:33). 240 heavy builds/min/IP × rotating IPs saturates the 2GB box.
- **Fix:** Tight per-route bucket (5–10/min/IP) for onepager+valuation, and/or require auth for PDF, and/or cache onepager per (ticker, valuation vintage).
- **Verification:** Loop POST /onepager; 429 after cap; /api/companies p95 stays flat.

### [SEC-03] Signup path email-bombs any address; only /resend-code is throttled
- **Domain:** Security  **Severity:** S2  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** New (verify flow new at HEAD f774831)
- **Location:** app/auth_routes.py:72-115 (signup sends every call) vs :201-221 (resend_code has 60s/address cooldown)
- **Evidence:** signup calls mailer.send_email on every request for any non-existing address; the 60s cooldown exists only in resend_code. Auth limiter 10/min/IP → 10 verification emails/min to a chosen victim per IP.
- **Why it matters:** Signup becomes an email-bombing tool at arbitrary third parties; burns GoDaddy/Titan SMTP quota + sender reputation (already fragile).
- **Fix:** Apply the same sent_at per-address cooldown + hourly per-address cap to the signup send path; optional global hourly ceiling.
- **Verification:** Two signups within 60s for one address → second returns pending without a second email.

### [SEC-04] CORS defaults to wildcard in code (latent; correctly locked in prod)
- **Domain:** Security  **Severity:** S3  **Likelihood:** Low  **Effort:** XS  **Priority:** P3  **Status:** Partially fixed (June item)
- **Location:** app/main.py:231-239
- **Evidence:** `origins=os.getenv("FRONTEND_ORIGIN","*")` → `["*"]` if unset. Live probe with Origin: https://evil.example returned no ACAO → prod is set correctly. `allow_credentials=False` + Bearer auth further mitigate.
- **Fix:** Fail closed — default to https://equityverdict.com (or raise) when env unset.

### [SEC-05] requests==2.32.3 CVE-2024-47081 + several unpinned deps
- **Domain:** Security  **Severity:** S3  **Likelihood:** Low  **Effort:** XS  **Priority:** P3  **Status:** New
- **Location:** requirements.txt
- **Evidence:** requests 2.32.3 → CVE-2024-47081 (.netrc cred leak), fixed 2.32.4 (low practical exposure — no .netrc, keys via headers, SSRF-guarded). pdfplumber/httpx/dnspython/cryptography/bse unpinned → non-reproducible builds. Frontend pdfjs-dist ^4.10.38 is past CVE-2024-4367 fix (keep pinned).
- **Fix:** requests>=2.32.4; pin every backend dep (hash-pin via pip-compile); pip-audit/npm audit in CI.

### [SEC-06] Encrypted backups derive their key with bare SHA-256, not a KDF
- **Domain:** Security  **Severity:** S3  **Likelihood:** Low  **Effort:** S  **Priority:** P3  **Status:** New
- **Location:** app/backup.py:40-47 (_fernet)
- **Evidence:** Weekly full-DB dumps (users, PBKDF2 hashes, emails, portfolios) Fernet-encrypted to R2 (outside India). Key = base64(sha256(BACKUP_KEY)) — no salt, single iteration → offline brute-forceable if ciphertext leaks + low-entropy passphrase.
- **Fix:** Derive key via scrypt/PBKDF2 (≥200k) over a stored random salt; enforce min BACKUP_KEY length.

### [SEC-07] Auth token in localStorage (well-mitigated by CSP + no XSS sinks)
- **Domain:** Security  **Severity:** S3  **Likelihood:** Low  **Effort:** S  **Priority:** P3  **Status:** New
- **Location:** src/lib/auth.js:7-37
- **Evidence:** Bearer token in localStorage (JS-readable). Strong mitigants: zero dangerouslySetInnerHTML/innerHTML/eval in src/, React escaping, tight CSP (script-src 'self', connect-src 'self' https://api.equityverdict.com, object-src 'none', frame-ancestors 'none'). Interacts with SEC-01 (exfiltrated token non-revocable 30d).
- **Fix:** Secure;HttpOnly;SameSite=Strict cookie (server change), or at least SEC-01 revocation + shorter TTL.

### [SEC-08] Auth ledger IP taken from spoofable leftmost XFF hop
- **Domain:** Security  **Severity:** S3  **Likelihood:** Low  **Effort:** XS  **Priority:** P3  **Status:** New
- **Location:** app/auth_routes.py:34-37 (_record_event)
- **Evidence:** Ledger IP = x-forwarded-for.split(",")[0] (client-settable). Limiter (main.py:145-156) correctly uses rightmost trusted hop; ledger does not → admin "last_ip" is attacker-controllable (forensic poisoning, not a control bypass).
- **Fix:** Reuse the limiter's _client_ip trusted-hop logic in _record_event.

### [SEC-09] Dormant Anthropic LLM path (latent cost/injection if enabled)
- **Domain:** Security  **Severity:** S4  **Likelihood:** Low  **Effort:** XS  **Priority:** P3  **Status:** Still open (latent)
- **Location:** scheduler.py:530-543 (run_transcript_ingest); admin trigger app/admin_routes.py:318-324 (/ingest/transcripts?llm=true)
- **Evidence:** LLM runs only if ANTHROPIC_API_KEY set AND TRANSCRIPT_LLM in (1,true,yes); neither set in prod. thesis_routes.py retired stub. If a key is ever added, vendor/BSE transcript text → LLM (untrusted-content-into-LLM + spend), no injection guard. (Also a DPDP cross-border item — see CMP-07.)
- **Fix:** Delete the with_llm branch + llm admin param, or leave a hard raise behind the flag.

### [SEC-10] Live-error endpoint returns raw exception string without DEBUG
- **Domain:** Security  **Severity:** S4  **Likelihood:** Low  **Effort:** XS  **Priority:** P3  **Status:** New
- **Location:** app/main.py:688-692 (company_detail), :832-836 (onepager)
- **Evidence:** On 500 these return {"error": str(e)} unconditionally (only full trace is DEBUG-gated). DB/driver message could surface internal detail. (404s confirmed clean.)
- **Fix:** Generic "internal error" unless _debug_enabled(); real detail already goes to the self-owned error log.

---

## Confirmed strengths (do not regress)
IDOR-proof multi-tenancy — every user route derives `user_key=f"u{user.id}"` server-side from the token; deletes filter by id AND user_key. Parameterized ORM throughout. SSRF guard resolves DNS + checks every resolved IP against loopback/RFC-1918/link-local(169.254.169.254)/reserved, re-validating each redirect hop (transcript_nlp.py:28-59). Admin fail-closed (require_admin 403s when ADMIN_EMAILS unset). Email-verify: 5-attempt lockout, constant-time compare; /resend-code always-200 (no enumeration). AUTH_SECRET refuses ephemeral start in prod. Single non-root worker → coherent in-memory limiter.

## Prior-audit reconciliation (security)
- AUDIT_2026-06 CORS `*` → Partially fixed (prod locked; code default still `*` → SEC-04). Anthropic thesis/cost → Mostly fixed (stub; one dormant path → SEC-09). Internal D-series (SSRF guard, AUTH_SECRET fail-fast, screener resilience, admin query scaling) → Fixed in current tree.
- COMPLIANCE §4 DPDP: account deletion Fixed (DELETE /api/auth/account, email re-confirm + cascade, auth_routes.py:244-264); signup consent Fixed; PBKDF2-260k/HMAC/TLS/limits confirmed.

## Cross-lane observations
- **Compliance:** AuthEvent stores IP+UA+email indefinitely (no TTL/purge). Backups (all PII) → R2 outside India (encrypted). Document a retention/purge policy.
- **Frontend:** src/App.jsx:359,398 reference fonts.googleapis.com but CSP omits it from style-src/font-src → those font loads are CSP-blocked (privacy-positive; dead external-font refs — confirm local data: font fallback renders).
- **Cleanup:** ~25 .bak files + 9 probe_*.py scratch scripts in the package tree — widen reviewable surface, prune.
