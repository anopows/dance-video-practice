#!/usr/bin/env bash
set -euo pipefail
[[ -n "${HC_HEALTH_URL:-}" ]] || exit 0
result=0
curl --max-time 20 -fsS "${CHOREO_ORIGIN:?Missing site origin}/healthz" >/dev/null || result=1
free_bytes=$(/opt/choreo/.venv/bin/python -c 'import shutil,sys; print(shutil.disk_usage(sys.argv[1]).free)' "${CHOREO_STATE_DIR:-/var/lib/choreo}") || result=1
[[ "${free_bytes:-0}" -ge 10000000000 ]] || result=1
target="$HC_HEALTH_URL"
[[ "$result" == 0 ]] || target="$target/fail"
curl --max-time 20 -fsS "$target" >/dev/null
exit "$result"
