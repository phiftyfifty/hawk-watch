from hawk.diff import Finding
from hawk.report import render_summary

SNAP = {"captured_at": "2026-08-24T09:00:00Z", "host": "db-prod"}


def test_clean_report_says_healthy():
    text = render_summary(SNAP, [])
    assert "healthy" in text.lower()
    assert "db-prod" in text


def test_report_lists_every_finding_in_a_table():
    findings = [
        Finding("count_drop", "orders", "orders dropped from 412 to 400 rows"),
        Finding("endpoint_empty", "customers", "endpoint customers answered but returned zero rows"),
    ]
    text = render_summary(SNAP, findings)
    assert "count_drop" in text
    assert "orders dropped from 412 to 400 rows" in text
    assert "endpoint_empty" in text
    assert text.count("\n|") >= 3


def test_report_escapes_pipes_so_the_table_survives():
    findings = [Finding("probe_error", "tables", "psql said: a | b")]
    text = render_summary(SNAP, findings)
    assert r"a \| b" in text
