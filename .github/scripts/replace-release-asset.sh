#!/bin/sh
# Replaces the asset $1 in the release $RELEASE with the uploaded
# next-$1: deletes the old asset, then renames the new one into its place.
# With "recover" as $2, a staged upload that never completed is dropped
# and the old asset kept.
set -eu
name="$1"
mode="${2:-}"
release_id=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$RELEASE" --jq .id)
asset() {
  gh api --paginate "repos/$GITHUB_REPOSITORY/releases/$release_id/assets?per_page=100" \
    --jq ".[] | select(.name == \"$1\") | \"\(.id) \(.state) \(.size)\""
}
old=$(asset "$name" | cut -d' ' -f1)
read -r staged state size <<STAGED
$(asset "next-$name")
STAGED
[ -n "$staged" ] || { echo "next-$name is not in the release" >&2; exit 1; }
if [ "$state" != uploaded ] || [ "$size" -eq 0 ]; then
  gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/assets/$staged"
  [ "$mode" = recover ] && exit 0
  echo "next-$name did not upload completely" >&2
  exit 1
fi
if [ -n "$old" ]; then
  gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/assets/$old"
fi
gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/assets/$staged" -f name="$name" >/dev/null
