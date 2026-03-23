#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# trigger-and-validate.sh — End-to-End System Test
#
# Creates a job via the backend API, polls until complete, then calls the
# validator service to check the generated code.  Use this to verify the
# full pipeline:  Frontend → Backend → AI Agents → Validator.
#
# Usage:
#   ./scripts/trigger-and-validate.sh                         # defaults
#   ./scripts/trigger-and-validate.sh "Build a REST API"      # custom vision
#   BACKEND=http://ocp-route:8080 ./scripts/trigger-and-validate.sh
#
# Prerequisites:
#   podman compose up -d --build    (all 3 services running)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────
BACKEND="${BACKEND:-http://localhost:8080}"
VALIDATOR="${VALIDATOR:-http://localhost:8180}"
VISION="${1:-Build a simple TODO REST API with Flask, SQLAlchemy, and a React frontend}"
POLL_INTERVAL=15       # seconds between polls
MAX_WAIT=900           # 15 minutes max
WORKSPACE_PATH="/app/workspace"   # path inside containers

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

banner() { echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${BOLD}  $1${NC}"; echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }
ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
fail()   { echo -e "  ${RED}✗${NC} $1"; }
info()   { echo -e "  ${YELLOW}→${NC} $1"; }

# ── Step 1: Health Checks ────────────────────────────────────────────────
banner "Step 1: Service Health Checks"

for svc in "${BACKEND}/health:Backend" "${VALIDATOR}/healthz:Validator"; do
    url="${svc%%:*}"
    name="${svc##*:}"
    if curl -sf "$url" > /dev/null 2>&1; then
        ok "$name is healthy ($url)"
    else
        fail "$name is NOT reachable ($url)"
        echo -e "  ${RED}Make sure services are running: podman compose up -d --build${NC}"
        exit 1
    fi
done

# ── Step 2: Create Job ──────────────────────────────────────────────────
banner "Step 2: Create Job"
info "Vision: ${VISION}"

CREATE_RESPONSE=$(curl -s -w '\n%{http_code}' -X POST "${BACKEND}/api/jobs" \
    -H "Content-Type: application/json" \
    -d "{\"vision\": \"${VISION}\"}")

HTTP_CODE=$(echo "$CREATE_RESPONSE" | tail -1)
BODY=$(echo "$CREATE_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" -ne 201 ] && [ "$HTTP_CODE" -ne 200 ]; then
    fail "Job creation failed (HTTP $HTTP_CODE)"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    exit 1
fi

JOB_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
if [ -z "$JOB_ID" ]; then
    fail "Could not extract job_id from response"
    echo "$BODY"
    exit 1
fi

ok "Job created: ${BOLD}${JOB_ID}${NC}"
echo -e "  ${CYAN}Dashboard: http://localhost:3000${NC}"

# ── Step 3: Poll Until Completion ────────────────────────────────────────
banner "Step 3: Waiting for Job Completion"
info "Polling every ${POLL_INTERVAL}s (max ${MAX_WAIT}s)"

ELAPSED=0
LAST_PHASE=""
LAST_PROGRESS=""

while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    JOB_DATA=$(curl -sf "${BACKEND}/api/jobs/${JOB_ID}" 2>/dev/null || echo '{}')
    STATUS=$(echo "$JOB_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")

    # Show progress
    PROGRESS_DATA=$(curl -sf "${BACKEND}/api/jobs/${JOB_ID}/progress" 2>/dev/null || echo '{}')
    PHASE=$(echo "$PROGRESS_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current_phase',''))" 2>/dev/null || echo "")
    PCT=$(echo "$PROGRESS_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress',0))" 2>/dev/null || echo "0")

    if [ "$PHASE" != "$LAST_PHASE" ] || [ "$PCT" != "$LAST_PROGRESS" ]; then
        info "[${ELAPSED}s] Status: ${STATUS} | Phase: ${PHASE:-starting} | Progress: ${PCT}%"
        LAST_PHASE="$PHASE"
        LAST_PROGRESS="$PCT"
    fi

    case "$STATUS" in
        completed)
            ok "Job completed in ${ELAPSED}s"
            break
            ;;
        failed|cancelled|error)
            fail "Job ${STATUS} after ${ELAPSED}s"
            echo "$JOB_DATA" | python3 -m json.tool 2>/dev/null || echo "$JOB_DATA"
            # Still run validation on whatever was generated
            break
            ;;
    esac

    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
    fail "Timeout after ${MAX_WAIT}s (status: ${STATUS})"
fi

# ── Step 4: List Generated Files ─────────────────────────────────────────
banner "Step 4: Generated Files"

FILES_DATA=$(curl -sf "${BACKEND}/api/jobs/${JOB_ID}/files" 2>/dev/null || echo '{"files":[]}')
FILE_COUNT=$(echo "$FILES_DATA" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('files',[])))" 2>/dev/null || echo "0")
ok "Generated ${FILE_COUNT} files"

echo "$FILES_DATA" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for f in data.get('files', [])[:20]:
    name = f if isinstance(f, str) else f.get('path', f.get('name', str(f)))
    print(f'    {name}')
if len(data.get('files', [])) > 20:
    print(f'    ... and {len(data[\"files\"]) - 20} more')
" 2>/dev/null || true

# ── Step 5: Validate with Crew Code Validator ────────────────────────────
banner "Step 5: Code Validation (Crew Code Validator)"

JOB_WORKSPACE="${WORKSPACE_PATH}/job-${JOB_ID}"
info "Workspace: ${JOB_WORKSPACE}"

# Detect tech stack from the job
TECH_STACK=$(echo "$JOB_DATA" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tech_stack', d.get('vision', '')))
" 2>/dev/null || echo "$VISION")

CHECKS='["syntax", "imports", "package_structure", "entrypoint"]'
info "Running checks: ${CHECKS}"

VALIDATE_RESPONSE=$(curl -s -w '\n%{http_code}' -X POST "${VALIDATOR}/api/v1/validate" \
    -H "Content-Type: application/json" \
    -d "{
        \"workspace_path\": \"${JOB_WORKSPACE}\",
        \"checks\": ${CHECKS},
        \"tech_stack\": \"${TECH_STACK}\"
    }")

V_HTTP=$(echo "$VALIDATE_RESPONSE" | tail -1)
V_BODY=$(echo "$VALIDATE_RESPONSE" | sed '$d')

if [ "$V_HTTP" -ne 200 ]; then
    fail "Validator returned HTTP ${V_HTTP}"
    echo "$V_BODY"
    exit 1
fi

# ── Step 6: Validation Report ────────────────────────────────────────────
banner "Step 6: Validation Report"

echo "$V_BODY" | python3 -c "
import sys, json

data = json.load(sys.stdin)
langs = data.get('detected_languages', [])
checks = data.get('checks', {})

print(f'  Detected languages: {\", \".join(langs) or \"none\"}')
print()

all_pass = True
for name, result in checks.items():
    passed = result.get('pass', False)
    icon = '✓' if passed else '✗'
    color = '\033[0;32m' if passed else '\033[0;31m'
    nc = '\033[0m'
    print(f'  {color}{icon}{nc} {name}: {\"PASS\" if passed else \"FAIL\"}')

    if not passed:
        all_pass = False
        # Show details
        errors = result.get('errors', [])
        broken = result.get('broken_imports', [])
        missing_init = result.get('issues', result.get('missing_init', []))
        missing_wiring = result.get('missing_wiring', [])

        for e in errors[:5]:
            f = e.get('file', '')
            err = e.get('error', '')
            print(f'      {f}: {err}')
        for b in broken[:5]:
            print(f'      {b.get(\"file\",\"\")}: broken import \"{b.get(\"module\",\"\")}\" (line {b.get(\"line\",\"?\")})')
        for i in (missing_init if isinstance(missing_init, list) else [])[:5]:
            print(f'      missing: {i}')
        for w in missing_wiring[:5]:
            print(f'      missing wiring: {w}')

print()
if all_pass:
    print('  \033[0;32m━━━ ALL CHECKS PASSED ━━━\033[0m')
else:
    print('  \033[0;31m━━━ SOME CHECKS FAILED ━━━\033[0m')
" 2>/dev/null || echo "$V_BODY" | python3 -m json.tool 2>/dev/null || echo "$V_BODY"

# ── Step 7: Save Full Report ─────────────────────────────────────────────
REPORT_FILE="validation-report-${JOB_ID}.json"
echo "$V_BODY" | python3 -m json.tool > "$REPORT_FILE" 2>/dev/null || echo "$V_BODY" > "$REPORT_FILE"
info "Full report saved to: ${REPORT_FILE}"

echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Done! Open http://localhost:3000 to see the job in the UI${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
