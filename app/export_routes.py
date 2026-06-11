"""
app/export_routes.py — Excel workbook exports (openpyxl).

  GET /api/export/screener.xlsx  → one "Screener" sheet, one row per company
                                   with a precomputed Valuation + MarketSnapshot
                                   (the same data /api/companies reads).
  GET /api/export/{ticker}.xlsx  → full company workbook: Summary, P&L,
                                   Balance Sheet, Cash Flow (years as columns)
                                   and the per-year DCF/RI Valuation schedule.

Numbers are written as numbers (never strings) so Excel can sort/format them.
"""
import io

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.database import get_db
from app import models, engines
from app.assemble import build_company, effective_assumptions, _shape_statements
from app.consensus import analyst_consensus

router = APIRouter(prefix="/api/export", tags=["export"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BOLD = Font(bold=True)


def _num(x, nd=4):
    """Coerce to a rounded float for a cell, or None (empty cell)."""
    if x is None:
        return None
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _style_header(ws, widths=None):
    """Bold the header row, freeze it, set column widths."""
    for cell in ws[1]:
        cell.font = _BOLD
    ws.freeze_panes = "A2"
    if widths:
        for idx, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w


def _xlsx_response(wb: Workbook, filename: str) -> Response:
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    return Response(
        content=data,
        media_type=XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Content-Length": str(len(data))},
    )


# ── Screener export ──────────────────────────────────────────────────────────

_SCREENER_HEADER = ["Ticker", "Name", "Sector", "Valuation Sector", "Price",
                    "Intrinsic", "MoS %", "Verdict", "Composite", "P/E", "P/B",
                    "ROE %", "Analyst Target", "Analyst Rating"]


@router.get("/screener.xlsx")
def export_screener(db: Session = Depends(get_db)):
    wb = Workbook()
    ws = wb.active
    ws.title = "Screener"
    ws.append(_SCREENER_HEADER)
    _style_header(ws, widths=[14, 30, 22, 18, 12, 12, 10, 12, 11, 9, 9, 9, 14, 14])

    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()

    for co in db.query(models.Company).order_by(models.Company.ticker).all():
        v = val_by.get(co.id)
        price = price_by.get(co.id)
        if v is None or price is None:
            continue
        ws.append([
            co.ticker, co.name, co.sector, v.valuation_sector,
            _num(price, 2), _num(v.intrinsic, 2),
            _num(v.mos * 100 if v.mos is not None else None, 1),
            v.verdict, _num(v.composite, 1),
            _num(v.pe, 2), _num(v.pb, 2),
            _num(v.roe * 100 if v.roe is not None else None, 1),
            _num(v.analyst_target, 2), v.analyst_rating,
        ])
    return _xlsx_response(wb, "screener.xlsx")


# ── Company export ───────────────────────────────────────────────────────────

_STMT_SHEETS = [("PL", "P&L"), ("BS", "Balance Sheet"), ("CF", "Cash Flow")]


def _summary_sheet(ws, co, price, rec, analyst, assumptions):
    ws.append(["Field", "Value"])
    _style_header(ws, widths=[30, 22])
    rows = [
        ("Ticker", co.ticker), ("Name", co.name), ("Sector", co.sector),
        ("Price", _num(price, 2)),
        ("Intrinsic", _num((rec or {}).get("intrinsic"), 2)),
        ("MoS %", _num((rec or {}).get("mos", None) * 100
                       if (rec or {}).get("mos") is not None else None, 1)),
        ("Verdict", (rec or {}).get("verdict")),
        ("Composite", _num((rec or {}).get("composite"), 1)),
        ("Primary Method", (rec or {}).get("primary_method")),
        ("Valuation Sector", (rec or {}).get("valuation_sector")),
    ]
    a = analyst or {}
    rows += [
        ("Analyst Target", _num(a.get("target"), 2)),
        ("Analyst Low", _num(a.get("low"), 2)),
        ("Analyst High", _num(a.get("high"), 2)),
        ("Analyst Rating", a.get("rating")),
        ("Analyst Upside %", _num(a.get("upside", None) * 100
                                  if a.get("upside") is not None else None, 1)),
        ("# Analysts", _num(a.get("n"), 0)),
    ]
    for k, v in rows:
        ws.append([k, v])
    # Derived (independent) assumptions block.
    ws.append([])
    ws.append(["Derived Assumptions", ""])
    ws.cell(row=ws.max_row, column=1).font = _BOLD
    for k in sorted(assumptions or {}):
        if k.startswith("_"):
            continue
        v = assumptions[k]
        ws.append([k, _num(v) if isinstance(v, (int, float)) else str(v)])


def _statement_sheet(ws, statements, stmt_type):
    years = sorted(statements.keys())
    ws.append(["Line Item"] + [f"FY{y}" for y in years])
    _style_header(ws, widths=[26] + [13] * len(years))
    items = sorted({item
                    for y in years
                    for item in (statements[y].get(stmt_type) or {})})
    for item in items:
        ws.append([item] + [_num((statements[y].get(stmt_type) or {}).get(item), 2)
                            for y in years])


def _valuation_sheet(ws, rec):
    v = (rec or {}).get("valuation") or {}
    rows = v.get("rows") or []
    cols = list(rows[0].keys()) if rows else ["t"]
    ws.append([c.upper() if c == "t" else c for c in cols])
    _style_header(ws, widths=[8] + [14] * (len(cols) - 1))
    for r in rows:
        ws.append([_num(r.get(c), 6) for c in cols])
    ws.append([])
    for label, val in [("Method", v.get("method")),
                       ("Ke", _num(v.get("ke"), 6)),
                       ("WACC", _num(v.get("wacc"), 6)),
                       ("BVPS (t0)", _num(v.get("bvps0"), 2)),
                       ("PV Explicit", _num(v.get("pv_explicit"), 2)),
                       ("TV (PV)", _num(v.get("tv_pv"), 2)),
                       ("EV", _num(v.get("ev"), 2)),
                       ("Intrinsic (model)", _num(v.get("intrinsic"), 2))]:
        ws.append([label, val])
        ws.cell(row=ws.max_row, column=1).font = _BOLD


@router.get("/{ticker}.xlsx")
def export_company(ticker: str, db: Session = Depends(get_db)):
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    price = co.market.price if co.market else None
    rec, assumptions, analyst = None, {}, None
    try:
        data = build_company(db, co)
        assumptions = effective_assumptions(db, co, data)
        rec = engines.recommend(data, assumptions)
    except Exception:
        rec, assumptions = None, assumptions or {}
    try:
        ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
        analyst = analyst_consensus(ins.data if ins else None, price)
    except Exception:
        analyst = None

    hist_rows = (db.query(models.HistoricalFinancial)
                   .filter_by(company_id=co.id).all())
    statements = _shape_statements(hist_rows)

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    _summary_sheet(summary, co, price, rec, analyst, assumptions)
    for stmt_type, title in _STMT_SHEETS:
        _statement_sheet(wb.create_sheet(title), statements, stmt_type)
    _valuation_sheet(wb.create_sheet("Valuation"), rec)

    return _xlsx_response(wb, f"{co.ticker}.xlsx")
