#!/bin/bash
# hawk-probe.sh — read-only daily data status probe for a Postgres host.
#
# Prints one JSON snapshot to stdout and exits 0. It never writes to the
# database and never needs a credential beyond what `docker exec` or a local
# `psql` already has. Any check that cannot complete reports "status":"error"
# inside the JSON rather than aborting, so the differ can tell bad data from a
# blind probe. A check with nothing configured reports "status":"skipped".
#
# Env (all optional):
#   HAWK_PG_CONTAINER   docker container running Postgres; empty runs psql directly
#   HAWK_PG_DB          database name, default postgres
#   HAWK_PG_USER        database user, default postgres
#   HAWK_PG_SCHEMA      schema whose tables are counted, default public
#   HAWK_SLOT           logical replication slot to watch; empty skips the check
#   HAWK_ENDPOINTS      space-separated name=url pairs; each must return JSON
#   HAWK_COUNT_FIELD    JSON field holding the row count, default count
#   HAWK_SEED_DIR       directory of seed files to hash; empty skips the check

set -o pipefail

HAWK_PG_CONTAINER="${HAWK_PG_CONTAINER-postgres}"
HAWK_PG_DB="${HAWK_PG_DB:-postgres}"
HAWK_PG_USER="${HAWK_PG_USER:-postgres}"
HAWK_PG_SCHEMA="${HAWK_PG_SCHEMA:-public}"
HAWK_SLOT="${HAWK_SLOT:-}"
HAWK_ENDPOINTS="${HAWK_ENDPOINTS:-}"
HAWK_COUNT_FIELD="${HAWK_COUNT_FIELD:-count}"
HAWK_SEED_DIR="${HAWK_SEED_DIR:-}"
SCHEMA_VERSION=1
PROBE_VERSION="1.0.0"

CAPTURED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOSTNAME_VALUE=$(hostname 2>/dev/null || echo unknown)

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))' 2>/dev/null \
    || printf '"unavailable"'
}

# psql writes NOTICE and WARNING lines to stderr, and a database carrying a
# collation mismatch emits one on every single query. Merging stderr into
# stdout would turn those lines into fake table names and junk row counts, so
# they are kept apart and only read when a query actually fails.
PSQL_ERR="${TMPDIR:-/tmp}/hawk-psql-err.$$"
: > "$PSQL_ERR" 2>/dev/null
trap 'rm -f "$PSQL_ERR"' EXIT

psql_run() {
  if [ -n "$HAWK_PG_CONTAINER" ]; then
    docker exec "$HAWK_PG_CONTAINER" psql -U "$HAWK_PG_USER" -d "$HAWK_PG_DB" -At -F'|' \
      -v ON_ERROR_STOP=1 -c "$1" 2>"$PSQL_ERR"
  else
    psql -U "$HAWK_PG_USER" -d "$HAWK_PG_DB" -At -F'|' \
      -v ON_ERROR_STOP=1 -c "$1" 2>"$PSQL_ERR"
  fi
}

psql_error() {
  head -c 400 "$PSQL_ERR" 2>/dev/null
}

# ---------------------------------------------------------------- tables
tables_json() {
  local list rc
  list=$(psql_run "
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='${HAWK_PG_SCHEMA}' AND table_type='BASE TABLE'
    ORDER BY table_name;")
  rc=$?
  if [ $rc -ne 0 ] || [ -z "$list" ]; then
    printf '{"status":"error","error":%s,"tables":{}}' \
      "$(printf '%s' "$(psql_error)
could not list tables" | json_escape)"
    return
  fi

  local entries="" table row count max_id latest ts_col
  while IFS= read -r table; do
    [ -z "$table" ] && continue
    # Modification columns are preferred over creation columns, since a row
    # edited today still proves activity.
    ts_col=$(psql_run "
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='${HAWK_PG_SCHEMA}' AND table_name='${table}'
        AND column_name IN ('updated_at','last_updated','modified_at',
                            'created_at','creation_date','created_date','inserted_at')
      ORDER BY CASE column_name
        WHEN 'updated_at' THEN 1 WHEN 'last_updated' THEN 2 WHEN 'modified_at' THEN 3
        WHEN 'created_at' THEN 4 WHEN 'creation_date' THEN 5 WHEN 'created_date' THEN 6
        ELSE 7 END
      LIMIT 1;")
    if [ -n "$ts_col" ]; then
      row=$(psql_run "SELECT count(*), COALESCE(max(id)::text,'NULL'),
                        COALESCE(max(${ts_col})::text,'NULL') FROM ${HAWK_PG_SCHEMA}.\"${table}\";")
    else
      row=$(psql_run "SELECT count(*), COALESCE(max(id)::text,'NULL'), 'NULL'
                        FROM ${HAWK_PG_SCHEMA}.\"${table}\";")
    fi
    count=$(printf '%s' "$row" | cut -d'|' -f1)
    # Join tables have no `id`, so max(id) errors and takes the row count down
    # with it. Fall back to counting alone rather than dropping the table.
    case "$count" in
      ''|*[!0-9]*)
        if [ -n "$ts_col" ]; then
          row=$(psql_run "SELECT count(*), 'NULL',
                            COALESCE(max(${ts_col})::text,'NULL') FROM ${HAWK_PG_SCHEMA}.\"${table}\";")
        else
          row=$(psql_run "SELECT count(*), 'NULL', 'NULL' FROM ${HAWK_PG_SCHEMA}.\"${table}\";")
        fi
        count=$(printf '%s' "$row" | cut -d'|' -f1)
        ;;
    esac
    max_id=$(printf '%s' "$row" | cut -d'|' -f2)
    latest=$(printf '%s' "$row" | cut -d'|' -f3)
    case "$count" in ''|*[!0-9]*) continue;; esac
    case "$max_id" in ''|*[!0-9]*) max_id="null";; esac
    if [ "$latest" = "NULL" ] || [ -z "$latest" ]; then
      latest="null"
    else
      latest="\"$(printf '%s' "$latest" | sed 's/ /T/; s/+00$/Z/')\""
    fi
    entries="${entries},\"${table}\":{\"count\":${count},\"max_id\":${max_id},\"latest_timestamp\":${latest}}"
  done <<< "$list"

  printf '{"status":"ok","tables":{%s}}' "${entries#,}"
}

# ----------------------------------------------------------- replication
replication_json() {
  if [ -z "$HAWK_SLOT" ]; then
    printf '{"status":"skipped"}'
    return
  fi
  local out slot_active lag walsender
  out=$(psql_run "
    SELECT
      COALESCE((SELECT active::text FROM pg_replication_slots WHERE slot_name='${HAWK_SLOT}'), 'missing'),
      COALESCE((SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::text
                FROM pg_replication_slots WHERE slot_name='${HAWK_SLOT}'), 'NULL'),
      COALESCE((SELECT state FROM pg_stat_replication WHERE application_name='${HAWK_SLOT}' LIMIT 1), 'no_walsender');")
  if [ $? -ne 0 ] || [ -z "$out" ]; then
    printf '{"status":"error","error":%s}' \
      "$(printf '%s' "$(psql_error)
could not query replication" | json_escape)"
    return
  fi
  slot_active=$(printf '%s' "$out" | cut -d'|' -f1)
  lag=$(printf '%s' "$out" | cut -d'|' -f2)
  walsender=$(printf '%s' "$out" | cut -d'|' -f3)

  case "$slot_active" in
    t) slot_active=true;;
    f) slot_active=false;;
    *) slot_active=null;;
  esac
  case "$lag" in ''|*[!0-9]*) lag=null;; esac

  printf '{"status":"ok","slot":"%s","slot_active":%s,"lag_bytes":%s,"walsender_state":"%s"}' \
    "$HAWK_SLOT" "$slot_active" "$lag" "$walsender"
}

# ------------------------------------------------------------- endpoints
endpoints_json() {
  if [ -z "$HAWK_ENDPOINTS" ]; then
    printf '{"status":"skipped","endpoints":{}}'
    return
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf '{"status":"error","error":"curl is not installed on this host","endpoints":{}}'
    return
  fi

  local entries="" pair name url body count
  for pair in $HAWK_ENDPOINTS; do
    name="${pair%%=*}"
    url="${pair#*=}"
    body=$(curl -sf --max-time 20 "$url" 2>/dev/null)
    if [ $? -ne 0 ]; then
      entries="${entries},\"${name}\":{\"ok\":false,\"http_status\":null,\"count\":null,\"detail\":\"request failed\"}"
      continue
    fi
    count=$(printf '%s' "$body" | HAWK_COUNT_FIELD="$HAWK_COUNT_FIELD" python3 -c \
'import json,os,sys
try:
    print(int(json.load(sys.stdin).get(os.environ["HAWK_COUNT_FIELD"], -1)))
except Exception:
    print(-1)' 2>/dev/null)
    if [ "$count" = "-1" ] || [ -z "$count" ]; then
      entries="${entries},\"${name}\":{\"ok\":false,\"http_status\":200,\"count\":null,\"detail\":\"response had no ${HAWK_COUNT_FIELD} field\"}"
    else
      entries="${entries},\"${name}\":{\"ok\":true,\"http_status\":200,\"count\":${count},\"detail\":null}"
    fi
  done

  printf '{"status":"ok","endpoints":{%s}}' "${entries#,}"
}

# ----------------------------------------------------------------- seeds
seeds_json() {
  if [ -z "$HAWK_SEED_DIR" ]; then
    printf '{"status":"skipped","files":{}}'
    return
  fi
  if [ ! -d "$HAWK_SEED_DIR" ]; then
    printf '{"status":"error","error":"seed dir not found","files":{}}'
    return
  fi
  local entries="" file base hash size
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    base=$(basename "$file")
    hash=$( (sha256sum "$file" 2>/dev/null || shasum -a 256 "$file" 2>/dev/null) | cut -d' ' -f1)
    [ -z "$hash" ] && continue
    size=$(wc -c < "$file" 2>/dev/null | tr -d ' ')
    case "$size" in ''|*[!0-9]*) size=null;; esac
    entries="${entries},\"${base}\":{\"sha256\":\"${hash}\",\"bytes\":${size}}"
  done <<< "$(find "$HAWK_SEED_DIR" -maxdepth 1 -type f 2>/dev/null | sort)"

  printf '{"status":"ok","files":{%s}}' "${entries#,}"
}

printf '{"schema_version":%s,"captured_at":"%s","host":"%s","probe_version":"%s","checks":{"tables":%s,"replication":%s,"endpoints":%s,"seeds":%s}}\n' \
  "$SCHEMA_VERSION" "$CAPTURED_AT" "$HOSTNAME_VALUE" "$PROBE_VERSION" \
  "$(tables_json)" "$(replication_json)" "$(endpoints_json)" "$(seeds_json)"

exit 0
