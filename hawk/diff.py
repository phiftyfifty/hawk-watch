"""Compare two data snapshots and report degradations."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Finding:
    rule: str
    subject: str
    message: str


def _parse_time(value):
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _check_probe_errors(today, findings):
    for name, check in today.get("checks", {}).items():
        if check.get("status") == "error":
            detail = check.get("error", "no detail given")
            findings.append(Finding(
                "probe_error", name,
                f"probe could not complete the {name} check: {detail}"))


def _errored(today, name):
    return today.get("checks", {}).get(name, {}).get("status") == "error"


def _skipped(today, name):
    return today.get("checks", {}).get(name, {}).get("status") == "skipped"


def _check_tables(today, baseline, rules, findings):
    if _errored(today, "tables"):
        return
    now = today["checks"]["tables"]["tables"]
    allowed_to_shrink = set(rules.get("tables_allowed_to_shrink", []))
    stale_by_table = rules.get("max_stale_days_by_table", {})
    captured = _parse_time(today.get("captured_at"))

    # Staleness is opt-in. Most of the schema is lookup tables seeded once and
    # never touched again, so a blanket allowance would fire on dozens of them
    # every single day and train the reader to ignore hawk.
    for table, allowance in sorted(stale_by_table.items()):
        entry = now.get(table)
        if entry is None:
            # A typo here would silently disable the watch forever, which is
            # the exact failure mode hawk exists to catch.
            findings.append(Finding(
                "stale_config_unknown_table", table,
                f"{table} is watched for staleness but is not in the database"))
            continue
        latest = _parse_time(entry.get("latest_timestamp"))
        if latest is None:
            findings.append(Finding(
                "stale_config_no_timestamp", table,
                f"{table} is watched for staleness but the probe found no "
                f"timestamp column on it"))
            continue
        if captured and latest:
            stale_days = (captured - latest).days
            if stale_days > allowance:
                findings.append(Finding(
                    "stale_marker", table,
                    f"{table} has had no new rows for {stale_days} days "
                    f"(allowance {allowance})"))

    if baseline is None or _errored(baseline, "tables"):
        return
    before = baseline["checks"]["tables"]["tables"]
    for table, prev in sorted(before.items()):
        if table not in now:
            findings.append(Finding(
                "table_missing", table,
                f"{table} was present yesterday and is absent today"))
            continue
        if table in allowed_to_shrink:
            continue
        prev_count, now_count = prev.get("count", 0), now[table].get("count", 0)
        if now_count < prev_count:
            findings.append(Finding(
                "count_drop", table,
                f"{table} dropped from {prev_count} to {now_count} rows"))


def _check_replication(today, rules, findings):
    if _errored(today, "replication") or _skipped(today, "replication"):
        return
    repl = today["checks"]["replication"]
    slot = repl.get("slot", "the watched slot")
    if repl.get("slot_active") is None:
        findings.append(Finding(
            "replication_unhealthy", "slot",
            f"replication slot {slot} does not exist"))
    elif repl.get("slot_active") is False:
        findings.append(Finding(
            "replication_unhealthy", "slot",
            f"replication slot {slot} is inactive"))
    walsender = repl.get("walsender_state")
    if walsender and walsender != "streaming":
        findings.append(Finding(
            "replication_unhealthy", "walsender",
            f"walsender state is {walsender}, expected streaming"))
    lag = repl.get("lag_bytes")
    threshold = rules.get("lag_bytes_warn", 10485760)
    if lag is not None and lag > threshold:
        findings.append(Finding(
            "replication_lag", "lag",
            f"replication lag {lag} bytes exceeds threshold {threshold}"))


def _check_endpoints(today, rules, findings):
    if _errored(today, "endpoints"):
        return
    seen = today["checks"]["endpoints"].get("endpoints", {})
    for name in rules.get("required_endpoints", []):
        if name not in seen:
            findings.append(Finding(
                "endpoint_missing", name,
                f"required endpoint {name} was not probed"))
    for name, entry in sorted(seen.items()):
        if not entry.get("ok"):
            detail = entry.get("detail") or f"HTTP {entry.get('http_status')}"
            findings.append(Finding(
                "endpoint_down", name, f"endpoint {name} failed: {detail}"))
            continue
        count = entry.get("count")
        if count is not None and count == 0:
            findings.append(Finding(
                "endpoint_empty", name,
                f"endpoint {name} answered but returned zero rows"))


def _check_seeds(today, baseline, findings):
    if baseline is None or _errored(today, "seeds") or _errored(baseline, "seeds"):
        return
    now = today["checks"]["seeds"].get("files", {})
    before = baseline["checks"]["seeds"].get("files", {})
    for name, prev in sorted(before.items()):
        if name not in now:
            findings.append(Finding(
                "seed_changed", name, f"seed file {name} has disappeared"))
            continue
        if now[name].get("sha256") != prev.get("sha256"):
            findings.append(Finding(
                "seed_changed", name,
                f"seed file {name} changed hash from "
                f"{prev.get('sha256')} to {now[name].get('sha256')}"))


def compare(today, baseline, rules):
    """Return the findings for today's snapshot against an optional baseline.

    A baseline of None means this is the first ever run: cross-day rules are
    skipped, but same-day rules such as probe errors still apply.
    """
    findings = []
    _check_probe_errors(today, findings)
    _check_tables(today, baseline, rules, findings)
    _check_replication(today, rules, findings)
    _check_endpoints(today, rules, findings)
    _check_seeds(today, baseline, findings)
    return findings
