#!/usr/bin/env bash
# Force-push a generated directory to a same-named orphan branch.
# Mirrors sing-geosite/.github/release-rule-set.sh (no GitHub releases).
# Consumed by Surge via:
#   https://raw.githubusercontent.com/<owner>/<repo>/<dir>/geosite-<code>.list
set -euo pipefail

dir="$1"
ver="${2:-unknown}"

cd "$dir"
git init -q
git checkout -q -b "$dir"
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "Update ${dir} (v2fly/domain-list-community@${ver})"
git push -f -q "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$dir"
echo "pushed branch '${dir}'"
