# Papertrade market data archive

Private archive for the public read-only [Papertrade monitor](https://papertrade-monitor.papertrade-monitor.workers.dev/). Code is kept separately in [papertrade-monitor](https://github.com/bibi45c/papertrade-monitor).

D1 is the primary store, collecting once per minute and retaining 90 days. This repository pulls full stored snapshots each hour at minute 23 UTC. GitHub scheduling is best effort; a delayed run catches up and rechecks the preceding 48 hours. Archived files are retained after D1 retention expires. Repository visibility does not make the underlying public market source private.

- `snapshots/YYYY/MM/DD/HH.jsonl.gz`: UTC hourly gzip shards. Each line contains the database minute `collectedAt` and the full original `snapshot`, including raw amount strings, order books, source timestamps and failures.
- `manifest.json`: record count, first/last minute, uncompressed SHA-256 checksums, source failures, gaps and the matching D1 checkpoint. Missing fields stay null and failed reads are preserved. No historical value is synthesized.
- `.github/workflows/archive.yml`: hourly pull and manual runs. Only this repository's built-in `GITHUB_TOKEN` writes commits; no wallet, trading key, Cloudflare token or personal GitHub token is stored.

Run locally with Python 3.12 or later, using only its standard library:

```sh
python -m unittest discover -s test -v
python scripts/archive.py
```

If local system DNS resolves the monitor incorrectly, `python scripts/archive.py --public-dns` uses Cloudflare's public DNS for that one HTTPS destination while preserving host/SNI and certificate validation. This is optional; Actions uses normal DNS.

Repeated runs are deterministic and do not duplicate rows. Interrupted runs keep completed shards so rerunning recovers them. An existing snapshot changing at the same minute, cursor failure, or a count mismatch makes the workflow fail before publishing its manifest. Use `python scripts/archive.py --full` (or the manual workflow's full option) to repair gaps older than 48 hours while D1 still retains those rows. Minute gaps and upstream source failures are distinct; counts matching D1 cannot prove every upstream field was collected successfully.

For recovery, disable this workflow and roll the monitor Worker back if necessary; keep both D1 and this repository's data. Stop scheduled runs before the repository grows too large; a future move to object storage requires a separate decision. GitHub Actions consumes the account's available Actions allowance; no billing configuration is changed by this project.
