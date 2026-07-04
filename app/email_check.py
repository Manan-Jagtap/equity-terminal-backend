"""
app/email_check.py — the email-validation layer behind signup.

Three checks, cheapest first:
  1. strict syntax (beyond the permissive regex used elsewhere)
  2. disposable-domain blocklist (throwaway inboxes make the auth ledger and
     any future email verification useless)
  3. DNS existence: MX lookup via dnspython when available, A/AAAA fallback
     otherwise. FAIL OPEN on DNS timeouts/errors — a resolver hiccup must
     never block a real person from signing up.

Returns None when acceptable, else a human-readable reason.
"""
from __future__ import annotations
import re
import socket

_SYNTAX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com",
    "10minutemail.com", "10minutemail.net", "temp-mail.org", "tempmail.com",
    "tempmail.dev", "throwawaymail.com", "yopmail.com", "trashmail.com",
    "getnada.com", "dispostable.com", "maildrop.cc", "mintemail.com",
    "mohmal.com", "fakeinbox.com", "spamgourmet.com", "mailnesia.com",
    "emailondeck.com", "tempinbox.com", "burnermail.io", "mytemp.email",
}


def _domain_accepts_mail(domain: str, timeout: float = 3.0) -> bool | None:
    """True/False when we could determine it; None = couldn't check (fail open)."""
    try:
        import dns.resolver
        try:
            r = dns.resolver.Resolver()
            r.lifetime = timeout
            return len(r.resolve(domain, "MX")) > 0
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            pass  # no MX — fall through to A/AAAA (implicit MX rule)
        except Exception:
            return None
    except ImportError:
        pass
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(domain, 25)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return None


def validate_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    if not _SYNTAX.match(e) or ".." in e:
        return "That doesn't look like a valid email address"
    domain = e.rsplit("@", 1)[1]
    if domain in DISPOSABLE_DOMAINS:
        return "Disposable email addresses aren't accepted — please use a real inbox"
    if _domain_accepts_mail(domain) is False:
        return f"The domain {domain} doesn't appear to accept email — check for a typo"
    return None
