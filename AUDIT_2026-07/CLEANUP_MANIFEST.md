# Appendix — Cleanup & Redundancy Manifest (Agent 7, Lead-owned)

Cross-cutting. Every item below is confirmed unreferenced or safe with the stated pre-check.
Endpoint-orphan and stale-doc items from the Code lane (Agent 4) will be merged in at consolidation.

## Safe to delete now (high confidence, zero/low blast radius)

### [CLN-01] Frontend `backend/` folder — stale duplicate of the real backend (OWNER PRIORITY)
- **Severity:** S2 (confusing dead code)  **Effort:** XS  **Priority:** P2  **Status:** Still open
- **Location:** `/Users/manan_jagtap/equity-terminal/backend/main.py` + `thesis.py` (~27KB, last touched Jun 4)
- **Evidence:** `grep` for `./backend` imports in src/vite/index.html/package.json → NONE (all "backend" hits are the word in comments about the API). It **is tracked in git** (`git ls-files backend/` returns both files).
- **Pre-check:** confirm no Vercel build step references it (it doesn't — Vite builds `src/`). **Blast radius: zero.**
- **Fix:** `git rm -r backend/` in the frontend repo + commit. Re-run `npm run build` to confirm unchanged.

### [CLN-02] No `.dockerignore` — dev clutter ships in the production image
- **Severity:** S2 (surface + bloat)  **Effort:** XS  **Priority:** P2  **Status:** New
- **Location:** backend repo root (missing `.dockerignore`); `deploy/aws/Dockerfile:23 COPY app ./app`
- **Evidence:** No `.dockerignore` exists, so `COPY app ./app` copies **~24 `.bak` files + 10 `probe_*.py` scripts + `app/onepager/legacy.py`** into the running container. (Root `*.db` files are NOT copied — Dockerfile only takes `app/`, `alembic/`, `scheduler.py`, `requirements.txt`.) **Correction 16 Aug 2026 (see CLN-06): `app/onepager/legacy.py` is NOT clutter — it is the live ReportLab one-pager renderer re-exported by `app/onepager/__init__.py`. Keep it OUT of `.dockerignore`; excluding it boot-fails the image on `main.py`'s module-level import while every local run still passes.**
- **Fix:** Add `.dockerignore` excluding `**/*.bak*`, `**/probe_*.py`, `*.db`, `__pycache__/`, `tests/`, `*.md`, `venv*/`. Rebuild → smaller image, narrower surface.
- **Verification:** `docker run … ls app/onepager/*.bak` returns nothing.

### [CLN-03] ~24 `.bak` working-tree files
- **Severity:** S3  **Effort:** XS  **Priority:** P3  **Status:** Still open (June §3)
- **Location:** backend: `scheduler.py.bak.*`, `app/engines.py.bak.*` (x2), `app/models.py.bak.*`, `app/main.py.bak.*` (x2), `app/ingest/*.bak.*`, `app/onepager/render*.py.bak.*` (x14, all 31 May)
- **Evidence:** all match `.gitignore` (`*.bak`, `*.bak.*`, `app/**/*.bak.*`) → **not tracked**, local clutter only, but shipped in the image via CLN-02.
- **Fix:** `find . -name '*.bak*' -delete` (they're gitignored; nothing tracked is lost). Superseded by git history.

### [CLN-04] 10 dead `probe_*.py` dev scripts
- **Severity:** S3  **Effort:** XS  **Priority:** P3  **Status:** Still open (June §3)
- **Location:** `app/ingest/probe_{market,quarterly,annual,profile,statements,dev,new,news,forecast,targets}.py`
- **Evidence:** none imported by live code — scheduler imports `app.ingest.endpoint_probe` (a *different*, live file — KEEP that one). The 10 `probe_*.py` are one-off vendor-shape探 scripts.
- **Pre-check:** confirm `endpoint_probe.py` is the only probe referenced (verified). **Blast radius: none.**
- **Fix:** delete the 10 `probe_*.py`. (Note: `probe_news.py` is among them — verify it's the dead one, not endpoint_probe.)

### [CLN-05] Local dev DB files at repo root
- **Severity:** S3  **Effort:** XS  **Priority:** P3  **Status:** New
- **Location:** `terminal.db`, `equity_terminal.db`, `_audit_test.db`
- **Evidence:** gitignored (`*.db`, explicit `terminal.db`, `equity_terminal.db`) and NOT copied to the image. Pure local clutter.
- **Fix:** delete locally; harmless if left.

## Needs a check before deleting (confirm with owner / Code lane)

### [CLN-06] `app/onepager/legacy.py` — likely superseded renderer
- **Severity:** S3  **Effort:** S  **Priority:** P3  **Status:** WITHDRAWN 16 Aug 2026 — **DO NOT DELETE.** Pre-check run; it inverts this entry.
- **Location:** `app/onepager/legacy.py`
- **Original evidence (WRONG — kept for the record):** `app/onepager/` is LIVE (`main.py:112 from app.onepager import build_onepager`); the PDF was rebuilt (render.py/render_css.py current). `legacy.py` name implies the old renderer. Not yet confirmed unreferenced.
- **Pre-check result (16 Aug 2026):** the grep pattern missed a re-export. `app/onepager/__init__.py` is `from .legacy import build_onepager`, so `main.py`'s live import IS the reference the grep was hunting for — `legacy.py` is the **only** renderer `POST /api/companies/{ticker}/onepager` has ever used, and deleting it 500s that route for every ticker. The "rebuilt" renderer is the dead one: `render.py`/`render_css.py`/`peers.py` lost their only entry point when the AI-free strip (5e9ea2e, 16 Jul 2026) deleted `app/onepager/extract.py`, and they need jinja2 + weasyprint, neither ever in `requirements.txt`.
- **Real action (needs owner sign-off, not a cleanup):** the deletion candidate is the weasyprint stage — `render.py`, `render_css.py`, `peers.py`, `brand.py` (zero importers), `fonts/` (212 KB), `scripts/generate_onepager.py`. Nothing outside that set references them. **Blast radius of removing THAT set: zero** (the script is already dead at import and is not even copied into the image).

### [CLN-07] `yfinance` — one live path only; evaluate removal
- **Severity:** S3  **Effort:** M  **Priority:** P3  **Status:** New — CONFIRM (cross-lane: Data/Perf)
- **Location:** `requirements.txt` (yfinance==0.2.51); live use `app/ingest/indianapi_ingester.py:~1295` (intraday spot via `yf.download`, flagged flaky "Yahoo blocks"); everywhere else is dead docstrings.
- **Pre-check:** confirm the intraday path is still reached given Dhan LTP is primary (Perf lane). If Dhan covers intraday, remove the yfinance path + dependency (also removes the CMP-02 Yahoo-data-licence exposure for that path). **Blast radius: intraday spot refresh** — verify Dhan LTP fills the gap.

### [CLN-08] Stale planning docs contradict current reality (Railway/yfinance/AI era)
- **Severity:** S2 (misleads a lawyer/new engineer)  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** backend `COMPLIANCE.md` (AI-thesis-as-live — see CMP-06), `AUDIT_2026-06.md`, `ROADMAP_2026-06.md`, `HANDOFF.md` (Railway system map), `ARCHITECTURE.md` (verify Mumbai-current), `STRATEGY_AND_ROADMAP_2026-07.md`
- **Evidence:** HANDOFF.md "LIVE SYSTEM MAP" still lists Railway URLs + Railway Postgres as live; COMPLIANCE.md §5 treats retired AI features as live. Reality is AWS Mumbai + AI-free.
- **Fix:** DON'T delete — these are historical record. Add a dated "SUPERSEDED — see ARCHITECTURE.md / AUDIT_2026-07" banner to the top of each pre-migration doc, and refresh HANDOFF.md's live-system-map + COMPLIANCE.md to current reality.
- **Verification:** HANDOFF.md system map shows AWS Mumbai; no doc presents Railway/AI-thesis as live without a superseded banner.

## Dependency cleanups (from Security lane, folded here)
- **CLN-09 (=SEC-05):** pin all unpinned backend deps (pdfplumber, httpx, dnspython, cryptography, bse); bump requests>=2.32.4. Effort XS.

## To be merged from Agent 4 (Code lane) at consolidation
- Dead/unreachable endpoints (of 123, those never called by the frontend).
- Unused exports, orphaned scripts under `scripts/`, config drift between repos.
