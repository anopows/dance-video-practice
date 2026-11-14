# R2 cost safeguard

The runtime can enforce persistent allowances **before** each S3 request, including
SDK retries. Enable `CHOREO_R2_GUARD_ENABLED=true` only after initializing
`/var/lib/choreo/r2-guard.sqlite3` as the application user. Missing, corrupt, or
unreadable state fails closed. Initialization refuses to overwrite existing state.

Default allowances are 20,000,000 S3 attempts, 100 GB uploaded, and 1 TB of media
responses served. These are **lifetime allowances from installation, not monthly
resets**. Every retry reserves another request and upload bytes; failed uploads
and object deletions do not refund allowances. Exceeding any allowance latches a
stop that survives API/worker restarts. A month change does not remove the stop.

Only the S3 operations used by the application are permitted. Server-side copies,
bucket administration, and non-Standard storage writes are refused. Public media
uses signed application URLs with at most 15 minutes of validity. The application
checks the project deadline and cost latch, supports single byte ranges, and counts
both the metadata request and object request. Previously issued application URLs
also stop working when the latch is set. Streaming responses close the R2 body on
completion or disconnect. A separate 1 TB response allowance bounds server egress.

## Required access restrictions

Keep the R2 bucket private: no public r2.dev endpoint, custom domain, or Worker
binding that bypasses the guard. Restrict the runtime object-read/write token to
this bucket and to the application server's public IPv4/IPv6 addresses. **Deploy
the application media proxy before applying IP restrictions**, then verify that
server-mediated playback works and direct signed R2 links return 403 externally.
Existing clients using old R2 links must reload. If the server IP changes, update
the token's IP allowlist before moving traffic.

Other administrator credentials or other services can still access R2 outside
these limits; restrict each additional access path before reusing the bucket.
This guard is not protection against compromise of the application host or an
account administrator disabling the controls.

## Cost envelope and limits of the protection

At Standard R2 pricing verified on 14 November 2026, Class A costs $4.50/million,
Class B $0.36/million, and storage $0.015/GB-month. Charging **all** 20 million
attempts at Class A gives a conservative $90 request allowance before free-tier
credits. The additional 100 GB upload allowance bounds incremental retained data
to roughly $1.50/month, plus pre-existing objects, metadata, and billing rounding.
The retained bytes are not deleted by a kill switch and continue to incur storage
charges until lifecycle cleanup. Keep the existing retention and multipart-abort
rules active. This is an application-level cost envelope with margin below $200,
not a provider-guaranteed $200 account invoice cap. Prior spend, other products,
taxes, bypass credentials, or future price changes are outside the envelope.

Price reference: https://developers.cloudflare.com/r2/pricing/

## Budget webhook backup

Configure `CHOREO_R2_WEBHOOK_SECRET`, `CHOREO_R2_ACCOUNT_ID`, and
`CHOREO_R2_BUDGET_POLICY_ID`. Point a Cloudflare generic webhook at
`https://YOUR_HOST/hooks/cloudflare-budget`, using that secret. A matching
`cf-webhook-auth` header, account ID, policy ID, and `billing_budget_alert` type
are required. The expected budget policy sets the latch; duplicates are
idempotent. End events cannot resume service. Cloudflare destination-test messages
are acknowledged without stopping access. Ordinary API rate limits do not block
the webhook during a traffic spike. The endpoint has no resume capability.

Cloudflare budget alerts are delayed, informational account-wide alerts, so this
webhook backs up the pre-request allowances. It does not replace them. The
receiver does **not** store a Cloudflare administrator credential or revoke the
runtime token. The IP restriction plus local cutoff closes the runtime access
path without granting a webhook token-administration privileges.

## Operations

Run as the application user so the SQLite database/WAL stay writable:

```sh
sudo -u choreo /opt/choreo/.venv/bin/python /opt/choreo/scripts/r2-guard.py status
sudo -u choreo /opt/choreo/.venv/bin/python /opt/choreo/scripts/r2-guard.py stop --reason 'Operator shutdown'
```

After investigating the cause, checking Cloudflare billing, and confirming that
the allowances have room, a manual resume retains all counters:

```sh
sudo -u choreo /opt/choreo/.venv/bin/python /opt/choreo/scripts/r2-guard.py resume --reviewed
```

If an allowance is exhausted, resume refuses. Review current usage and pricing,
then explicitly increase only the reviewed allowance in SQLite. Do not reset,
delete, or restore an older copy of the counter database: that restores spending
capacity. Back it up using SQLite's backup API, separately from `room.sqlite3`.
For code rollback, keep the media-proxy/guard behavior and token IP restrictions;
rolling back to direct R2 URLs while restrictions are active breaks playback.

## Verification

`tests/test_r2_guard.py` covers concurrent reservations, persistent stops, missing
or corrupt state, actual SDK retries via a local HTTP server, upload bytes,
forbidden operations/storage classes, media signatures/ranges/expiry, webhook
authentication/scope/probes/duplicates, and webhook delivery under API saturation.
Also run service and retention regression tests. Production smoke checks should
verify small multipart uploads, byte-range playback, outside-IP denial, an
authenticated stop, restart persistence, and manual recovery with counters intact.
