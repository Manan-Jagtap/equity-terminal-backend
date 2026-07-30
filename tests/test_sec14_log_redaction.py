"""SEC-14: credentials must never reach a log line.

httpx logs request URLs at INFO (not DEBUG), so the Dhan token mint wrote the
account PIN and TOTP into the scheduler log on every renewal. Rotating the PIN
did not help — the replacement leaked identically on the next renewal.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from app.log_redact import RedactFilter, install, scrub

# The literal line observed in the scheduler log on 2026-07-30.
LEAKED = ('HTTP Request: POST https://auth.dhan.co/app/generateAccessToken'
          '?dhanClientId=1112267166&pin=212724&totp=314710 "HTTP/1.1 200 "')


def test_scrubs_the_line_that_actually_leaked():
    out = scrub(LEAKED)
    assert "212724" not in out, "the PIN survived redaction"
    assert "314710" not in out, "the TOTP survived redaction"
    assert "pin=***" in out and "totp=***" in out


def test_keeps_the_line_useful():
    """Redaction must not destroy diagnostics — only the secret values go."""
    out = scrub(LEAKED)
    assert "auth.dhan.co" in out
    assert "dhanClientId=1112267166" in out, "client id is not a secret"
    assert "HTTP/1.1 200" in out


@pytest.mark.parametrize("key", ["pin", "totp", "password", "secret", "token",
                                 "access_token", "api_key", "client_secret"])
def test_every_credential_key_is_covered(key):
    assert f"{key}=***" in scrub(f"GET /x?{key}=hunter2&keep=1")


def test_case_insensitive_and_leaves_other_params():
    out = scrub("GET /x?PIN=999&Token=abc&page=3")
    assert "999" not in out and "abc" not in out
    assert "page=3" in out


def test_httpx_request_logging_is_off_at_source():
    """Layer 1: the URL line should not be emitted at all."""
    install()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_filter_rewrites_a_real_record():
    """Layer 2: defence in depth for any other library that logs a URL."""
    rec = logging.LogRecord("x", logging.INFO, __file__, 1,
                            "POST /a?pin=%s", ("212724",), None)
    assert RedactFilter().filter(rec) is True
    assert "212724" not in rec.getMessage()


def test_install_is_idempotent():
    install(); install()
    root = logging.getLogger()
    assert sum(isinstance(f, RedactFilter) for f in root.filters) == 1


def test_both_entrypoints_install_it():
    for p in ("scheduler.py", os.path.join("app", "main.py")):
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), p)).read()
        assert "log_redact" in src, f"{p} does not install redaction"
