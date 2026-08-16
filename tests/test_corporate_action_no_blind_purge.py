"""An absent vendor payload must never wipe a company's corporate-action history.

`_save_corporate_actions` purged then re-inserted. When the vendor omitted
`stockCorporateActionData` — or shipped it in a shape we could not parse — the
DELETE had already run and there was nothing to put back, so the company's whole
split/bonus history vanished.

That loss is not merely an empty table. `app/corporate_actions.price_factor`
reads those rows to adjust historical prices for splits and bonuses, so a wiped
ledger makes prices silently WRONG rather than visibly missing.

This is the DATA-12 shape exactly: on 24 Jul 2026 a vendor endpoint began
returning 404 and a blind replace wiped ~378 of ~736 names before anyone noticed.
That incident was fixed by merging instead of replacing; this call site kept the
old pattern.

Run: ./venv313/bin/python -m pytest tests/test_corporate_action_no_blind_purge.py -q
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.ingest.indianapi_ingester import _save_corporate_actions


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def company(session):
    co = models.Company(ticker="TESTCO", name="Test Co", type="nonfinancial",
                        sector="Software & Programming", shares_outstanding=100.0)
    session.add(co)
    session.commit()
    # two stored events, as a prior healthy ingest would have written
    session.add_all([
        models.CorporateAction(company_id=co.id, source="indianapi",
                               action_type="split", ex_date="2024-05-10", value=2.0),
        models.CorporateAction(company_id=co.id, source="indianapi",
                               action_type="bonus", ex_date="2023-08-01", value=1.5),
    ])
    session.commit()
    return co


def _count(session, co):
    return session.query(models.CorporateAction).filter_by(company_id=co.id).count()


def test_vendor_omits_the_block_entirely(session, company):
    """The 404 / revoked-endpoint case — the one that caused DATA-12."""
    assert _count(session, company) == 2
    _save_corporate_actions(session, company, {})          # no corporate-action key
    assert _count(session, company) == 2, "an absent payload wiped stored history"


def test_vendor_sends_none(session, company):
    _save_corporate_actions(session, company, None)
    assert _count(session, company) == 2


def test_block_present_but_unparseable(session, company):
    """Shape drift: the key exists but yields no usable rows. Still not a
    licence to delete — we learned nothing."""
    _save_corporate_actions(session, company, {"stockCorporateActionData": {"bonus": []}})
    assert _count(session, company) == 2


def test_a_real_payload_still_replaces(session, company):
    """The guard must not freeze the ledger: a genuine payload still refreshes
    it, otherwise corrections could never land."""
    # The vendor keys the ex-date per action type: dividend `xdDate`, split
    # `xsDate`, bonus `xbDate` (see _corporate_actions and the CorporateAction
    # model docstring). This fixture used to say `exDate`, which the parser has
    # never read, so `n` was always 0 and the skip below fired on every run —
    # the ONLY test of the write path never actually wrote anything.
    stock = {
        "stockCorporateActionData": {
            "splits": [{"xsDate": "2025-01-15", "oldFaceValue": "10", "newFaceValue": "1"}],
        }
    }
    n = _save_corporate_actions(session, company, stock)
    # A hard assertion, not a skip: if the parser stops recognising the real
    # split shape, that is a regression to fail on, not a reason to go silent.
    assert n == 1, "parser must recognise the vendor's real split shape (xsDate/oldFaceValue/newFaceValue)"
    rows = session.query(models.CorporateAction).filter_by(company_id=company.id).all()
    assert len(rows) == n
    assert all(r.ex_date == "2025-01-15" for r in rows), "old rows should have been replaced"
