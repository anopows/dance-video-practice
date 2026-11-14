"""Verified TLS SMTP and bounded operational notices. Never log message contents."""
from email.message import EmailMessage
import html
import logging
import smtplib
import ssl
import time

from .db import kpi_count
from . import kpi

LOG = logging.getLogger(__name__)


class Mailer:
    def __init__(self, cfg, db, capture=None):
        self.cfg, self.db, self.capture = cfg, db, capture

    def send(self, recipient, subject, body):
        if not self.cfg.smtp_host:
            raise RuntimeError("Email transport is not configured")
        msg = EmailMessage()
        msg["From"] = self.cfg.mail_from
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(body)
        # Escape every operator/user-supplied string; only our own sign-in URL is linked.
        content = html.escape(body)
        for word in body.split():
            if word.startswith(self.cfg.origin + "/auth/email#t="):
                escaped = html.escape(word, quote=True)
                content = content.replace(escaped, '<a href="' + escaped + '">' + escaped + '</a>')
        msg.add_alternative("<html><body><p>" + content.replace("\n", "<br>") + "</p></body></html>", subtype="html")
        if self.capture is not None:
            self.capture(msg)
            return
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
            smtp.send_message(msg)

    def signin(self, email, token):
        self.send(email, "Your five·six sign-in link", "Open this link to sign in to five·six:\n\n"
                  + self.cfg.origin + "/auth/email#t=" + token + "\n\n"
                  "The link works once and expires in 15 minutes.\n"
                  "If you did not ask for it, ignore this email. Nobody can sign in without the link.\n\n"
                  "five·six · " + self.cfg.origin)

    def notify(self, kind, target, recipient, subject, body):
        if not self.cfg.smtp_host or not recipient:
            return False
        now = time.time()
        with self.db.connect(write=True) as con:
            bucket = "mail:" + kind + ":" + target
            if con.execute("SELECT count(*) FROM requests WHERE bucket='mail:system' AND stamp>?", (now-86400,)).fetchone()[0] >= 50:
                return False
            if con.execute("SELECT 1 FROM requests WHERE bucket=? AND stamp>?", (bucket, now-3600)).fetchone():
                return False
            con.executemany("INSERT INTO requests VALUES (?,?)", [("mail:system", now), (bucket, now)])
        try:
            self.send(recipient, subject, body)
            return True
        except Exception:
            # Provider errors can contain credentials/addresses. Do not print them.
            LOG.error("Operational email failed (type=%s)", kind)
            return False
