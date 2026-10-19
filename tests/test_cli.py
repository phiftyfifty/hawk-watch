import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
CLI = REPO / "bin" / "hawk-diff"


def snapshot(count=412):
    return {
        "schema_version": 1,
        "captured_at": "2026-08-24T09:00:00Z",
        "host": "db-prod",
        "checks": {
            "tables": {"status": "ok", "tables": {
                "orders": {"count": count, "max_id": count,
                            "latest_timestamp": "2026-08-23T18:04:11Z"}}},
            "replication": {"status": "ok", "slot_active": True, "lag_bytes": 1024,
                            "walsender_state": "streaming"},
            "endpoints": {"status": "ok", "endpoints": {
                "orders_api": {"ok": True, "http_status": 200, "count": count},
                "customers": {"ok": True, "http_status": 200, "count": 88},
                "products": {"ok": True, "http_status": 200, "count": 31}}},
            "seeds": {"status": "ok", "files": {}},
        },
    }


TEST_RULES = {
    "lag_bytes_warn": 10485760,
    "tables_allowed_to_shrink": [],
    "max_stale_days_by_table": {"orders": 14},
    "required_endpoints": ["orders_api", "customers", "products"],
}


def run(tmp_path, today, baseline, extra=()):
    today_path = tmp_path / "today.json"
    today_path.write_text(json.dumps(today))
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(TEST_RULES))
    args = [sys.executable, str(CLI), "--today", str(today_path),
            "--rules", str(rules_path)]
    if baseline is not None:
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))
        args += ["--baseline", str(baseline_path)]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO)


def test_clean_run_exits_zero(tmp_path):
    result = run(tmp_path, snapshot(), snapshot())
    assert result.returncode == 0, result.stderr
    assert "healthy" in result.stdout.lower()


def test_degraded_run_exits_one(tmp_path):
    result = run(tmp_path, snapshot(count=400), snapshot())
    assert result.returncode == 1
    assert "count_drop" in result.stdout


def test_no_baseline_exits_zero(tmp_path):
    result = run(tmp_path, snapshot(), None)
    assert result.returncode == 0, result.stderr


def test_invalid_snapshot_exits_two(tmp_path):
    broken = snapshot()
    del broken["schema_version"]
    result = run(tmp_path, broken, None)
    assert result.returncode == 2
    assert "schema" in (result.stdout + result.stderr).lower()


def test_snapshot_with_a_secret_exits_two(tmp_path):
    leaky = snapshot()
    leaky["checks"]["seeds"]["files"]["DB_PASSWORD"] = {"sha256": "x"}
    result = run(tmp_path, leaky, None)
    assert result.returncode == 2
    assert "secret" in (result.stdout + result.stderr).lower()


def test_summary_file_is_written_when_requested(tmp_path):
    out = tmp_path / "summary.md"
    result = run(tmp_path, snapshot(count=400), snapshot(), extra=["--summary-file", str(out)])
    assert result.returncode == 1
    assert "count_drop" in out.read_text()
