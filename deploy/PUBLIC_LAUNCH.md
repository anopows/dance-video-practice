# Public-launch release and recovery

## Local completion versus release

M0 baseline, M1 foundations, M2 accounts and M3 screening/moderation/cost implementation are separate local commits/branches. No branch has been pushed or merged and no production service has been changed. Automatic approval review rejected remote source publication. Existing unrelated working files were preserved.

The verified host is still 4 vCPU, approximately 8 GB RAM, 80 GB disk. It fails the M3 host gate. Do not infer provider credentials, legal approval, backup recoverability or screening calibration from passing local tests.

## Required operator evidence before rollout

- Legal operator identity/address/country; reviewed final Terms, Privacy and Legal Notice. Templates under deploy/legal-templates have deliberate placeholders; install finished files outside git under /etc/choreo/legal. The Legal Notice must contain id="report".
- Written suspected-abuse reporting and preservation procedure, including authority and retention. The service does not automatically submit authority reports.
- Admin and monitored abuse mailboxes; SMTP submission with verified STARTTLS on port 587, SPF/DKIM/DMARC; Google External production consent screen with openid/email and exact origin/auth/google/callback redirect. Test both sign-in methods using real delivered mail/OAuth.
- Stable random CHOREO_SECRET_KEY (at least 32 characters), mandatory R2 guard and appropriately scoped credentials; $60 account budget webhook configured and exercised.
- Host plan meeting 6 vCPU/16 GB/160 GB/20 TB traffic and the combined $200 allowance; priced invoice and traffic evidence must be checked before upgrade.
- An off-host age private key and its public recipient, separate site-backups bucket credentials, 30-day backup lifecycle, and healthchecks ping URLs. Never put private keys or service credentials in git.
- Screening calibration: every existing practice clip and at least 30 authorized real dance videos; zero rejects; less than 90 seconds for a ten-minute clip on the actual production host. Record model hashes, constants and JSON outputs. Follow the original design's bounded threshold-adjustment procedure; do not disable checks silently.

## Deploy order

1. Review local diffs and new dependencies, publish to the approved repository and obtain passing CI. Tag/record immutable source revisions. Never publish Songfinder private history.
2. M0: baseline 1.1. M1: foundations 1.2, legal routes/backup/health support and Songfinder 0.3. Before each deployment, verify queues are empty, stop affected processes and take a verified SQLite backup. Do not copy WAL files while writers run.
3. Configure backup/monitoring secrets and enable their timers only after successful manual runs and a decrypt/restore drill. M1 is not complete until off-host retrieval works.
4. Before M2 2.0: configure admin email, stable HMAC secret and SMTP. Start API and worker on the same version; Database.migrate performs one atomic v0-to-v1 migration, assigns existing projects to the admin, signs everyone out and pauses uploads. Use CHOREO_SIGNUP_CAP=5 for initial smoke checks. Verify email login, original library access, cross-account isolation, anonymous share access and account deletion with synthetic fixtures.
5. Before M3 2.1: confirm host/budget gates, stop services, back up room and guard, run scripts/r2-guard.py migrate-monthly, install/verify model files, deploy the matched API/worker, then run scripts/backfill-storage.py without --apply. After reviewing its exact list, use --apply. Record the derived/source ratio and set the daily source-byte cap to min(10 GB, floor(1.8e12/(90*ratio)/1e9)*1e9). If the result is zero, retain the upload pause; investigate output size instead of overriding the cap.
6. Run scripts/smoke-screen.py AUTHORIZED_DANCE_CLIP and python -m choreo.screen FILE... on the calibration set. No synthetic or mocked result counts as this gate. Validate hold, restore, removal, ban, quarantine and previous signed-link revocation using disposable test videos.
7. M4: verify every operator input, final legal content, OAuth, mail, restore drill, real model calibration and public HTTPS flows. Legal readiness alone is not proof these gates passed.
8. M5: set signup cap 100, explicitly resume uploads in admin or host CLI, then observe 14 days. Record daily costs, queue median below 10 minutes, disk >10 GB and no missed report targets. The 14-day acceptance gate cannot be pre-completed in a development session.

## Backup and monitoring setup

Install age/rclone and the included systemd services/timers. /etc/choreo-backup.env holds AGE_RECIPIENT and HC_BACKUP_URL; /etc/choreo-health.env holds HC_HEALTH_URL. Use owner choreo and mode 0600. /etc/choreo-backup.conf defines rclone remote `backups`, type s3, provider Cloudflare, endpoint and a token scoped only to site-backups. Directory prefix is fivesix/; Songfinder uses its own user/token and songfinder/ prefix. Backups run at 03:40/03:50 UTC; health checks every five minutes. A database backup contains personal records: encrypted backups expire after 30 days; document the deletion lag in Privacy.

A backup makes SQLite online snapshots, verifies integrity and row counts in restored databases, archives and age-encrypts them, and sends the encrypted object off-host. A successful upload alone is not a restore drill. Download a backup off-host, decrypt with the privately held key, extract into a private temporary directory, verify both databases, and start an isolated test instance with fake mail/storage. Remove the drill files afterwards. Models/media and secrets are not backed up by these jobs.

Health checks use the public HTTPS /healthz and local free space. Use four distinct checks (two health and two backup checks), resolving the owner-input table's inconsistent reference to three URLs. Configure external failure/grace thresholds so a missed timer run also alerts. Five·six /healthz is local-only internally: SQLite, worker heartbeat <=120 seconds and a usable, unstopped guard. It never makes a storage call.

## Recovery and emergency controls

Use scripts/admin.py through systemd-run --uid=choreo -p EnvironmentFile=/etc/choreo.env. Commands: pause-uploads --reason TEXT, resume-uploads, hold/remove/quarantine PID --reason TEXT, ban EMAIL --reason TEXT, unban EMAIL, release-hold EMAIL. release-quarantine PID --delete or --restore is host-only. Restoration refuses expired links; preservation beyond R2's 90-day lifecycle requires the approved operator procedure.

Do not replace an active database while processes run. For rollback, pause traffic/uploads, stop both processes, restore matching room and guard snapshots with service ownership, deploy the matching old code and restart. A v1 account database cannot be used with v0 room code. Restore the guard conservatively: counters from newer activity must not be discarded just to regain allowances; retain/reconcile the higher usage or keep the stop latched until reviewed. Verify API health, anonymous links, owner isolation and worker heartbeat before reopening.

The guard's stop can be reviewed through scripts/r2-guard.py status; use stop --reason TEXT immediately for a budget incident. A stopped guard blocks claims and media; upload pause alone preserves existing playback. Operational emails are capped at 50/day and per-type/target hourly; the admin report list remains the authoritative work queue when email is suppressed.

## Acceptance commands

`bash scripts/check.sh` runs backend, Node and JS syntax checks. Run both timing benchmark suites documented in README with the worker dependencies; they measure musical timing, not screening accuracy. Model smoke/calibration and real SMTP/Google/backup/HTTPS evidence remain separate host gates. Run Songfinder Python and Node suites in both private and public checkouts after the reviewed source export. Record exact results and revisions in RUNBOOK.md.
