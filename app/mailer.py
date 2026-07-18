"""app/mailer.py — minimal SMTP sender for transactional mail (no vendors,
DPDP-friendly: mail goes out through the platform's own mailbox).

Configured entirely by env (add to the server's app.env):
    EMAIL_SMTP_HOST  e.g. smtpout.secureserver.net  (GoDaddy Professional Email)
    EMAIL_SMTP_PORT  465 (SSL, default) or 587 (STARTTLS)
    EMAIL_SMTP_USER  admin@equityverdict.com
    EMAIL_SMTP_PASS  the mailbox password
    EMAIL_FROM       optional display-from; defaults to EMAIL_SMTP_USER

Unset → `configured()` is False and callers fall back to their no-email
behavior, so dev/tests/CI never need SMTP.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.getenv("EMAIL_SMTP_HOST") and os.getenv("EMAIL_SMTP_USER")
                and os.getenv("EMAIL_SMTP_PASS"))


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises on failure — callers decide whether
    that blocks the flow (signup verification: yes) or is best-effort."""
    host = os.environ["EMAIL_SMTP_HOST"]
    port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
    user = os.environ["EMAIL_SMTP_USER"]
    pw = os.environ["EMAIL_SMTP_PASS"]

    msg = EmailMessage()
    msg["From"] = os.getenv("EMAIL_FROM", user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if port == 587:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=20,
                              context=ssl.create_default_context()) as s:
            s.login(user, pw)
            s.send_message(msg)
