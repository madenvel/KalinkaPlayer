#!/usr/bin/env bash
#
# Give GitHub's "Latest" badge back to the newest kalinka-v* release.
#
# Publishing anything marks it Latest unless something else is pinned, and
# --latest=false only declines the badge rather than handing it back. The slot
# belongs to the app bundle, so every release train that is not the bundle's
# runs this after publishing.
#
# Env: GH_TOKEN, GITHUB_REPOSITORY (both set for you inside Actions).
set -euo pipefail

newest=$(gh release list --repo "$GITHUB_REPOSITORY" --limit 100 \
  --json tagName,isDraft,isPrerelease \
  --jq '[.[] | select(.isDraft == false and .isPrerelease == false)
        | select(.tagName | startswith("kalinka-v"))][0].tagName')

if [ -n "$newest" ]; then
  echo "Pinning Latest back to $newest"
  gh release edit "$newest" --repo "$GITHUB_REPOSITORY" --latest
else
  echo "No published kalinka-v* release yet; leaving Latest alone"
fi
