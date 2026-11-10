#!/bin/sh
# Hosted disks (Render, Fly, ...) are often mounted root-owned. Fix ownership of the data
# dir, then drop to the unprivileged "app" user before starting the server.
set -e
DATA="${TIMETABLE_DATA_DIR:-/data}"
mkdir -p "$DATA"
if [ "$(id -u)" = "0" ]; then
  chown -R app:app "$DATA"
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
