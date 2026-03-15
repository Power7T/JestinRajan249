#!/bin/sh
# Entrypoint for the PostgreSQL streaming replica container.
#
# On first start (empty data dir): clones the primary using pg_basebackup,
# writes standby.signal, then starts PostgreSQL as a hot standby.
# On subsequent starts: just starts PostgreSQL (data dir already cloned).
#
# Required env vars:
#   REPL_PASSWORD              — password for the replication user
#   POSTGRES_PASSWORD          — used by healthcheck / app connections
#   PRIMARY_HOST (default: db) — hostname of the primary container

set -e

DATA_DIR="/var/lib/postgresql/data"
PRIMARY_HOST="${PRIMARY_HOST:-db}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPL_USER="${POSTGRES_REPLICATION_USER:-replicator}"

if [ -z "$REPL_PASSWORD" ]; then
  echo "ERROR: REPL_PASSWORD env var is required" >&2
  exit 1
fi

# First-time initialisation: clone primary
if [ ! -f "${DATA_DIR}/PG_VERSION" ]; then
  echo "==> First start: waiting for primary at ${PRIMARY_HOST}:${PRIMARY_PORT}..."
  until PGPASSWORD="${REPL_PASSWORD}" pg_isready -h "${PRIMARY_HOST}" -p "${PRIMARY_PORT}" -U "${REPL_USER}" -q; do
    sleep 2
  done

  echo "==> Cloning primary with pg_basebackup..."
  PGPASSWORD="${REPL_PASSWORD}" pg_basebackup \
    -h "${PRIMARY_HOST}" \
    -p "${PRIMARY_PORT}" \
    -U "${REPL_USER}" \
    -D "${DATA_DIR}" \
    -P -Xs -R \
    --checkpoint=fast \
    --no-password

  # pg_basebackup -R creates standby.signal and appends primary_conninfo to
  # postgresql.auto.conf — no extra config needed.
  chown -R postgres:postgres "${DATA_DIR}"
  chmod 700 "${DATA_DIR}"
  echo "==> Replica data directory initialised"
fi

# Start PostgreSQL as the postgres OS user
exec su-exec postgres postgres \
  -D "${DATA_DIR}" \
  -c hot_standby=on \
  -c max_standby_streaming_delay=30s \
  -c wal_receiver_status_interval=10s \
  -c hot_standby_feedback=on
