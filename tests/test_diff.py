import copy

from hawk.diff import Finding, compare

RULES = {
    "lag_bytes_warn": 10485760,
    "tables_allowed_to_shrink": [],
    "max_stale_days_by_table": {"orders": 14},
    "required_endpoints": ["orders_api", "customers", "products"],
}


def snapshot(**overrides):
    snap = {
        "schema_version": 1,
        "captured_at": "2026-08-24T09:00:00Z",
        "host": "db-prod",
        "checks": {
            "tables": {
                "status": "ok",
                "tables": {
                    "orders": {"count": 412, "max_id": 412,
                                "latest_timestamp": "2026-08-23T18:04:11Z"},
                    "customer": {"count": 88, "max_id": 88,
                               "latest_timestamp": "2026-08-20T10:00:00Z"},
                },
            },
            "replication": {"status": "ok", "slot_active": True, "lag_bytes": 1024,
                            "walsender_state": "streaming"},
            "endpoints": {
                "status": "ok",
                "endpoints": {
                    "orders_api": {"ok": True, "http_status": 200, "count": 412},
                    "customers": {"ok": True, "http_status": 200, "count": 88},
                    "products": {"ok": True, "http_status": 200, "count": 31},
                },
            },
            "seeds": {"status": "ok",
                      "files": {"products.json": {"sha256": "abc123", "bytes": 5000}}},
        },
    }
    snap["checks"].update(overrides)
    return snap


def test_identical_snapshots_produce_no_findings():
    findings = compare(snapshot(), snapshot(), RULES)
    assert findings == []


def test_dropped_row_count_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["orders"]["count"] = 400
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "count_drop" and "orders" in f.subject for f in findings)


def test_growth_is_not_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["orders"]["count"] = 450
    assert compare(today, snapshot(), RULES) == []


def test_allowed_table_may_shrink():
    rules = dict(RULES, tables_allowed_to_shrink=["orders"])
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["orders"]["count"] = 400
    assert compare(today, snapshot(), rules) == []


def test_vanished_table_is_a_finding():
    today = copy.deepcopy(snapshot())
    del today["checks"]["tables"]["tables"]["customer"]
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "table_missing" and "customer" in f.subject for f in findings)


def test_new_table_is_not_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["invoice"] = {"count": 5, "max_id": 5,
                                                  "latest_timestamp": "2026-08-24T08:00:00Z"}
    assert compare(today, snapshot(), RULES) == []


def test_stale_marker_beyond_allowance_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["captured_at"] = "2026-09-30T09:00:00Z"
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "stale_marker" and f.subject == "orders" for f in findings)


def test_typo_in_a_watched_table_name_is_surfaced_not_ignored():
    rules = dict(RULES, max_stale_days_by_table={"product": 21})
    findings = compare(snapshot(), snapshot(), rules)
    assert any(f.rule == "stale_config_unknown_table"
               and f.subject == "product" for f in findings)


def test_watched_table_without_a_timestamp_column_is_surfaced():
    rules = dict(RULES, max_stale_days_by_table={"invoice": 21})
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["invoice"] = {
        "count": 10, "max_id": 10, "latest_timestamp": None}
    findings = compare(today, snapshot(), rules)
    assert any(f.rule == "stale_config_no_timestamp" and f.subject == "invoice"
               for f in findings)


def test_staleness_is_not_checked_for_tables_without_an_allowance():
    # customer is deliberately absent from max_stale_days_by_table. A typical schema
    # is mostly lookup tables seeded once, and a blanket allowance would bury
    # every run in stale_marker findings.
    today = copy.deepcopy(snapshot())
    today["captured_at"] = "2031-01-01T09:00:00Z"
    findings = compare(today, snapshot(), RULES)
    assert not any(f.subject == "customer" for f in findings)


def test_first_run_over_a_schema_of_untouched_lookup_tables_is_quiet():
    lookups = {name: {"count": 8, "max_id": 8,
                      "latest_timestamp": "2021-03-01T00:00:00Z"}
               for name in ("order_status", "order_type", "role", "permission")}
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"] = lookups
    rules = dict(RULES, max_stale_days_by_table={})
    assert compare(today, None, rules) == []


def test_stale_marker_within_allowance_is_not_a_finding():
    today = copy.deepcopy(snapshot())
    today["captured_at"] = "2026-08-25T09:00:00Z"
    assert compare(today, snapshot(), RULES) == []


def test_unhealthy_replication_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"]["slot_active"] = False
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "replication_unhealthy" for f in findings)


def test_replication_lag_beyond_threshold_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"]["lag_bytes"] = 99999999
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "replication_lag" for f in findings)


def test_missing_walsender_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"]["walsender_state"] = "no_walsender"
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "replication_unhealthy" for f in findings)


def test_dead_endpoint_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["endpoints"]["endpoints"]["orders_api"] = {
        "ok": False, "http_status": 502, "count": None, "detail": "bad gateway"}
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "endpoint_down" and "orders_api" in f.subject for f in findings)


def test_endpoint_returning_zero_rows_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["endpoints"]["endpoints"]["orders_api"]["count"] = 0
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "endpoint_empty" and "orders_api" in f.subject for f in findings)


def test_required_endpoint_absent_from_snapshot_is_a_finding():
    today = copy.deepcopy(snapshot())
    del today["checks"]["endpoints"]["endpoints"]["customers"]
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "endpoint_missing" and "customers" in f.subject for f in findings)


def test_changed_seed_hash_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["seeds"]["files"]["products.json"]["sha256"] = "def456"
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "seed_changed" for f in findings)


def test_probe_error_status_is_a_finding_distinct_from_bad_data():
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"] = {"status": "error", "error": "container not found", "tables": {}}
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "probe_error" for f in findings)
    assert not any(f.rule == "count_drop" for f in findings)


def test_first_ever_run_with_no_baseline_produces_no_findings():
    assert compare(snapshot(), None, RULES) == []


def test_first_ever_run_still_reports_probe_errors():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"] = {"status": "error", "error": "psql unreachable"}
    findings = compare(today, None, RULES)
    assert any(f.rule == "probe_error" for f in findings)


def test_findings_carry_human_readable_messages():
    today = copy.deepcopy(snapshot())
    today["checks"]["tables"]["tables"]["orders"]["count"] = 400
    finding = compare(today, snapshot(), RULES)[0]
    assert isinstance(finding, Finding)
    assert "412" in finding.message and "400" in finding.message


def test_skipped_replication_is_not_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"] = {"status": "skipped"}
    assert compare(today, snapshot(), RULES) == []


def test_missing_replication_slot_is_a_finding():
    today = copy.deepcopy(snapshot())
    today["checks"]["replication"]["slot_active"] = None
    findings = compare(today, snapshot(), RULES)
    assert any(f.rule == "replication_unhealthy" and "does not exist" in f.message
               for f in findings)
