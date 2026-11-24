#!/usr/bin/env bash
# Stops when an index lists a unit or hillshade of the region that its release
# no longer holds, as after a deletion by a later run.
# Usage: check-index-assets.sh INDEX REGION
set -euo pipefail
index=$1
region=$2
here=$(dirname "$0")
listed=$(jq -r --arg r "$region" '.units[] | select(.region == $r) | .url, (.hillshade.url // empty)
  | capture("/releases/download/(?<tag>[^/]+)/(?<name>[^/]+)$") | "\(.tag) \(.name)"' "$index" | sort)
released=$(bash "$here/mirror-assets.sh" "units-$region" | jq -r '.[] | "\(.release) \(.name)"' | sort)
missing=$(comm -23 <(echo "$listed") <(echo "$released") | grep -c . || true)
if [ "$missing" -gt 0 ]; then
  comm -23 <(echo "$listed") <(echo "$released") | head -20
  echo "::error::$missing assets this index lists are gone from the release. Re-run the whole workflow, not only this job."
  exit 1
fi
echo "All $(echo "$listed" | grep -c .) assets of $region the index lists are released."
