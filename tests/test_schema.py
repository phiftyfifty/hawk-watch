import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "snapshot.schema.json"


def load_schema():
    return json.loads(SCHEMA_PATH.read_text())


def minimal_snapshot():
    return {
        "schema_version": 1,
        "captured_at": "2026-08-24T09:00:00Z",
        "host": "db-prod",
        "checks": {
            "tables": {"status": "ok", "tables": {}},
            "replication": {"status": "ok", "slot_active": True,
                            "lag_bytes": 0, "walsender_state": "streaming"},
            "endpoints": {"status": "ok", "endpoints": {}},
            "seeds": {"status": "ok", "files": {}},
        },
    }


def test_minimal_snapshot_is_valid():
    jsonschema.validate(minimal_snapshot(), load_schema())


def test_snapshot_missing_schema_version_is_invalid():
    snap = minimal_snapshot()
    del snap["schema_version"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(snap, load_schema())


def test_check_status_must_be_known_value():
    snap = minimal_snapshot()
    snap["checks"]["tables"]["status"] = "probably_fine"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(snap, load_schema())


def test_table_entry_accepts_count_and_markers():
    snap = minimal_snapshot()
    snap["checks"]["tables"]["tables"] = {
        "orders": {"count": 412, "max_id": 412, "latest_timestamp": "2026-08-23T18:04:11Z"}
    }
    jsonschema.validate(snap, load_schema())
