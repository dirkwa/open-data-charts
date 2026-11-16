#!/bin/bash
# The mirror release's assets as one JSON array of {name, size, digest}.
set -euo pipefail
id=$(gh api "repos/$GITHUB_REPOSITORY/releases" --paginate --jq ".[] | select(.tag_name == \"$MIRROR_TAG\") | .id" | head -1)
gh api "repos/$GITHUB_REPOSITORY/releases/$id/assets?per_page=100" --paginate \
  --jq '.[] | {name, size, digest}' | jq -s .
