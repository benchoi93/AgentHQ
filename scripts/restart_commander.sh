#!/bin/bash
# Daily commander restart script
TOKEN="VIqvhP7zgxaO51uFAtzxw4DBS_Cl5hYoCl0RFoUIICk"
API="http://localhost:30001/api"
LOG="/home/chois/gitsrcs/AgentHQ/logs/commander_restart.log"

COMMANDER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/sessions" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(s['id']) for s in d if s.get('project')=='commander']" 2>/dev/null | head -1)

if [ -z "$COMMANDER_ID" ]; then
  echo "$(date -Iseconds) ERROR: Commander session not found" >> "$LOG"
  exit 1
fi

RESULT=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" "$API/sessions/$COMMANDER_ID/restart")
echo "$(date -Iseconds) Restarted commander ($COMMANDER_ID): $RESULT" >> "$LOG"
