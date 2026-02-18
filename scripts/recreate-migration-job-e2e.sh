#!/usr/bin/env bash
# Recreate the legacy-inventory-system migration job (47e43e65-e00b-4250-8a95-64343326de17)
# and run migration e2e. Requires backend at http://localhost:8080.
#
# Usage: ./scripts/recreate-migration-job-e2e.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

API_BASE="${API_BASE:-http://localhost:8080}"
VISION="[MTA] legacy-inventory-system.zip"

# Paths: source zip and MTA report (from original job's docs)
SOURCE_ZIP="${REPO_ROOT}/legacy-inventory-system.zip"
OLD_JOB_WS="${REPO_ROOT}/workspace/job-47e43e65-e00b-4250-8a95-64343326de17"
REPORT_JSON="${OLD_JOB_WS}/docs/c6e9599b-2e82-4cf6-a6e0-65282376cd12_issues.json"

if [[ ! -f "$SOURCE_ZIP" ]]; then
  echo "Missing source zip: $SOURCE_ZIP"
  exit 1
fi
if [[ ! -f "$REPORT_JSON" ]]; then
  echo "Missing MTA report: $REPORT_JSON"
  echo "Using a copy from repo root if present: mta-issues.json"
  REPORT_JSON="${REPO_ROOT}/mta-issues.json"
  if [[ ! -f "$REPORT_JSON" ]]; then
    exit 1
  fi
fi

echo "Creating migration job: vision=$VISION"
echo "  source_archive: $SOURCE_ZIP"
echo "  document (MTA report): $REPORT_JSON"
echo "  API: $API_BASE"
echo ""

RESP=$(curl -s -X POST "$API_BASE/api/jobs" \
  -F "vision=$VISION" \
  -F "mode=migration" \
  -F "source_archive=@$SOURCE_ZIP" \
  -F "documents=@$REPORT_JSON")

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('job_id',''))" 2>/dev/null || true)
if [[ -z "$JOB_ID" ]]; then
  echo "Failed to create job. Response: $RESP"
  exit 1
fi
echo "Created job_id: $JOB_ID"

echo "Starting migration..."
MIG_RESP=$(curl -s -w "\n%{http_code}" -X POST "$API_BASE/api/jobs/$JOB_ID/migrate" \
  -H "Content-Type: application/json" \
  -d '{"migration_goal": "Analyse the MTA report and apply all migration changes"}')
HTTP_CODE=$(echo "$MIG_RESP" | tail -n1)
BODY=$(echo "$MIG_RESP" | sed '$d')
if [[ "$HTTP_CODE" != "202" ]]; then
  echo "Failed to start migration (HTTP $HTTP_CODE): $BODY"
  exit 1
fi
echo "Migration started (202)."

echo ""
echo "Job URL (open in browser): ${API_BASE%/}/ (or studio UI job $JOB_ID)"
echo "Polling job status every 15s (Ctrl+C to stop)..."
while true; do
  PAYLOAD=$(curl -s "$API_BASE/api/jobs/$JOB_ID")
  STATUS=$(echo "$PAYLOAD" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "error")
  PHASE=$(echo "$PAYLOAD" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current_phase',''))" 2>/dev/null)
  PROG=$(echo "$PAYLOAD" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress',0))" 2>/dev/null)
  echo "  $(date +%H:%M:%S)  status=$STATUS  phase=$PHASE  progress=$PROG"
  case "$STATUS" in
    completed) echo "Done."; exit 0 ;;
    failed)    echo "Job failed."; echo "$PAYLOAD" | python3 -m json.tool 2>/dev/null | head -40; exit 1 ;;
    cancelled) echo "Job cancelled."; exit 1 ;;
  esac
  sleep 15
done
