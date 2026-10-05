#!/bin/sh
set -eu
: "${APP_PERSISTENT_STORAGE:?APP_PERSISTENT_STORAGE is required}"
mkdir -p "$APP_PERSISTENT_STORAGE"
if [ "$(id -u)" = 0 ]; then
  chown -R events:events "$APP_PERSISTENT_STORAGE"
  # HaRP client.key may be a root-owned 0600 bind mount. Copy required client material
  # into a private tmp directory without changing the host's certificates.
  if [ -d /certs/frp ]; then
    mkdir -p /tmp/events-frp-certs
    cp /certs/frp/client.crt /certs/frp/client.key /certs/frp/ca.crt /tmp/events-frp-certs/
    chmod 700 /tmp/events-frp-certs
    chmod 600 /tmp/events-frp-certs/*
    chown -R events:events /tmp/events-frp-certs
    export HP_CERT_DIR=/tmp/events-frp-certs
  fi
  exec gosu events /scripts/start.sh
fi
exec /scripts/start.sh
