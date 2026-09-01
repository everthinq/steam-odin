#!/usr/bin/env bash
#
# Off-machine backup of Draupnir portfolio data (fix #3).
#
# Commits Yggdrasil/heimdall/backend/portfolios.json to a dedicated
# `portfolio-backup` branch and pushes it to origin, giving you versioned,
# off-machine history (git's log IS the point-in-time timeline).
#
# Why a dedicated branch built with plumbing, not a normal `git commit`:
#   * it NEVER touches your working tree, staging area, or current branch — safe
#     to run on a cron while you have uncommitted code edits open;
#   * backup commits don't pollute `master` history or fight your dev pushes.
#
# The file holds only item names / prices / dates — no secrets. maFiles and the
# encryption key are intentionally NOT backed up here (keep those in iCloud/USB).
#
# Enable hourly (macOS launchd) — see scripts/com.steamodin.portfolio-backup.plist
# Or cron:  0 * * * * /path/to/steam-odin/scripts/backup_portfolios.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FILE="Yggdrasil/heimdall/backend/portfolios.json"
BRANCH="portfolio-backup"
REMOTE="origin"

cd "$REPO"

if [[ ! -f "$FILE" ]]; then
  echo "[backup] $FILE not found; nothing to do" >&2
  exit 0
fi

# Hash the current file into the object database.
blob="$(git hash-object -w "$FILE")"

# Resolve the current tip of the backup branch (may not exist yet).
parent=""
if parent="$(git rev-parse --verify -q "refs/heads/$BRANCH")"; then
  # Skip if the file is byte-identical to what the branch already holds.
  prev="$(git rev-parse -q --verify "$parent:$FILE" 2>/dev/null || echo '')"
  if [[ "$prev" == "$blob" ]]; then
    echo "[backup] portfolios.json unchanged since last backup; skipping"
    exit 0
  fi
fi

# Build a tree containing just the file, in a throwaway index (never the real one).
tmp_index="$(mktemp -t portfolio-backup-index.XXXXXX)"
trap 'rm -f "$tmp_index"' EXIT
export GIT_INDEX_FILE="$tmp_index"
if [[ -n "$parent" ]]; then
  git read-tree "$parent"
else
  git read-tree --empty
fi
git update-index --add --cacheinfo "100644,$blob,$FILE"
tree="$(git write-tree)"
unset GIT_INDEX_FILE

# Commit onto the backup branch and push.
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ -n "$parent" ]]; then
  commit="$(git commit-tree "$tree" -p "$parent" -m "portfolio backup $ts")"
else
  commit="$(git commit-tree "$tree" -m "portfolio backup $ts")"
fi
git update-ref "refs/heads/$BRANCH" "$commit"

if git push "$REMOTE" "$BRANCH" 2>/tmp/portfolio-backup-push.log; then
  echo "[backup] pushed $BRANCH @ ${commit:0:8} ($ts)"
else
  echo "[backup] local commit ${commit:0:8} made, but push failed:" >&2
  cat /tmp/portfolio-backup-push.log >&2
  exit 1
fi
