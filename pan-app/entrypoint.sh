#!/bin/sh
set -eu

Xvfb :99 -screen 0 1365x900x24 -ac +extension RANDR >/dev/null 2>&1 &
for attempt in $(seq 1 30); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
  sleep 0.2
done
x11vnc -display :99 -localhost -rfbport 5900 -forever -shared -nopw >/dev/null 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/dev/null 2>&1 &

exec gosu runner uvicorn backend.app:app \
  --app-dir /opt/veriidia \
  --host 0.0.0.0 \
  --port 8000 \
  --no-access-log
