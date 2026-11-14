from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from pydantic_settings import BaseSettings, SettingsConfigDict


TERMS_VERSION = 1


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREO_", env_file=None)
    origin: str = "https://fivesix.example.com"
    state_dir: Path = Path("/var/lib/choreo")
    legal_dir: Path = Path("/etc/choreo/legal")
    password_hash: str = ""
    turnstile_site_key: str = ""
    turnstile_secret: str = ""
    r2_endpoint: str = ""
    r2_bucket: str = "choreo-practice"
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_guard_enabled: bool = False
    r2_webhook_secret: str = ""
    r2_budget_policy_id: str = ""
    r2_account_id: str = ""
    secret_key: str = ""
    admin_email: str = ""
    abuse_email: str = ""
    signup_cap: int = 100
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    storage_cap_bytes: int = 2_000_000_000_000
    project_egress_day_bytes: int = 50_000_000_000
    user_uploads_day: int = 5
    user_uploads_month: int = 20
    ip_uploads_day: int = 15
    site_uploads_day: int = 150
    site_upload_bytes_day: int = 10_000_000_000
    analyze_queue_limit: int = 10
    email_daily_cap: int = 90
    session_seconds: int = 2592000
    retention_seconds: int = 90 * 86400
    upload_seconds: int = 1800
    max_bytes: int = 500_000_000
    part_bytes: int = 8 * 1024 * 1024
    max_duration: int = 600
    cookie_secure: bool = True
    trusted_proxy: str = "127.0.0.1"

    @property
    def hostname(self):
        return urlsplit(self.origin).hostname

    def validate_production(self):
        from .accounts import normalize_email
        if len(self.secret_key) < 32 or not self.r2_guard_enabled:
            raise RuntimeError("Production requires a stable secret key and the R2 cost guard")
        normalize_email(self.admin_email)
        normalize_email(self.abuse_email, aliases=False, disposable=False)
        if not self.smtp_host and not self.google_client_id:
            raise RuntimeError("Configure at least one sign-in method")
        if bool(self.google_client_id) != bool(self.google_client_secret):
            raise RuntimeError("Google ID and secret must both be configured")
        if self.smtp_host and (self.smtp_port != 587 or not all((self.smtp_user, self.smtp_password, self.mail_from))):
            raise RuntimeError("SMTP requires port 587, credentials and a From address")
        if any(getattr(self, key) <= 0 for key in ("signup_cap", "storage_cap_bytes", "project_egress_day_bytes", "user_uploads_day", "user_uploads_month", "ip_uploads_day", "site_uploads_day", "site_upload_bytes_day", "analyze_queue_limit", "email_daily_cap")):
            raise RuntimeError("Launch limits must be positive")
        fields = (self.password_hash, self.turnstile_site_key, self.turnstile_secret,
                  self.r2_endpoint, self.r2_access_key, self.r2_secret_key)
        if not all(fields):
            raise RuntimeError("Missing required server secrets; refusing to start")
        if not self.password_hash.startswith("$argon2id$"):
            raise RuntimeError("An Argon2id password hash is required")
        if not self.origin.startswith("https://") or not self.cookie_secure:
            raise RuntimeError("Production requires HTTPS and secure cookies")
        if self.turnstile_site_key.startswith(("1x000", "2x000", "3x000")):
            raise RuntimeError("Turnstile test keys are forbidden in production")
        if self.turnstile_secret.startswith(("1x000", "2x000", "3x000")):
            raise RuntimeError("Turnstile test secrets are forbidden in production")


@lru_cache
def settings():
    return Settings()
