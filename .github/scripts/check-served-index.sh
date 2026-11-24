#!/usr/bin/env bash
# Stops when the index Pages serves is not the one a run started from (its
# sha256, or "none" when Pages served none). Usage: check-served-index.sh BASELINE
set -euo pipefail
baseline=$1
status=$(curl -sSL --retry 3 -o served.json -w '%{http_code}' "$PAGES_URL/open-data-index.json?run=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT")
case "$status" in
  200) served=$(sha256sum served.json | cut -c1-64) ;;
  404) served=none ;;
  *) echo "::error::$PAGES_URL/open-data-index.json answered $status"; exit 1 ;;
esac
if [ "$served" != "$baseline" ]; then
  echo "::error::The served index changed after this run started. Re-run the whole workflow, not only this job."
  exit 1
fi
