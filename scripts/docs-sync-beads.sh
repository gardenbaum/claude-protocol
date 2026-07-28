#!/usr/bin/env bash
# docs-sync-beads — refresh docs/vendor/beads/ from upstream
#
# Clones the Beads repo at the pinned v1.1.0 tag, copies the curated subset
# (website/docs -> site/, guides/, CHANGELOG.md) into docs/vendor/beads/,
# and records the upstream commit in UPSTREAM_COMMIT.txt.
#
# Usage:
#   mise run docs-sync-beads         # refresh from v1.1.0
#   BEADS_REF=main mise run docs-sync-beads   # refresh from main (for testing)
#
# Idempotent: overwrites the vendored tree, never leaves stale files.
# Exit non-zero on any failure (no half-refreshed state).

set -euo pipefail

REF="${BEADS_REF:-v1.1.0}"
REPO="https://github.com/gastownhall/beads.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$PROJECT_DIR/docs/vendor/beads"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Cloning $REPO @ $REF into $TMP"
git clone --depth 1 --branch "$REF" --quiet "$REPO" "$TMP/beads"

cd "$TMP/beads"
UPSTREAM_SHA="$(git rev-parse HEAD)"
echo "==> Upstream commit: $UPSTREAM_SHA"

echo "==> Refreshing $VENDOR_DIR"
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"

# Curated subset: CHANGELOG, the two guides with no site equivalent
# (WORKTREES.md, MULTI_REPO_MIGRATION.md live in docs/ upstream), and the
# user-facing website docs (website/docs -> site/ here)
cp -r CHANGELOG.md "$VENDOR_DIR/CHANGELOG.md"
mkdir -p "$VENDOR_DIR/guides"
for f in WORKTREES.md MULTI_REPO_MIGRATION.md; do
    [ -f "docs/$f" ] && cp "docs/$f" "$VENDOR_DIR/guides/$f"
done
cp -r website/docs "$VENDOR_DIR/site"

# Scoped prune: drop non-Claude integrations
echo "==> Pruning non-Claude integrations from site/"
for agent in aider cursor codex gemini factory junie mux windsurf cody kilocode copilot; do
    rm -rf "$VENDOR_DIR/site/integrations/$agent" 2>/dev/null || true
done

# Drop Beads' own dev-facing root files (they describe how to contribute to
# Beads, not how to use it)
echo "$UPSTREAM_SHA" > "$VENDOR_DIR/UPSTREAM_COMMIT.txt"

echo "==> Done. Vendored $(find "$VENDOR_DIR" -type f | wc -l) files."
echo "    Pin: $REF @ $UPSTREAM_SHA"
