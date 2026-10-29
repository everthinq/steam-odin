#!/bin/sh
# Pre-commit guard: never commit a secret file or a stored secret (Ivan's rule).
# Install once:  ln -sf ../../scripts/pre-commit-secret-guard.sh .git/hooks/pre-commit
#
# 1. Refuses staged secret files (maFiles, the Mímir vault and its backups, keys,
#    settings, CSFloat keys, the Telegram caller session, .env, ASF config).
# 2. Refuses staged changes that contain any value kept in Mímir (password, email,
#    billing address) or in a maFile, checked by backend/secret_scan.py inside the
#    backend container, which holds the key. If the container is not running the
#    commit is refused too: start it (make raid) and commit again.
CONTAINER=steam-odin-heimdall-backend-1

secret_files=$(git diff --cached --name-only --diff-filter=ACMR | grep -E \
  '(\.maFile$|credentials\.vault|\.heimdall_key|\.heimdall_salt|Yggdrasil/heimdall/backend/settings\.json|csfloat_keys\.json|telegram_caller\.json|(^|/)\.env$|^Yggdrasil/asf/config/)')
if [ -n "$secret_files" ]; then
  echo "pre-commit: refusing to commit secret files:" >&2
  echo "$secret_files" >&2
  exit 1
fi

if ! docker exec "$CONTAINER" true 2>/dev/null; then
  echo "pre-commit: the backend container is not running, so the staged changes cannot be" >&2
  echo "checked against Mímir and the maFiles: start it (make raid) and commit again." >&2
  exit 1
fi

git diff --cached --no-color -U0 | grep '^+' | docker exec -i "$CONTAINER" python /app/secret_scan.py
