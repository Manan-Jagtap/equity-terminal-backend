"""
app/sentiment.py — a transparent, explainable sentiment score (0–100) built ONLY
from signals we already compute, never a black box:

  • Concall tone      — management's confident-vs-cautious language (tone_score,
                        [-1,+1], from the transcript ingester). The heaviest leg.
  • Estimate revision — analysts up/downgrading (the catalyst signal: change in
                        consensus-implied upside between snapshots).
  • Beat/miss streak  — the results track (signed run of EPS beats/misses).
  • News flow         — a lexicon read of recent headlines (bullish vs bearish
                        words). Populated cheaply from the news the News tab
                        already fetches — no extra API calls, no AI.

Each leg's contribution is returned alongside the score, so the number is always
auditable. Neutral (50) when a leg is missing — we never fabricate sentiment.
"""
from __future__ import annotations
import re
import datetime as _dt

NEWS_SENT_KEY = "news_sentiment_v1"

# Headline lexicon — matched at word boundaries so short words don't collide
# ("ban" in "urban", "fine" in "finance"). Multi-word phrases match as substrings.
_BULL = ("surge", "surges", "surged", "jump", "jumps", "jumped", "rally", "rallies",
         "gain", "gains", "gained", "rise", "rises", "rose", "soar", "soars", "soared",
         "climb", "climbs", "all-time high", "upgrade", "upgraded",
         "beat", "beats", "strong", "robust", "wins", "win", "bags", "bagged",
         "order win", "new order", "expansion", "expands", "acquire", "acquires",
         "acquisition", "launch", "launches", "approval", "approved", "outperform",
         "buyback", "bonus", "multibagger", "target raised", "profit rises",
         "profit jumps", "profit surges", "stake buy", "raises stake", "wins order")
_BEAR = ("fall", "falls", "fell", "drop", "drops", "dropped", "plunge", "plunges",
         "plunged", "slump", "slumps", "decline", "declines", "tumble", "tumbles",
         "miss", "misses", "loss", "losses", "downgrade", "downgraded",
         "probe", "fraud", "scam", "penalty", "fined", "ban", "banned",
         "recall", "default", "defaults", "resign", "resigns", "resignation",
         "layoff", "layoffs", "weak", "lawsuit", "raid", "raids", "warns", "warning",
         "stake sale", "pledge", "insolvency", "nclt", "sebi bars", "tax demand",
         "gst notice", "profit falls", "profit drops", "target cut", "downgraded to sell")


_BULL_PHRASES = tuple(w for w in _BULL if " " in w or "-" in w)
_BEAR_PHRASES = tuple(w for w in _BEAR if " " in w or "-" in w)
_BULL_SINGLES = frozenset(w for w in _BULL if " " not in w and "-" not in w)
_BEAR_SINGLES = frozenset(w for w in _BEAR if " " not in w and "-" not in w)
# ENG-13: a small negation set. A sentiment word preceded by one of these within
# a 3-token window is not counted — "no fraud alleged" / "denies wrongdoing" must
# not read bearish.
_NEG = frozenset({"no", "not", "never", "without", "denies", "denied", "deny",
                  "dismiss", "dismisses", "dismissed", "rejects", "rejected",
                  "false", "unfounded", "cleared", "clears"})
_TOKEN_RE = re.compile(r"[a-z'\-]+")


def _hit_count(toks, singles):
    """Count single-word matches NOT negated by a preceding word (3-token window)."""
    n = 0
    for i, tk in enumerate(toks):
        if tk in singles and not any(w in _NEG for w in toks[max(0, i - 3):i]):
            n += 1
    return n


def news_headline_score(texts) -> tuple[float | None, int]:
    """Lexicon sentiment over headlines → (score in [-1,+1], n headlines with a
    hit). None when nothing scores, so the leg is simply absent (never faked).
    Single-word hits are negation-aware (ENG-13); multi-word phrases count as
    substrings (rarely negated)."""
    pos = neg = hits = 0
    for t in texts or []:
        low = (t or "").lower()
        if not low:
            continue
        toks = _TOKEN_RE.findall(low)
        p = _hit_count(toks, _BULL_SINGLES) + sum(low.count(ph) for ph in _BULL_PHRASES)
        n = _hit_count(toks, _BEAR_SINGLES) + sum(low.count(ph) for ph in _BEAR_PHRASES)
        pos += p
        neg += n
        if p or n:
            hits += 1
    if pos + neg == 0:
        return None, 0
    return round((pos - neg) / (pos + neg), 3), hits


def record_news_sentiment(db, ticker: str, items: list) -> None:
    """Score the headlines the News tab just fetched and cache them in KV, so the
    sentiment score gets a news leg with ZERO extra API calls. Fail-silent."""
    try:
        from app.manager_engine import _kv_get, _kv_put
        texts = [(i.get("title") or "") + " " + (i.get("summary") or "") for i in (items or [])]
        score, n = news_headline_score(texts)
        if score is None:
            return
        store = dict(_kv_get(db, NEWS_SENT_KEY) or {})
        store[(ticker or "").upper()] = {"score": score, "n": n,
                                         "as_of": _dt.date.today().isoformat()}
        _kv_put(db, NEWS_SENT_KEY, store)
    except Exception:
        pass


def _news_one(db, tk: str):
    try:
        from app.manager_engine import _kv_get
        rec = (_kv_get(db, NEWS_SENT_KEY) or {}).get(tk)
        if not rec:
            return None
        # ENG-13: expire a stale cached news score (> 30 days). It is written only
        # when the News tab is opened, so a 6-month-old bearish headline would
        # otherwise keep dragging today's sentiment indefinitely.
        as_of = rec.get("as_of")
        if as_of:
            try:
                if (_dt.date.today() - _dt.date.fromisoformat(as_of)).days > 30:
                    return None
            except Exception:
                pass
        return rec.get("score")
    except Exception:
        return None


def _score(tone, catalyst, streak, news=None) -> dict:
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
    if news is not None:
        c = round(max(-12, min(12, news * 12)), 1)          # headline bull−bear → ±12
        s += c
        parts.append({"signal": "News flow", "detail": f"{news:+.2f} (bullish−bearish headlines)", "contribution": c})
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
    news = _news_one(db, tk)
    r = _score(tone, catalyst, streak, news)
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
    news = {}
    try:
        from app.manager_engine import _kv_get
        news = {k: (v or {}).get("score") for k, v in (_kv_get(db, NEWS_SENT_KEY) or {}).items()
                if (v or {}).get("score") is not None}
    except Exception:
        news = {}
    out = {}
    for tk in set(tone) | set(cat) | set(streak) | set(news):
        if not tk:
            continue
        r = _score(tone.get(tk), cat.get(tk), streak.get(tk), news.get(tk))
        if r["parts"]:
            out[tk] = r
    return out
