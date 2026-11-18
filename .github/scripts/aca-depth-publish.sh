#!/bin/sh
# Replaces the square $1 in the release $RELEASE with the uploaded
# next-$1: deletes the old asset, then renames the new one into its place.
set -eu
square="$1"
release_id=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$RELEASE" --jq .id)
asset_id() {
  gh api --paginate "repos/$GITHUB_REPOSITORY/releases/$release_id/assets?per_page=100" \
    --jq ".[] | select(.name == \"$1\") | .id"
}
old=$(asset_id "$square")
staged=$(asset_id "next-$square")
[ -n "$staged" ] || { echo "next-$square is not in the release" >&2; exit 1; }
if [ -n "$old" ]; then
  gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/assets/$old"
fi
gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/assets/$staged" -f name="$square" >/dev/null
