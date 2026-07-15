"""
app/sentiment.py — a transparent, explainable sentiment score (0–100) built ONLY
from signals we already compute, never a black box:

  • Concall tone      — management's confident-vs-cautious language (tone_score,
                        [-1,+1], from the transcript ingester). The heaviest leg.
  • Estimate revision — analysts up/downgrading (the catalyst signal: change in
                        consensus-implied upside between snapshots).
  • Beat/miss streak  — the results track (signed run of EPS beats/misses).

Each leg's contribution is returned alongside the score, so the number is always
auditable. Neutral (50) when a leg is missing — we never fabricate sentiment.
"""
from __future__ import annotations


def _score(tone, catalyst, streak) -> dict:
    s = 50.0
    parts = []
    if tone is not None:
        c = round(max(-25, min(25, tone * 25)), 1)          # tone [-1,1] → ±25
        s += c
        parts.append({"signal": "Concall tone", "detail": f"{tone:+.2f} (confident−cautious)", "contribution": c})
    if catalyst is not None:
        c = round(max(-15, min(15, catalyst * 300)), 1)     # revision in implied upside → ±15
        s += c
        parts.append({"signal": "Estimate revision", "detail": f"{catalyst*100:+.1f}% implied upside", "contribution": c})
    if streak:
        c = round(max(-10, min(10, streak * 3.5)), 1)       # signed beat/miss run → ±10
        s += c
        parts.append({"signal": "Beat/miss streak", "detail": (f"{abs(streak)}Q {'beat' if streak > 0 else 'miss'}"), "contribution": c})
    s = max(0.0, min(100.0, s))
    label = "positive" if s >= 62 else "negative" if s <= 38 else "neutral"
    return {"score": round(s), "label": label, "parts": parts}


def _catalyst_one(db, tk: str):
    from app import models
    h = (db.query(models.ConsensusSnapshot).filter_by(ticker=tk)
           .order_by(models.ConsensusSnapshot.date).all())
    if len(h) < 2:
        return None
    latest, prior = h[-1], h[0]
    if latest.upside is not None and prior.upside is not None:
        return latest.upside - prior.upside
    if latest.target and prior.target:
        return latest.target / prior.target - 1.0
    return None


def company_sentiment(db, ticker: str) -> dict | None:
    """Sentiment for one name, or None when no signal exists. Lightweight —
    queries only this ticker's rows."""
    from app import models
    from app.results_logic import eps_surprise_history
    tk = (ticker or "").upper()
    ti = db.query(models.TranscriptInsight).filter_by(ticker=tk).first()
    tone = ti.tone_score if ti else None
    catalyst = _catalyst_one(db, tk)
    streak = None
    co = db.query(models.Company).filter_by(ticker=tk).first()
    if co:
        ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
        h = eps_surprise_history((ins.data or {}).get("forecasts")) if (ins and ins.data) else None
        streak = (h or {}).get("streak")
    r = _score(tone, catalyst, streak)
    if not r["parts"]:
        return None
    r["as_of"] = ti.quarter if ti else None
    return r


def sentiment_by(db) -> dict:
    """{ticker: {score, label, parts}} across the universe — for the screener.
    Batch-loads tone + revisions + beat/miss so it's one pass, not N queries."""
    from app import models
    from app.signals import catalyst_by
    from app.results_logic import eps_surprise_history
    tone = {r.ticker.upper(): r.tone_score for r in db.query(models.TranscriptInsight).all()
            if r.tone_score is not None}
    cat = catalyst_by(db)
    tk_of = {c.id: (c.ticker or "").upper() for c in db.query(models.Company).all()}
    streak = {}
    for r in db.query(models.CompanyInsight).all():
        h = eps_surprise_history((r.data or {}).get("forecasts")) if r.data else None
        if h and h.get("streak"):
            streak[tk_of.get(r.company_id)] = h["streak"]
    out = {}
    for tk in set(tone) | set(cat) | set(streak):
        if not tk:
            continue
        r = _score(tone.get(tk), cat.get(tk), streak.get(tk))
        if r["parts"]:
            out[tk] = r
    return out
