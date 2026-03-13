#!/bin/sh
set -e

export POSTGRES_DB="${POSTGRES_DB:-video_checker}"
export POSTGRES_USER="${POSTGRES_USER:-video_checker}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-video_checker}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_HOST="127.0.0.1"
export PUID="${PUID:-1000}"
export PGID="${PGID:-1000}"

PGDATA_DIR="/config/postgres"
mkdir -p "$PGDATA_DIR"

PG_BIN_DIR="$(find /usr/lib/postgresql -maxdepth 3 -type f -name initdb 2>/dev/null | sed 's#/initdb$##' | sort | tail -n 1)"
if [ -z "$PG_BIN_DIR" ]; then
  echo "PostgreSQL binaries not found under /usr/lib/postgresql"
  exit 1
fi

INITDB="$PG_BIN_DIR/initdb"
PG_CTL="$PG_BIN_DIR/pg_ctl"

if getent group "$PGID" >/dev/null 2>&1; then
  RUNTIME_GROUP="$(getent group "$PGID" | cut -d: -f1)"
else
  RUNTIME_GROUP="appgroup"
  groupadd -g "$PGID" "$RUNTIME_GROUP"
fi

if getent passwd "$PUID" >/dev/null 2>&1; then
  RUNTIME_USER="$(getent passwd "$PUID" | cut -d: -f1)"
else
  RUNTIME_USER="appuser"
  useradd -m -o -K UID_MIN=99 -K UID_MAX=60000 -u "$PUID" -g "$PGID" -s /bin/sh "$RUNTIME_USER"
fi

chown -R "$PUID:$PGID" "$PGDATA_DIR" 2>/dev/null || true

run_as_runtime_user() {
  su -s /bin/sh -c "$1" "$RUNTIME_USER"
}

ensure_local_pg_hba() {
  PG_HBA_FILE="$PGDATA_DIR/pg_hba.conf"
  if [ ! -f "$PG_HBA_FILE" ]; then
    return
  fi

  TMP_HBA_FILE="$PGDATA_DIR/pg_hba.conf.tmp"
  {
    echo "local   all             all                                     trust"
    echo "host    all             all             127.0.0.1/32            trust"
    echo "host    all             all             ::1/128                 trust"
    grep -v "^local   all             all" "$PG_HBA_FILE" | grep -v "127.0.0.1/32" | grep -v "::1/128" || true
  } > "$TMP_HBA_FILE"
  mv "$TMP_HBA_FILE" "$PG_HBA_FILE"
}

reconcile_postgres_access() {
  run_as_runtime_user "psql -h 127.0.0.1 -p $POSTGRES_PORT -U '$POSTGRES_USER' -d postgres -tc \"SELECT 1 FROM pg_roles WHERE rolname = '$POSTGRES_USER';\" | grep -q 1 || psql -h 127.0.0.1 -p $POSTGRES_PORT -U '$POSTGRES_USER' -d postgres -c \"CREATE ROLE $POSTGRES_USER LOGIN;\""
  run_as_runtime_user "psql -h 127.0.0.1 -p $POSTGRES_PORT -U '$POSTGRES_USER' -d postgres -c \"ALTER ROLE $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';\""
  run_as_runtime_user "psql -h 127.0.0.1 -p $POSTGRES_PORT -U '$POSTGRES_USER' -d postgres -tc \"SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB';\" | grep -q 1 || psql -h 127.0.0.1 -p $POSTGRES_PORT -U '$POSTGRES_USER' -d postgres -c \"CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER;\""
}

PG_SERVER_OPTS="-c listen_addresses='127.0.0.1' -p $POSTGRES_PORT -c unix_socket_directories='/tmp'"

if [ ! -s "$PGDATA_DIR/PG_VERSION" ]; then
  run_as_runtime_user "$INITDB -D '$PGDATA_DIR' -U '$POSTGRES_USER' --auth-local=trust --auth-host=trust"
fi

ensure_local_pg_hba
run_as_runtime_user "$PG_CTL -D '$PGDATA_DIR' -o \"$PG_SERVER_OPTS\" -w start"
reconcile_postgres_access

exec su -s /bin/sh -c "uvicorn app.main:app --host 0.0.0.0 --port '${WEB_PORT:-8080}'" "$RUNTIME_USER"
