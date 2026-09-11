#!/bin/bash
# Restart the dashboard server fully detached from whatever shell runs this
# (an SSH session, cron). Safe to run when nothing is running. Used by the
# deploy steps and by the crontab watchdog.
cd "$(dirname "$0")/.." || exit 1
pkill -f "[d]ashboard/server.py" 2>/dev/null
sleep 2
setsid nohup venv/bin/python dashboard/server.py >> logs/dashboard.log 2>&1 < /dev/null &
disown
sleep 6
if ss -tln 2>/dev/null | grep -q ':8085 '; then
  echo "dashboard listening on :8085"
else
  echo "dashboard did NOT come up -- see logs/dashboard.log"; exit 1
fi
