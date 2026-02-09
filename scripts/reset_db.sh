#!/usr/bin/env bash
#
# Reset the job database and optionally workspace to start from scratch.
# Uses the same env vars as the app: JOB_DB_PATH, WORKSPACE_PATH.
#
# Usage (from repo root):
#   ./scripts/reset_db.sh           # Clear DB + all job workspaces
#   ./scripts/reset_db.sh --db-only # Clear DB only, keep workspace folders
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Same defaults as crew_studio/llamaindex_web_app.py
JOB_DB_PATH="${JOB_DB_PATH:-./crew_jobs.db}"
WORKSPACE_PATH="${WORKSPACE_PATH:-./workspace}"

DB_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --db-only) DB_ONLY=true ;;
    -h|--help)
      echo "Usage: $0 [--db-only]"
      echo "  --db-only   Remove only the DB file; keep workspace job folders."
      echo "  (default)   Remove DB and all job-* folders under WORKSPACE_PATH."
      exit 0
      ;;
  esac
done

echo "Resetting from scratch..."
echo "  JOB_DB_PATH=$JOB_DB_PATH"
echo "  WORKSPACE_PATH=$WORKSPACE_PATH"
echo ""

# 1. Remove database
if [ -f "$JOB_DB_PATH" ]; then
  rm -f "$JOB_DB_PATH"
  echo "Removed: $JOB_DB_PATH"
else
  echo "No DB file at: $JOB_DB_PATH"
fi

# 2. Remove job workspaces (unless --db-only)
if [ "$DB_ONLY" = false ] && [ -d "$WORKSPACE_PATH" ]; then
  count=0
  for dir in "$WORKSPACE_PATH"/job-*; do
    [ -d "$dir" ] || continue
    rm -rf "$dir"
    count=$((count + 1))
  done
  if [ "$count" -gt 0 ]; then
    echo "Removed $count job workspace(s) under $WORKSPACE_PATH"
  else
    echo "No job-* folders under $WORKSPACE_PATH"
  fi
else
  echo "Workspace left unchanged (--db-only or missing WORKSPACE_PATH)"
fi

echo ""
echo "Done. Restart the backend to use a fresh DB."
