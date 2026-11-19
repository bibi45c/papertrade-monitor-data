# Papertrade market data archive

Private archive for the public read-only [Papertrade monitor](https://papertrade-monitor.papertrade-monitor.workers.dev/). Code is kept separately in [papertrade-monitor](https://github.com/bibi45c/papertrade-monitor).

D1 is a short-term cache: it keeps every minute for 14 days and only the first row of each UTC hour up to 90 days (it hit the free 500 MB cap on 2026-10-27). A server pulls full stored snapshots every 15 minutes into a clone of this repository and pushes once a day at 00:30 UTC; each run rechecks the preceding 48 hours, so a delayed run catches up. Each run verifies that it fetched every row D1 holds in that read window (`window` on `/api/archive`), and an archived minute whose content changes is an error. Archived files are never deleted when D1 thins or expires rows.

- `snapshots/YYYY/MM/DD/HH.jsonl.gz`: UTC hourly gzip shards. Each line contains the database minute `collectedAt` and the full original `snapshot`, including raw amount strings, order books, source timestamps and failures.
- `manifest.json`: record count, first/last minute, uncompressed SHA-256 checksums, source failures, absent source counts, gaps and the matching D1 checkpoint. Missing fields stay null and failed reads are preserved. No historical value is synthesized.
- Pushes come from the server with a repository-scoped deploy key (write access to this repository only), committed as `papertrade-archiver`. `.github/workflows/archive.yml` is manual-only (`workflow_dispatch`) as a fallback and commits with the built-in `GITHUB_TOKEN`; do not re-enable its schedule while the server timer runs, or the two will race. No wallet, trading key, Cloudflare token or personal GitHub token is stored.

Run locally with Python 3.12 or later, using only its standard library:

```sh
python -m unittest discover -s test -v
python scripts/archive.py
```

If local system DNS resolves the monitor incorrectly, `python scripts/archive.py --public-dns` uses Cloudflare's public DNS for that one HTTPS destination while preserving host/SNI and certificate validation. This is optional; Actions uses normal DNS.

Repeated runs are deterministic and do not duplicate rows. Interrupted runs keep completed shards so rerunning recovers them. An existing snapshot changing at the same minute, cursor failure, or a count mismatch makes the workflow fail before publishing its manifest. Use `python scripts/archive.py --full` (or the manual workflow's full option) to repair gaps older than 48 hours while D1 still retains those rows. Minute gaps and upstream source failures are distinct; counts matching D1 cannot prove every upstream field was collected successfully.

For recovery, disable this workflow and roll the monitor Worker back if necessary; keep both D1 and this repository's data. Stop scheduled runs before the repository grows too large; a future move to object storage requires a separate decision. GitHub Actions consumes the account's available Actions allowance; no billing configuration is changed by this project.
