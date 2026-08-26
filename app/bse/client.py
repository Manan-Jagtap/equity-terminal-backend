"""
app/bse/client.py — Cloudflare-fronted BSE API client.

BSE serves an undocumented JSON API at api.bseindia.com that powers their
"Corporate Announcements" page. The endpoints are stable but not guaranteed.

Key endpoints (verified empirically — may need adjustment):

  Announcements (paginated):
    GET https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w
        ?strCat=-1                  # category filter; -1 = all
        &strPrevDate=YYYYMMDD       # from date
        &strToDate=YYYYMMDD         # to date
        &strScrip=NUMERIC_CODE      # scrip code
        &strSearch=P                # P=Pending, A=Archive; we use 'P'
        &strType=C                  # C = Company

  Attachment download:
    GET https://www.bseindia.com/xml-data/corpfiling/AttachLive/{filename}

Required headers (BSE blocks bots without these):
    Origin: https://www.bseindia.com
    Referer: https://www.bseindia.com/
    User-Agent: <real browser UA>
    Accept: application/json, text/plain, */*

Response shape (announcement, fields we care about):
    {
      "NEWSID": "...",
      "HEADLINE": "...",
      "ATTACHMENTNAME": "abc.pdf",
      "CATEGORYNAME": "Result",
      "SUBCATNAME": "Investor Presentation",
      "DT_TM": "2026-05-15 18:30:00",
      "NEWSSUB": "...",
      "NSURL": "..."
    }
"""
from __future__ import annotations
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_ATTACHMENT_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive"

# These headers must match what a real browser sends. BSE blocks requests
# without them. UA is a recent Chrome on macOS.
_DEFAULT_HEADERS = {
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_REQUEST_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 60


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_DEFAULT_HEADERS)
    return s


def fetch_announcements(
    scrip_code: str,
    from_date: date,
    to_date: Optional[date] = None,
    *,
    category: str = "-1",
    max_retries: int = 3,
) -> list[dict]:
    """
    Fetch all corporate announcements for a scrip code between two dates.

    Args:
        scrip_code: numeric BSE scrip code, e.g. "533398"
        from_date: start date (inclusive)
        to_date: end date (inclusive). Defaults to today.
        category: BSE category filter. "-1" = all. Other common codes:
                  "20" = Company Update, "21" = Result, "23" = Investor Presentation
        max_retries: retries on transient HTTP errors

    Returns:
        List of announcement dicts (raw from BSE).
    """
    if to_date is None:
        to_date = date.today()

    params = {
        "strCat": category,
        "strPrevDate": from_date.strftime("%Y%m%d"),
        "strToDate": to_date.strftime("%Y%m%d"),
        "strScrip": scrip_code,
        "strSearch": "P",
        "strType": "C",
    }

    url = f"{BSE_API_BASE}/AnnGetData/w"
    sess = _session()

    for attempt in range(1, max_retries + 1):
        try:
            log.info("bse fetch_announcements scrip=%s %s→%s (attempt %d)",
                     scrip_code, from_date, to_date, attempt)
            r = sess.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            r.raise_for_status()
            payload = r.json()

            # BSE wraps results in {"Table": [...]} — but the shape has
            # varied historically. Handle both.
            if isinstance(payload, dict):
                rows = payload.get("Table") or payload.get("data") or []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            log.info("bse fetch_announcements scrip=%s got %d rows", scrip_code, len(rows))
            return rows

        except requests.HTTPError as e:
            log.warning("bse http error (attempt %d): %s", attempt, e)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
        except requests.RequestException as e:
            log.warning("bse request error (attempt %d): %s", attempt, e)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    return []


def download_attachment(attachment_name: str, *, max_retries: int = 3) -> bytes:
    """
    Download a PDF attachment from BSE.

    Args:
        attachment_name: the ATTACHMENTNAME field from an announcement,
                         e.g. "5e3f8c10-7a2b-4d8e-9c1f-1234abcd5678.pdf"

    Returns:
        Raw PDF bytes.
    """
    url = f"{BSE_ATTACHMENT_BASE}/{attachment_name}"
    sess = _session()

    for attempt in range(1, max_retries + 1):
        try:
            log.info("bse download %s (attempt %d)", attachment_name, attempt)
            r = sess.get(url, timeout=_DOWNLOAD_TIMEOUT, stream=True)
            r.raise_for_status()
            data = r.content
            log.info("bse downloaded %s: %d bytes", attachment_name, len(data))
            return data
        except requests.HTTPError as e:
            log.warning("bse download http error (attempt %d): %s", attempt, e)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
        except requests.RequestException as e:
            log.warning("bse download request error (attempt %d): %s", attempt, e)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"failed to download {attachment_name}")


def parse_filing_date(dt_str: str) -> Optional[datetime]:
    """BSE returns dates like '2026-05-15 18:30:00' or '2026-05-15T18:30:00'.

    Some responses carry fractional seconds ('...:00.000'). Those are tried
    FIRST: strptime has no partial match, so a format without %f simply raises
    on them and the date silently becomes None — which downstream reads as "no
    filing date" rather than as a parse failure. Two extra formats, no other
    behaviour change.
    """
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None
