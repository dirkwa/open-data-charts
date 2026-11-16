#!/bin/bash
# A release's assets as one JSON array of {name, size, digest}: the release
# tagged $1, or the mirror release by default.
set -euo pipefail
tag=${1:-$MIRROR_TAG}
id=$(gh api "repos/$GITHUB_REPOSITORY/releases" --paginate --jq ".[] | select(.tag_name == \"$tag\") | .id" | head -1)
gh api "repos/$GITHUB_REPOSITORY/releases/$id/assets?per_page=100" --paginate \
  --jq '.[] | {name, size, digest}' | jq -s .
