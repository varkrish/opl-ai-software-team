#!/usr/bin/env bash
# Monitor a Crew Studio job: poll status and workspace file count.
# Usage: ./scripts/monitor-job.sh [job_id]
# Default job_id: 34ed1eb2-a62c-4495-93b5-a6201cff7eff

JOB_ID="${1:-34ed1eb2-a62c-4495-93b5-a6201cff7eff}"
BASE_URL="${CREW_STUDIO_URL:-http://localhost:8081}"
WS="${WORKSPACE_PATH:-$(pwd)/agent/workspace}/job-${JOB_ID}"

echo "Monitoring job $JOB_ID (Ctrl+C to stop)"
echo "---"

while true; do
  j=$(curl -s "$BASE_URL/api/jobs/$JOB_ID")
  jstatus=$(echo "$j" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null)
  jphase=$(echo "$j" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current_phase','?'))" 2>/dev/null)
  pct=$(echo "$j" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress',0))" 2>/dev/null)
  msg=$(echo "$j" | python3 -c "import sys,json; d=json.load(sys.stdin); m=d.get('last_message',[]); print(m[-1].get('message','') if m else '')" 2>/dev/null)
  err=$(echo "$j" | python3 -c "import sys,json; d=json.load(sys.stdin); e=d.get('error'); print((str(e)[:60]+'...') if e and len(str(e))>60 else (e or ''))" 2>/dev/null)
  nfiles=""
  [ -d "$WS" ] && nfiles=$(find "$WS" -type f 2>/dev/null | wc -l)
  echo "$(date +%H:%M:%S)  status=$jstatus  phase=$jphase  progress=$pct%  files=$nfiles  $msg ${err:+| $err}"
  [ "$jstatus" = "completed" ] || [ "$jstatus" = "failed" ] && break
  sleep 15
done

echo "---"
echo "Done. Workspace: $WS"
