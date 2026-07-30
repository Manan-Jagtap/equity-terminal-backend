"""Scrub credentials out of log records.

SEC-14. httpx logs every request URL at INFO — not DEBUG — so
`httpx.post(url, params={"pin": ..., "totp": ...})` in app/dhan/auth.py wrote

    POST https://auth.dhan.co/app/generateAccessToken?dhanClientId=...&pin=212724&totp=...

into the scheduler log on every token mint (each restart plus each auto-renew).
The PIN was recoverable by anyone with log access, and rotating it did not help:
the next PIN leaked identically on the next renewal.

Two layers, deliberately:

1. httpx's request logger is raised to WARNING, which removes the URL line at
   source. That alone fixes the known leak.
2. A filter on the ROOT logger redacts credential-ish query params from any
   record, whatever emits it. Layer 1 fixes what we found; layer 2 covers the
   next library that logs a URL we did not think about.

The auth call itself is deliberately NOT changed. Moving the credentials into
the POST body would be the stronger fix, but Dhan's endpoint may not accept
them there and a broken token mint takes the whole price pipeline down. That
belongs in a change that can be tested against the live endpoint.
"""
import logging
import re

# Query params whose VALUES must never reach a log line.
_SECRET_KEYS = ("pin", "totp", "password", "passwd", "secret", "token",
                "access_token", "api_key", "apikey", "client_secret", "auth")
_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(map(re.escape, _SECRET_KEYS)) + r")=([^&\s\"']+)")
_REPLACEMENT = r"\1=***"


def scrub(text: str) -> str:
    """Replace `pin=212724` with `pin=***`, leaving the rest of the line intact."""
    return _PATTERN.sub(_REPLACEMENT, text)


class RedactFilter(logging.Filter):
    """Rewrites the formatted message; leaves records without secrets untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:                      # noqa: BLE001 — never break logging
            return True
        cleaned = scrub(msg)
        if cleaned != msg:
            record.msg, record.args = cleaned, ()
        return True


def install() -> None:
    """Idempotent; safe to call from every entrypoint."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    root = logging.getLogger()
    if not any(isinstance(f, RedactFilter) for f in root.filters):
        root.addFilter(RedactFilter())
    for h in root.handlers:
        if not any(isinstance(f, RedactFilter) for f in h.filters):
            h.addFilter(RedactFilter())
