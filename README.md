# hawk

A daily data status watch for a Postgres box you can reach over SSH.

Once a day, hawk asks the server what its data looks like, compares the answer to yesterday's, and fails loudly when something has degraded.
I wrote it because data problems on a production box tend to be silent: a seeder that re-runs and resets a table, a replication slot that quietly goes inactive, an API that still answers but with zero rows.
Nobody notices for days, and then everybody notices at once.

## What it checks

- Row counts and latest-record timestamps for every table, so a drop or a stall shows up
- Logical replication health, publisher side: slot active, walsender streaming, lag under a threshold
- JSON endpoints you name, asserting they return real rows rather than merely answering
- SHA-256 hashes of seed files on disk, so a silently swapped seed shows up

Each check is optional except the table counts. Leave a setting unset and that check is skipped.

## How it works

The probe is one read-only bash script.
`hawk-fetch` pipes it over SSH into `bash -s` on the server, so nothing is installed or left behind there, and it prints one JSON snapshot.
`hawk-diff` validates the snapshot against `schema/snapshot.schema.json`, refuses anything that looks like a credential, and applies the rules in `config/rules.json`.
`hawk-run` ties the two together once a day, keeps each snapshot, and sends a desktop notification on a bad day.

The probe never reads an `.env` and never holds a password.
SSH runs in batch mode, so how you reach the server lives in `~/.ssh/config` where it belongs.
A tailnet hostname works as is, and a jump host is one `ProxyJump` line.
For an unattended run, give hawk its own key and pin it on the server with a forced command so it can't do anything else.

## Setup

```bash
git clone https://github.com/phiftyfifty/hawk-watch.git && cd hawk-watch
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
mkdir -p ~/.config/hawk && cp config/hawk.env.example ~/.config/hawk/env
```

Edit `~/.config/hawk/env` to point at your server, then try a run without saving anything:

```bash
bin/hawk-run --no-save
```

When that looks right, schedule it. On macOS `bin/hawk-schedule install` adds a LaunchAgent at 09:15, and on Linux a cron line does the same job.
Snapshots go to `~/.local/state/hawk/snapshots`; run `git init` in there and every day's snapshot gets committed, so trends are a plain `git log -p`.

To run it from CI instead, `examples/github-actions.yml` joins a tailnet with the Tailscale action and runs the same probe.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | healthy, no findings against the previous snapshot |
| 1 | the data degraded, findings are printed as a table |
| 2 | the snapshot couldn't be trusted (unreachable, schema invalid, or a possible secret) |

## Tuning a noisy check

Edit `config/rules.json`, not the differ.

- `tables_allowed_to_shrink` lists tables that lose rows in normal operation, like sessions or job queues
- `max_stale_days_by_table` is opt-in on purpose: most schemas are mostly lookup tables that never change, and a blanket rule would go red every day until you stopped reading it
- `required_endpoints` names the endpoints that have to be probed every run
- `lag_bytes_warn` is the replication lag threshold, 10 MiB by default

A filled-in version looks like this:

```json
{
  "lag_bytes_warn": 10485760,
  "tables_allowed_to_shrink": ["sessions", "job_queue", "schema_migrations"],
  "max_stale_days_by_table": {"orders": 7, "signups": 14},
  "required_endpoints": ["orders"]
}
```

A table named in `max_stale_days_by_table` that doesn't exist is itself a finding, so a typo can't switch a watch off.

## Tests

```bash
.venv/bin/python -m pytest
```

The probe has contract tests that stub `docker` and `curl`, so the whole suite runs on any machine without a database.

## License

MIT
