#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --frozen --extra dev pytest -q
node --test tests/*.test.cjs
for file in choreo/static/*.js; do node --check "$file"; done
