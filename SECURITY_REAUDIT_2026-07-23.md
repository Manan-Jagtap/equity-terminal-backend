# EquityVerdict — Security Re-Audit (read-only + one fix)

**Date:** 2026-07-23 · **Scope:** backend HEAD `4287942` (FastAPI, AWS Mumbai: EC2+Docker+RDS+Caddy) + frontend live `equityverdict.com` (React/Vite/Vercel) · **Method:** static analysis of the current tree, verification of every prior `SEC-*` finding against live code, review of all attack surface added since the 2026-07-19 audit, and read-only live probes (GET/HEAD only; no intrusion testing). No secret values reproduced.

This is a **re-audit** of [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) (2026-07-19, HEAD `f774831`). It confirms the prior findings' remediation and audits the surface added since (macro MCP/DBnomics fetchers, the `/admin` ops panel, the telemetry beacon + feedback channel, macro file-upload, REIT/economy routes).

## Verdict

**Materially stronger than four days ago. Still no S0/S1, and now no open S2.** All three P2 abuse items the prior audit said to close *before charging* (SEC-01 token revocation, SEC-02 heavy-compute cost cap, SEC-03 signup email-bomb) are **fixed and verified live**. Every other prior finding is fixed or has its mitigation shipped. The new surface is well-built — feedback is auth-gated, the admin panel is fully `require_admin`-gated, the file-upload is capped, and the new fetchers introduce no SSRF or injection. One genuinely new low-severity item (SEC-12, telemetry retention) is fixed in this same change; two minor hygiene notes remain.

---

## Prior findings — remediation status (all 11)

| ID | Prior sev | Status now | Evidence (current code) |
|---|---|---|---|
| **SEC-01** token revocation | S2/P2 | ✅ **Fixed** | `create_token` embeds `tv`; `_user_from_authorization` rejects a token whose `tv` ≠ `user.token_version` (auth.py:88-141). "Sign out everywhere" now possible. |
| **SEC-02** unauth heavy-compute DoS | S2/P2 | ✅ **Fixed** | `HEAVY_LIMIT` (default 12/min/IP) bucket on `/onepager`, `/valuation`, strategy backtest (main.py:150-213). |
| **SEC-03** signup email-bomb | S2/P2 | ✅ **Fixed** | 60 s per-address send cooldown now on the signup path, not just resend (auth_routes.py:103-118). |
| **SEC-04** CORS wildcard default | S3/P3 | ✅ **Fixed** | Fail-closed: defaults to the branded origin (with a warning) when `FRONTEND_ORIGIN` unset in a prod-shaped env; `allow_credentials=False` (main.py:298-311). |
| **SEC-05** `requests` CVE + unpinned deps | S3/P3 | ✅ **Fixed** | `requests==2.32.4`; all 19 backend deps pinned with `==` (cryptography 49.0.0, pdfplumber/httpx/dnspython now pinned). |
| **SEC-06** backup key = bare SHA-256 | S3/P3 | ✅ **Fixed** | scrypt KDF (n=2¹⁴,r=8,p=1) via `MultiFernet`; legacy sha256 kept for decrypt only (backup.py:38-58). |
| **SEC-07** token in `localStorage` | S3/P3 | ⚠️ **Mitigated** | Still `localStorage` (`eq_token`), but now revocable (SEC-01) and **zero** XSS sinks in `src/` (no `dangerouslySetInnerHTML`/`innerHTML`/`eval`) under a tight CSP. Residual accepted. |
| **SEC-08** ledger IP from spoofable XFF | S3/P3 | ✅ **Fixed** | `_record_event` now uses `RateLimitMiddleware._client_ip` (trusted rightmost hop) (auth_routes.py:34-40). |
| **SEC-09** dormant LLM branch | S4/P3 | ✅ **Substantively fixed** | No reachable Anthropic call anywhere (`transcript_nlp` explicit); only cosmetic no-op `with_llm` params linger (transcript_ingester.py:16-17). |
| **SEC-10** raw exception in 500 body | S4/P3 | ✅ **Fixed** | Generic `{"error":"internal error"}`; real detail only under `_debug_enabled()` (main.py:939-941, 1086-1088). |
| **SEC-11** RDS TLS not verify-full | S3/P3 | ⚠️ **Mechanism shipped** | verify-full is opt-in via `DB_SSL_VERIFY=full`+`RDS_CA_BUNDLE`; defaults to encryption-without-verification (documented; low in-VPC risk) (database.py:24-36). Enable after a connection rehearsal. |

## New surface reviewed (added since 2026-07-19) — clean

- **`/api/feedback`** — **auth-gated** (`get_current_user`), 2000-char cap, DB-only, **no email send** → no anonymous spam sink, no email-bomb. ✅
- **`/admin` ops panel** — all **28** admin routes carry `Depends(require_admin)` (fail-closed 403 on unset `ADMIN_EMAILS`); verified none unguarded, including the new `usage`, `kv-health`, `feedback` (read-all), `macro/upload`, `fm-engine/rebuild`. ✅
- **`POST /api/admin/macro/upload`** — admin-gated + 10 MB cap; xlsx parsed by openpyxl, admin-only trigger. ✅
- **MoSPI-MCP / DBnomics / activity fetchers** — all fetch **fixed** hosts (`mcp.mospi.gov.in`, `api.db.nomics.world`, env-configured gov URLs); **no request-body-supplied URL** → no new SSRF. ✅
- **Logo fetch** — fixed CDN hosts keyed by a numeric `ticker_id` from the DB, not user input → not SSRF. ✅
- **Injection / RCE** — no `os.system`/`subprocess`/`eval`/`exec` on request data; all ORM SQL parameterised (the one f-string `text()` in backup.py uses ORM-metadata table names, not user input). ✅
- **Secrets** — no `.env`/`.pem`/`.key` tracked; no hardcoded AWS keys/private keys. ✅
- **Edge** — live CSP tight (`script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `connect-src 'self' https://api.equityverdict.com`), HSTS + `nosniff` + referrer-policy present. ✅

## New findings

### SEC-12 — Unauthenticated telemetry write; retention pruned only on admin visit
- **Severity:** S3 **Likelihood:** Low-Med **Effort:** XS · **Status: FIXED in this change**
- **Location:** telemetry_routes.py:41-57 (anonymous `POST /api/telemetry`) + :80-94 (`prune_usage_events`), previously called only from admin_routes.py:585.
- **Evidence:** `/api/telemetry` accepts anonymous events and writes a `UsageEvent` row per call, guarded only by the general 240/min/IP bucket (not the heavy bucket). The 180-day retention prune ran **only** when an admin opened the usage page — so on a box whose admin rarely visits, rows accumulate unbounded (rotating IPs amplify). Small rows + the general cap make this slow, but it is an unauthenticated DB-write with no enforced ceiling.
- **Fix (shipped):** a standing daily scheduler job (`run_usage_prune`, 03:15 UTC) makes retention self-enforcing regardless of admin activity. Optionally (not done): a tighter per-IP cap on anonymous `/api/telemetry`.

### SEC-13 — No `pip-audit`/`npm audit` gate in CI (hygiene)
- **Severity:** S4 **Effort:** XS · **Status: Fixed**
- **Evidence:** the 2026-07-19 audit recommended a dependency-CVE gate; CI had none.
- **Fix (shipped):** backend CI runs `pip-audit -r requirements.txt` (non-blocking report — a new transitive advisory must not hard-block an unrelated hotfix; pinned deps make the fix a deliberate edit). Frontend CI (equity-terminal repo) blocks the merge on a high in **production** deps (`npm audit --omit=dev --audit-level=high`) plus a non-blocking full-tree report; the two dev/build-tool highs present at audit time (`brace-expansion`, `vite`) were cleared (`vite` 8.0.14→8.1.5; build + bundle budget + parity re-verified).

### Minor hygiene (no action required)
- CSP `style-src 'unsafe-inline'` — common for React inline styles; low risk given `script-src 'self'`.
- SEC-09 leftover no-op `with_llm` params — cosmetic dead code; delete on next touch of `transcript_ingester.py`.
- `backup.py:134` f-string `text()` uses trusted ORM-metadata identifiers (not user input) — safe, but a smell.

## Bottom line

No takeover / IDOR / injection / SSRF / secret-exposure / auth-bypass finding survives. The three P2 abuse items that gated "safe to charge" are **closed**. From a breach/exposure standpoint the platform is safe to expose publicly and — on the security axis — safe to begin charging once SEC-12 (this change) deploys. SEC-13 and the minor notes are defense-in-depth. (Legal/licensing gating remains a separate, non-security track.)
