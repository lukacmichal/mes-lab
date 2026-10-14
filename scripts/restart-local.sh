#!/bin/bash
# Restart the local MES + simulation (Git Bash), speed as the argument (default 10), e.g. scripts/restart-local.sh 10.
# Stops the old python and its PostgreSQL cleanly (a killed dev process leaves the database broken), starts again,
# and sets the speed (the database remembers the last one, so --speed alone would not change it).
SPEED=${1:-10}
cd /c/NAS/mes-lab
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'mes_lab\.dev' -and \$_.Name -match 'python' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" >/dev/null 2>&1
sleep 2
.venv/Lib/site-packages/pgserver/pginstall/bin/pg_ctl.exe -D var/pgdata stop -m fast >/dev/null 2>&1
sleep 1
(nohup .venv/Scripts/python.exe -m mes_lab.dev --speed $SPEED > var/dev.log 2>&1 &)
for i in $(seq 1 60); do
  if curl.exe --noproxy "*" -s http://127.0.0.1:8070/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
sleep 4
curl.exe --noproxy "*" -s -X POST -H "Content-Type: application/json" -d "{\"speed\": $SPEED}" http://127.0.0.1:8070/api/v1/sim/speed
echo
