"""
app/validation.py — lightweight data-integrity checks on a company's stored
statements. This is the near-term version of the "accuracy spine": rather than
add a second data source, we cross-check what IndianAPI gave us and surface
mismatches so the UI can show "numbers don't tie" instead of quietly printing a
wrong figure.

Each check returns {"check", "ok", "severity", "detail"}. `ok=None` means the
check couldn't run (missing inputs) — not a failure.
"""
from __future__ import annotations


def _latest(statements: dict):
    yrs = sorted((statements or {}).keys())
    return yrs[-1] if yrs else None


def _rel_err(a, b):
    if not b:
        return None
    return abs(a - b) / abs(b)


def validate_statements(statements: dict, is_financial: bool = False) -> list[dict]:
    """Run tie-out checks on the latest fiscal year. Tolerant by design —
    Indian filings round and reclassify, so we flag only material gaps."""
    out = []
    yr = _latest(statements)
    if yr is None:
        return out
    pl = (statements[yr].get("PL") or {})
    bs = (statements[yr].get("BS") or {})
    cf = (statements[yr].get("CF") or {})

    # 1) Balance sheet balances: total assets ≈ total liabilities + net worth.
    ta = bs.get("total_assets")
    tl = bs.get("total_liabilities")
    nw = bs.get("net_worth")
    if ta is not None and tl is not None and nw is not None:
        err = _rel_err(ta, tl + nw)
        ok = err is not None and err <= 0.02
        out.append({"check": "Balance sheet balances", "ok": ok, "severity": "high",
                    "detail": f"Assets ₹{ta:,.0f}cr vs Liab+Equity ₹{tl+nw:,.0f}cr"
                              + (f" ({err*100:.1f}% gap)" if err else "")})

    # 2) PAT consistency between P&L and cash-flow opening line.
    pat_pl = pl.get("pat")
    pat_cf = cf.get("pat")
    if pat_pl is not None and pat_cf is not None:
        err = _rel_err(pat_pl, pat_cf)
        out.append({"check": "PAT reconciles P&L → CF", "ok": err is not None and err <= 0.02,
                    "severity": "medium",
                    "detail": f"P&L PAT ₹{pat_pl:,.0f}cr vs CF PAT ₹{pat_cf:,.0f}cr"})

    # 3) PBT - Tax = PAT (within rounding).
    pbt, tax, pat = pl.get("pbt"), pl.get("tax"), pl.get("pat")
    if pbt is not None and tax is not None and pat is not None:
        err = _rel_err(pbt - tax, pat)
        out.append({"check": "PBT − Tax = PAT", "ok": err is not None and err <= 0.03,
                    "severity": "low",
                    "detail": f"PBT−Tax ₹{pbt-tax:,.0f}cr vs PAT ₹{pat:,.0f}cr"})

    # 4) Non-financials: EBIT should not exceed revenue.
    if not is_financial:
        rev, ebit = pl.get("revenue"), pl.get("ebit")
        if rev is not None and ebit is not None and rev > 0:
            out.append({"check": "EBIT ≤ Revenue", "ok": ebit <= rev * 1.01,
                        "severity": "high",
                        "detail": f"EBIT ₹{ebit:,.0f}cr vs Revenue ₹{rev:,.0f}cr"})

    return out


def validation_summary(checks: list[dict]) -> dict:
    ran = [c for c in checks if c["ok"] is not None]
    failed = [c for c in ran if not c["ok"]]
    return {
        "checks": checks,
        "n_run": len(ran),
        "n_failed": len(failed),
        "ties_out": len(failed) == 0,
        "has_high_severity_failure": any(c["severity"] == "high" for c in failed),
    }
