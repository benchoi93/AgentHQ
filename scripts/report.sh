#!/bin/bash
# Session → Commander callback script
# Usage: report.sh <status> <summary> [task_id]
#   status: completed | error | in_progress | info
#   summary: Brief description
#   task_id: Optional commander task ID
#
# Examples:
#   report.sh completed "Training done — accuracy 94.2%"
#   report.sh error "OOM at epoch 34"
#   report.sh in_progress "Epoch 50/100, loss=0.023"

STATUS="${1:-info}"
SUMMARY="${2:-No summary provided}"
TASK_ID="${3:-}"

# Auto-detect session ID from tmux pane title or env
if [ -n "$AGENTHQ_SESSION_ID" ]; then
    SESSION_ID="$AGENTHQ_SESSION_ID"
else
    # Extract from tmux pane name (format: agenthq-XXXXXXXXXXXX)
    PANE_TITLE=$(tmux display-message -p '#{session_name}' 2>/dev/null)
    SESSION_ID=$(echo "$PANE_TITLE" | sed -n 's/^agenthq-//p')
fi

if [ -z "$SESSION_ID" ]; then
    echo "ERROR: Cannot determine session ID. Set AGENTHQ_SESSION_ID or run inside a tmux session." >&2
    exit 1
fi

# AgentHQ server config
API_URL="${AGENTHQ_URL:-http://localhost:30001}"
TOKEN="${AGENTHQ_TOKEN:-VIqvhP7zgxaO51uFAtzxw4DBS_Cl5hYoCl0RFoUIICk}"

# Build JSON payload
if [ -n "$TASK_ID" ]; then
    PAYLOAD=$(printf '{"status":"%s","summary":"%s","task_id":"%s"}' "$STATUS" "$SUMMARY" "$TASK_ID")
else
    PAYLOAD=$(printf '{"status":"%s","summary":"%s"}' "$STATUS" "$SUMMARY")
fi

# Send callback
RESULT=$(curl -s -X POST \
    "${API_URL}/api/sessions/${SESSION_ID}/report" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" 2>&1)

echo "$RESULT"
