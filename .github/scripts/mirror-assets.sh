#!/bin/bash
# Release assets as one JSON array of {release, name, size, digest}: those of
# the release tagged $1 (by default $MIRROR_TAG) and of its buckets $1-0 … $1-f.
set -euo pipefail
export TAG="${1:-$MIRROR_TAG}"
select='.tag_name == env.TAG or (.tag_name | test("^" + env.TAG + "-[0-9a-f]$"))'
gh api "repos/$GITHUB_REPOSITORY/releases" --paginate --jq ".[] | select($select) | \"\(.id) \(.tag_name)\"" |
  while read -r id tag; do
    gh api "repos/$GITHUB_REPOSITORY/releases/$id/assets?per_page=100" --paginate \
      --jq ".[] | {release: \"$tag\", name, size, digest}"
  done | jq -s .
