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

The default is 90 days from upload for practice copies, instrument estimates,
analysis, and exports. Originals are deleted after successful analysis. API access ends at the project deadline.
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

Run the worker continuously. Media requests recheck access, so holds, removal and expiry revoke previously issued links on the next request. Treat share links as view access.

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

Accounts own isolated libraries. The admin needs account sign-in plus password/Turnstile step-up for moderation. Guests need a random project link and cannot enumerate the library, delete videos, or overwrite shared count settings.

`/etc/choreo.env` is the persistent application secret file. Rotating the password
hash and clearing session rows signs everyone out, but does not revoke share
links. Delete a project to revoke its link. Rotate R2/Turnstile credentials by
replacing their environment values and restarting both services.

Smoke scripts accept only explicit synthetic fixtures and exercise storage,
worker, signed playback, and exports. Run them as `choreo` with the production
EnvironmentFile. They do not bypass HTTP login or replace browser upload tests.

## KPI counters

The application records aggregate UTC daily counts and daily pseudonymous distinct-visitor hashes in its own SQLite database. The hashes are pruned by the worker cleanup loop and never appear in exports. No access logs are enabled. The app's KPI timer runs its exporter as the application user and writes its JSON under `/var/lib/kpi/data/`; see the separate KPI repository for metric meanings, deployment and how to add fixed metric names. Existing database backups include the counters. Exported counts are best effort and are not an audit log.

## Public launch M1 implementation — 14 November 2026

Legal pages are read from `CHOREO_LEGAL_DIR`; all three must be complete and the legal notice must include `id="report"`. Missing files or unresolved template tokens keep the routes unavailable. Install reviewed operator-specific files outside the repository.

`/healthz` checks the database, a worker heartbeat no older than 120 seconds and the local cost guard. Install `choreo-health.timer` with `/etc/choreo-health.env` containing `HC_HEALTH_URL`. The check verifies HTTPS health and 10 GB free disk.

For nightly backups install `age` and `rclone`, a bucket-scoped `/etc/choreo-backup.conf` (remote `backups`), and `/etc/choreo-backup.env` with `AGE_RECIPIENT` and `HC_BACKUP_URL`. Keep these files mode 0600 owned by choreo. Enable `choreo-backup.timer` for 03:40 UTC. Keep the private age key off the host. Restore quarterly on a separate machine and record the integrity and API checks.

Local foundation acceptance tests passed. Production rollout has not occurred: GitHub publication/CI, backup credentials, monitoring endpoints and the restore/alert drills remain launch gates. The inspected host has 4 vCPU, 8 GB RAM and an 80 GB disk; M3 requires the design's larger host. Existing services were healthy during the read-only check.

## Public-launch implementation, 14 November 2026

See PUBLIC_LAUNCH.md for the authoritative new release procedure and IMPLEMENTATION_DECISIONS.md for security corrections to the handover. These additions supersede old shared-room login instructions for version 2.x. All new work remains local; production still runs its prior release. Host upgrade, model calibration, owner legal/provider inputs, backup restore drill, remote CI and the 14-day soft launch are not yet evidenced.


### Recorded local verification

On 14 November 2026, `uv run --frozen --extra dev --with 'scipy>=1.14,<2' pytest -q` passed all 138 tests. `node --test tests/*.test.cjs` passed 13 tests; every static JavaScript file passed `node --check`, and `git diff --check` passed. The SciPy-enabled run includes the four optional consensus tests omitted by the basic development environment. The localhost SDK-retry test requires permission to bind a local test socket.

Both timing benchmark suites passed all 12 cases (development seed 60117 and validation seed 841903). Hash-verified real screening models loaded locally and rejected a generated 10-second colour-bar/tone clip with `no_person` in 7.258 seconds (10 sampled frames, zero nudity flags, zero person detections). This is a negative smoke result, not a dance-video calibration or a production performance result.

Chrome verification against an isolated local test server completed email request, captured-mail confirmation, private library, quota display and admin password step-up. The confirmation fragment was removed before submission; the browser reported no console errors. Test storage, test Turnstile and captured email were used. Real Google/SMTP delivery, Linux sandbox execution, 30-clip model calibration and production upload/playback remain explicit release gates.
