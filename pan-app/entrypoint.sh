#!/bin/sh
set -eu

PORT="${PORT:-10000}"
case "$PORT" in
  ''|*[!0-9]*) echo "PORT must be a numeric TCP port." >&2; exit 1 ;;
esac
export PORT
envsubst '${PORT}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

Xvfb :99 -screen 0 1365x900x24 -ac +extension RANDR >/dev/null 2>&1 &
for attempt in $(seq 1 30); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
  sleep 0.2
done
x11vnc -display :99 -localhost -rfbport 5900 -forever -shared -nopw >/dev/null 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/dev/null 2>&1 &
nginx

exec gosu runner uvicorn backend.app:app \
  --app-dir /opt/veriidia \
  --host 0.0.0.0 \
  --port 8000 \
  --no-access-log
