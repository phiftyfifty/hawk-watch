import json
import os
import stat
import subprocess
from pathlib import Path

import jsonschema

REPO = Path(__file__).parent.parent
PROBE = REPO / "probe" / "hawk-probe.sh"
SCHEMA = json.loads((REPO / "schema" / "snapshot.schema.json").read_text())

DOCKER_STUB = r"""#!/bin/bash
# Stub docker. Understands the psql shapes hawk-probe.sh uses.
if [ "$1" = "exec" ]; then
  for arg in "$@"; do
    case "$arg" in
      *pg_replication_slots*) echo "t|1024|streaming"; exit 0;;
      *information_schema.columns*) echo "updated_at"; exit 0;;
      *information_schema.tables*) printf 'orders\ncustomer\n'; exit 0;;
      *"count(*), 'NULL'"*) echo "412||2026-08-23 18:04:11+00"; exit 0;;
      *"max(id)"*) echo "ERROR:  column \"id\" does not exist" >&2; exit 1;;
    esac
  done
  exit 0
fi
exit 0
"""

CURL_STUB = r"""#!/bin/bash
echo '{"data":[{"id":1}],"count":412}'
"""


def stub(dir_, name, body):
    path = dir_ / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def run_probe(tmp_path, **extra):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub(stub_dir, "docker", DOCKER_STUB)
    stub(stub_dir, "curl", CURL_STUB)
    (tmp_path / "seed").mkdir(exist_ok=True)
    (tmp_path / "seed" / "products.json").write_text('{"a":1}')
    env = dict(os.environ, PATH=f"{stub_dir}:{os.environ['PATH']}",
               HAWK_SEED_DIR=str(tmp_path / "seed"), HAWK_SLOT="app_sub",
               HAWK_ENDPOINTS="orders=http://localhost:8000/api/orders")
    env.update(extra)
    return subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env)


def test_probe_emits_schema_valid_json(tmp_path):
    result = run_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    jsonschema.validate(json.loads(result.stdout), SCHEMA)


def test_probe_reports_tables_it_found(tmp_path):
    snapshot = json.loads(run_probe(tmp_path).stdout)
    assert snapshot["checks"]["tables"]["tables"]["orders"]["count"] == 412


def test_probe_reads_the_count_from_each_endpoint(tmp_path):
    snapshot = json.loads(run_probe(tmp_path).stdout)
    assert snapshot["checks"]["endpoints"]["endpoints"]["orders"] == {
        "ok": True, "http_status": 200, "count": 412, "detail": None}


def test_probe_names_the_watched_slot(tmp_path):
    snapshot = json.loads(run_probe(tmp_path).stdout)
    repl = snapshot["checks"]["replication"]
    assert repl["slot"] == "app_sub" and repl["slot_active"] is True


def test_probe_hashes_seed_files(tmp_path):
    files = json.loads(run_probe(tmp_path).stdout)["checks"]["seeds"]["files"]
    assert len(files["products.json"]["sha256"]) == 64


def test_unconfigured_checks_are_skipped(tmp_path):
    result = run_probe(tmp_path, HAWK_SLOT="", HAWK_ENDPOINTS="", HAWK_SEED_DIR="")
    snapshot = json.loads(result.stdout)
    jsonschema.validate(snapshot, SCHEMA)
    for name in ("replication", "endpoints", "seeds"):
        assert snapshot["checks"][name]["status"] == "skipped"


def test_probe_emits_no_secrets(tmp_path):
    import sys
    sys.path.insert(0, str(REPO))
    from hawk.redact import assert_no_secrets
    assert_no_secrets(json.loads(run_probe(tmp_path).stdout))


def test_table_without_an_id_column_is_still_counted(tmp_path):
    # The stub errors on any max(id) query, standing in for a join table.
    entry = json.loads(run_probe(tmp_path).stdout)["checks"]["tables"]["tables"]["orders"]
    assert entry["count"] == 412
    assert entry["max_id"] is None


def test_probe_reports_error_status_when_docker_is_missing(tmp_path):
    # An empty PATH dir hides docker while /bin and /usr/bin keep the shell
    # and its coreutils reachable.
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ, PATH=f"{empty}:/bin:/usr/bin", HAWK_SEED_DIR=str(tmp_path))
    result = subprocess.run(["/bin/bash", str(PROBE)],
                            capture_output=True, text=True, env=env)
    snapshot = json.loads(result.stdout)
    jsonschema.validate(snapshot, SCHEMA)
    assert snapshot["checks"]["tables"]["status"] == "error"
