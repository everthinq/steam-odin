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

# Push the backup branch; on failure keep the local commit (the next run retries).
push_branch() {
  local push_log
  push_log="$(mktemp -t portfolio-backup-push.XXXXXX)"
  if git push "$REMOTE" "refs/heads/$BRANCH:refs/heads/$BRANCH" 2>"$push_log"; then
    rm -f "$push_log"
    echo "[backup] pushed $BRANCH @ $(git rev-parse --short "refs/heads/$BRANCH") ($1)"
  else
    echo "[backup] local commit $(git rev-parse --short "refs/heads/$BRANCH") made, but push failed:" >&2
    cat "$push_log" >&2
    rm -f "$push_log"
    exit 1
  fi
}

# Resolve the current tip of the backup branch (may not exist yet).
parent=""
if parent="$(git rev-parse --verify -q "refs/heads/$BRANCH")"; then
  # Skip if the file is byte-identical to what the branch already holds...
  prev="$(git rev-parse -q --verify "$parent:$FILE" 2>/dev/null || echo '')"
  if [[ "$prev" == "$blob" ]]; then
    # ...but only once that commit has actually reached the remote. The local
    # branch moves as soon as a backup is committed, so after a failed push the
    # file looks "unchanged" here while the remote never got it. Compare with
    # what the remote really holds (a failed lookup = not pushed) and retry the
    # push when the local branch is ahead.
    remote_tip="$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" 2>/dev/null | head -n1 | cut -f1 || true)"
    if [[ "$remote_tip" == "$parent" ]]; then
      echo "[backup] portfolios.json unchanged since last backup; skipping"
      exit 0
    fi
    echo "[backup] portfolios.json unchanged, but the last backup never reached $REMOTE; pushing it"
    push_branch "retry"
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

push_branch "$ts"
