#!/bin/bash
# Runs inside the PRIMARY PostgreSQL container on first initialization.
# Creates a replication user and allows standby connections.
set -e

REPL_USER="${POSTGRES_REPLICATION_USER:-replicator}"
REPL_PASS="${POSTGRES_REPLICATION_PASSWORD:?POSTGRES_REPLICATION_PASSWORD must be set}"

echo "==> Creating replication user: $REPL_USER"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${REPL_USER}') THEN
      CREATE ROLE ${REPL_USER} WITH REPLICATION LOGIN PASSWORD '${REPL_PASS}';
    END IF;
  END \$\$;
EOSQL

# Allow the replication user to connect for streaming replication.
# pg_hba.conf lives inside the PGDATA volume — append our rule.
echo "host  replication  ${REPL_USER}  0.0.0.0/0  scram-sha-256" >> "${PGDATA}/pg_hba.conf"
echo "==> Replication user ready; pg_hba.conf updated"
