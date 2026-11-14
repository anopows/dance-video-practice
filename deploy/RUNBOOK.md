# Operations

See [SELF_HOSTING.md](../SELF_HOSTING.md) for initial installation.

## Update and rollback

Record the deployed commit. Wait for active uploads/jobs, stop API and worker,
back up SQLite with its online backup API, and deploy the reviewed source.
Preserve `/etc/choreo.env`, `/var/lib/choreo`, and models.

Run `uv sync --frozen --extra worker` with the installed uv binary, verify models,
run tests, then restart both services. Validate Caddy before proxy changes.
Roll back to the previous source/lockfile if needed. Review schema compatibility
before restoring old code; never blindly replace a live WAL database.

## Retention

The default is 90 days from upload for originals, practice copies, instrument
estimates, analysis, and exports. API access ends at the project deadline.
The worker deletes expired objects and retries failures; R2's 90-day object
lifecycle provides a backstop.

When upgrading an older installation:

1. Increase the bucket lifecycle deletion to 90 days first.
2. Set `CHOREO_RETENTION_SECONDS=7776000` in `/etc/choreo.env`.
3. Back up SQLite and preview:
   `sudo .venv/bin/python scripts/extend-retention.py --days 90`.
4. Apply with `--apply`, then restart both services.

The migration extends only currently available projects to 90 days from their
original upload time. It never revives deleted, failed, or already expired
projects, and never shortens an existing longer deadline.

Run the worker continuously. Issued media URLs can remain usable until their
expiry (at most 15 minutes) or object deletion. Treat share links as view access.

## Inspect

```sh
systemctl status choreo-api choreo-worker caddy
journalctl -u choreo-worker --since '1 hour ago'
curl --fail https://YOUR_DOMAIN/healthz
df -h /var/lib/choreo
```

Caddy replaces CF-Connecting-IP with the direct peer address so clients cannot
spoof per-IP limits. Review the trust chain if adding a CDN or another proxy.
The supplied configuration assumes direct DNS to the host.

Access logging is disabled. Worker logs contain project IDs and bounded
diagnostics. Avoid posting private links or signed media URLs in reports.
Model-check errors mean assets differ from the manifest. Upload failures can
mean unsupported codecs, limits, or unusable music. Inspect recurring failures,
queue age, and free disk space before increasing quotas.

## Backups

Use SQLite's online backup API, not a copy of only the live database file.
Encrypt off-host backups, restrict permissions, and bound their retention:
records can contain filenames and analysis. R2 is live storage, not an independent
backup. No off-host backup destination is configured by the installer.

## Access and credentials

The password grants management of every room video; there is no per-user
isolation. Guests need a random project link and cannot enumerate the library,
delete videos, or overwrite shared count settings.

`/etc/choreo.env` is the persistent application secret file. Rotating the password
hash and clearing session rows signs everyone out, but does not revoke share
links. Delete a project to revoke its link. Rotate R2/Turnstile credentials by
replacing their environment values and restarting both services.

Smoke scripts accept only explicit synthetic fixtures and exercise storage,
worker, signed playback, and exports. Run them as `choreo` with the production
EnvironmentFile. They do not bypass HTTP login or replace browser upload tests.

## KPI counters

The application records aggregate UTC daily counts and daily pseudonymous distinct-visitor hashes in its own SQLite database. The hashes are pruned by the worker cleanup loop and never appear in exports. No access logs are enabled. The app's KPI timer runs its exporter as the application user and writes its JSON under `/var/lib/kpi/data/`; see the separate KPI repository for metric meanings, deployment and how to add fixed metric names. Existing database backups include the counters. Exported counts are best effort and are not an audit log.
