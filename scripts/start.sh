#!/bin/sh
set -eu
umask 077
frp_pid=""
app_pid=""
stop() {
  if [ -n "$app_pid" ]; then kill -TERM "$app_pid" 2>/dev/null || true; wait "$app_pid" || true; fi
  if [ -n "$frp_pid" ]; then kill -TERM "$frp_pid" 2>/dev/null || true; wait "$frp_pid" || true; fi
}
trap stop TERM INT EXIT
if [ -n "${HP_SHARED_KEY:-}" ]; then
  python /scripts/configure_frp.py
  frpc -c /tmp/frpc.toml &
  frp_pid=$!
fi
python -m ex_app.lib.main &
app_pid=$!
wait "$app_pid"
