from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREO_", env_file=None)
    origin: str = "https://fivesix.example.com"
    state_dir: Path = Path("/var/lib/choreo")
    password_hash: str = ""
    turnstile_site_key: str = ""
    turnstile_secret: str = ""
    r2_endpoint: str = ""
    r2_bucket: str = "choreo-practice"
    r2_access_key: str = ""
    r2_secret_key: str = ""
    session_seconds: int = 28800
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
