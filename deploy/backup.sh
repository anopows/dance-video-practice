#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${AGE_RECIPIENT:?Missing AGE_RECIPIENT}"
temporary=$(mktemp -d "${CHOREO_STATE_DIR:-/var/lib/choreo}/backup.XXXXXX")
trap 'rm -rf -- "$temporary"' EXIT
"/opt/choreo/.venv/bin/python" -m choreo.backup "$temporary/db"
tar -C "$temporary/db" -cf "$temporary/database.tar" .
age -r "$AGE_RECIPIENT" -o "$temporary/database.tar.age" "$temporary/database.tar"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
rclone --config "/etc/choreo-backup.conf" copyto "$temporary/database.tar.age" "backups:site-backups/fivesix/$stamp.tar.age"
rm -rf -- "$temporary"
trap - EXIT
if [[ -n "${HC_BACKUP_URL:-}" ]]; then curl --max-time 20 -fsS "$HC_BACKUP_URL" >/dev/null; fi
