"""Install validated runtime secrets without placing credentials in git or argv.

sudo .venv/bin/python scripts/configure-runtime.py /root/r2-credentials.json
The JSON must contain access_key_id and secret_access_key. The password is read
with a hidden prompt, or from stdin when --password-stdin is explicitly passed.
Run provision-cloudflare.py first to create /etc/choreo-turnstile.env.
"""
import argparse
import getpass
import json
import os
import secrets
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from argon2 import PasswordHasher
from choreo.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("r2_credentials", type=Path)
    parser.add_argument("--account-config",type=Path,required=True,help="Root-only JSON of admin_email, abuse_email and SMTP/Google settings")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--config",type=Path,default=ROOT/"deploy/cloudflare-config.json")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root to install the root-only environment file.")
    if Path("/etc/choreo.env").exists():
        raise SystemExit("Existing environment must be upgraded in place; refusing to rotate secrets or overwrite configuration.")
    account=json.loads(args.account_config.read_text())
    allowed={"admin_email","abuse_email","smtp_host","smtp_port","smtp_user","smtp_password","mail_from","google_client_id","google_client_secret","legal_dir"}
    if set(account)-allowed:raise SystemExit("Unexpected account setting")
    config = json.loads(args.config.read_text())
    credentials = json.loads(args.r2_credentials.read_text())
    turnstile = dict(line.split("=", 1) for line in
                     Path("/etc/choreo-turnstile.env").read_text().splitlines() if line)
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Admin step-up password: ")
    if not password or len(password) > 128:
        raise SystemExit("Use a nonempty password of at most 128 characters.")
    values = {
        "origin": "https://" + config["hostname"],
        "state_dir": "/var/lib/choreo",
        "retention_seconds": "7776000",
        "password_hash": PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password),
        "turnstile_site_key": turnstile["CHOREO_TURNSTILE_SITE_KEY"],
        "turnstile_secret": turnstile["CHOREO_TURNSTILE_SECRET"],
        "r2_endpoint": f"https://{config['account_id']}.r2.cloudflarestorage.com",
        "r2_bucket": config["bucket"],
        "r2_access_key": credentials["access_key_id"],
        "r2_secret_key": credentials["secret_access_key"],
    }
    values.update({key:str(value) for key,value in account.items()})
    values.update(secret_key=secrets.token_urlsafe(48),r2_guard_enabled="true")
    if any(not isinstance(v, str) or any(c in v for c in "\r\n\x00") for v in values.values()):
        raise SystemExit("Invalid environment value; no configuration written.")
    Settings(**values).validate_production()
    # systemd reads these values literally; do not shell-source this file.
    fd, temporary = tempfile.mkstemp(prefix=".choreo.env-", dir="/etc")
    try:
        with os.fdopen(fd, "w") as output:
            for key, value in values.items():
                escaped=value.replace("\\","\\\\").replace('"','\\"')
                output.write(f'CHOREO_{key.upper()}="{escaped}"\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, "/etc/choreo.env")
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("Validated runtime configuration installed as root-only /etc/choreo.env.")


if __name__ == "__main__":
    main()
