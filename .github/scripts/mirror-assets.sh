#!/bin/bash
# Release assets as one JSON array of {release, name, size, digest}: those of
# the release tagged $1, or by default those of every mirror release
# (mirror and mirror-0 … mirror-f).
set -euo pipefail
if [ $# -gt 0 ]; then
  select=".tag_name == \"$1\""
else
  select='.tag_name == env.MIRROR_TAG or (.tag_name | test("^" + env.MIRROR_TAG + "-[0-9a-f]$"))'
fi
gh api "repos/$GITHUB_REPOSITORY/releases" --paginate --jq ".[] | select($select) | \"\(.id) \(.tag_name)\"" |
  while read -r id tag; do
    gh api "repos/$GITHUB_REPOSITORY/releases/$id/assets?per_page=100" --paginate \
      --jq ".[] | {release: \"$tag\", name, size, digest}"
  done | jq -s .
