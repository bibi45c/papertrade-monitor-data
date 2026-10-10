"""Archive only public market snapshots. No account credentials are required."""
import argparse
import gzip
import hashlib
import http.client
import json
import pathlib
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = 'https://papertrade-monitor.papertrade-monitor.workers.dev'
MINUTE = 60_000


class ResolvedHTTPSConnection(http.client.HTTPSConnection):
    """Diagnostic fallback: normal TLS validation and original host/SNI retained."""
    def __init__(self, host, address):
        super().__init__(host, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        self.sock = self._context.wrap_socket(socket.create_connection((self.address, 443), self.timeout), server_hostname=self.host)


def public_dns_get(url):
    parsed = urllib.parse.urlsplit(url)
    # The fallback is restricted to this public monitor; never forward credentials.
    if parsed.scheme != 'https' or parsed.hostname != urllib.parse.urlsplit(DEFAULT_URL).hostname:
        raise ValueError('DNS fallback is restricted to the public monitor')
    query = 'https://cloudflare-dns.com/dns-query?' + urllib.parse.urlencode({'name': parsed.hostname, 'type': 'A'})
    req = urllib.request.Request(query, headers={'Accept': 'application/dns-json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        answers = json.load(response).get('Answer', [])
    addresses = [a['data'] for a in answers if a.get('type') == 1]
    if not addresses:
        raise RuntimeError('No public DNS address')
    conn = ResolvedHTTPSConnection(parsed.hostname, addresses[0])
    try:
        conn.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''), headers={'Accept': 'application/json', 'User-Agent': 'papertrade-data-archive'})
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError('Public endpoint HTTP ' + str(response.status))
        return json.loads(response.read())
    finally:
        conn.close()


def fetch_page(base_url, after, through, public_dns=False):
    url = base_url.rstrip('/') + '/api/archive?' + urllib.parse.urlencode({'after': after, 'through': through, 'limit': 120})
    for attempt in range(3):
        try:
            if public_dns:
                return public_dns_get(url)
            req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'papertrade-data-archive'})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError('Archive endpoint HTTP ' + str(error.code)) from None
            delay = error.headers.get('Retry-After', '')
            delay = float(delay) if delay.replace('.', '', 1).isdigit() else 2 ** attempt
            if delay > 30:
                raise RuntimeError('Endpoint requested a retry outside this run budget') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            if attempt == 2:
                raise RuntimeError('Archive network read failed: ' + type(error).__name__) from None
            delay = 2 ** attempt
        time.sleep(delay)
    raise RuntimeError('Archive retries exhausted')


def canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8') + b'\n'


def shard_path(root, at):
    hour = datetime.fromtimestamp(at / 1000, timezone.utc)
    return root / 'snapshots' / hour.strftime('%Y/%m/%d/%H.jsonl.gz')


def read_shard(path):
    if not path.exists():
        return []
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        return [json.loads(line) for line in source if line.strip()]


def validate_row(row):
    at = row.get('collectedAt')
    if type(at) is not int or at < 0 or at % MINUTE or not isinstance(row.get('snapshot'), dict):
        raise ValueError('Invalid snapshot minute or payload')
    sample_at = row['snapshot'].get('collectedAt')
    if type(sample_at) not in (int, float) or int(sample_at // MINUTE) * MINUTE != at:
        raise ValueError('Snapshot timestamp disagrees with database minute')
    return at


def merge_rows(root, rows):
    grouped = {}
    for row in rows:
        path = shard_path(root, validate_row(row))
        grouped.setdefault(path, []).append(row)
    added = 0
    for path, incoming in grouped.items():
        existing = {validate_row(row): row for row in read_shard(path)}
        for row in incoming:
            at = row['collectedAt']
            if at in existing and canonical(existing[at]) != canonical(row):
                raise ValueError('Stored snapshot changed at minute ' + str(at))
            added += at not in existing
            existing[at] = row
        content = b''.join(canonical(existing[k]) for k in sorted(existing))
        compressed = gzip.compress(content, compresslevel=9, mtime=0)
        if not path.exists() or path.read_bytes() != compressed:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + '.tmp')
            temp.write_bytes(compressed)
            temp.replace(path)
    return added


def manifest_for(root, previous=None):
    shards = []; times = []; failures = {}; digest = hashlib.sha256()
    for path in sorted((root / 'snapshots').glob('*/*/*/*.jsonl.gz')):
        rows = read_shard(path); content = b''.join(canonical(row) for row in rows)
        for row in rows:
            times.append(validate_row(row)); digest.update(canonical(row))
            for name, status in row['snapshot'].get('sources', {}).items():
                if status.get('ok') is not True:
                    failures[name] = failures.get(name, 0) + 1
        shards.append({'path': path.relative_to(root).as_posix(), 'count': len(rows), 'sha256': hashlib.sha256(content).hexdigest()})
    if times != sorted(set(times)):
        raise ValueError('Duplicate or out-of-order archived minutes')
    gaps = [{'after': a, 'before': b, 'missingMinutes': (b - a) // MINUTE - 1} for a, b in zip(times, times[1:]) if b - a > MINUTE]
    result = {'version': 1, 'source': DEFAULT_URL + '/api/archive', 'timezone': 'UTC', 'count': len(times), 'firstAt': times[0] if times else None,
              'lastAt': times[-1] if times else None, 'sha256': digest.hexdigest(), 'sourceFailures': failures, 'gaps': gaps, 'shards': shards}
    if previous and 'checkpoint' in previous:
        result['checkpoint'] = previous['checkpoint']
    return result


def archive(root, base_url=DEFAULT_URL, fetcher=fetch_page, through=None, public_dns=False, full=False):
    root = pathlib.Path(root); root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None
    before = manifest_for(root, previous)
    # Re-read 48 hours to recover late inserts or an interrupted run; older shards
    # are retained even after D1's 90-day retention removes the online originals.
    after = 0 if full else max(0, (before['lastAt'] or 0) - 2 * 86_400_000)
    through = int(time.time() * 1000) if through is None else through
    added = 0; available = None; complete = False
    while not complete:
        page = fetcher(base_url, after, through, public_dns)
        if page.get('version') != 1 or page.get('through') != through or type(page.get('complete')) is not bool or not isinstance(page.get('rows'), list):
            raise ValueError('Invalid archive page schema')
        available = page['available']; rows = page['rows']
        cursor = after
        for row in rows:
            at = validate_row(row)
            if at <= cursor or at > through:
                raise ValueError('Archive page cursor failed to advance monotonically')
            cursor = at
        if page.get('nextAfter') != cursor or (not page['complete'] and not rows):
            raise ValueError('Invalid next archive cursor')
        added += merge_rows(root, rows); after = cursor; complete = page['complete']
    result = manifest_for(root, previous)
    oldest, latest = available.get('oldestAt'), available.get('latestAt')
    matched = 0; online_digest = hashlib.sha256()
    for path in sorted((root / 'snapshots').glob('*/*/*/*.jsonl.gz')):
        for row in read_shard(path):
            if oldest is not None and oldest <= row['collectedAt'] <= latest:
                matched += 1; online_digest.update(canonical(row))
    if matched != available['count']:
        raise ValueError('Archive count does not match D1 available history; keep partial files and rerun with --full to repair older gaps')
    # No changing wall clock is persisted, so identical repeated runs do not commit.
    result['checkpoint'] = {'d1OldestAt': oldest, 'd1LatestAt': latest, 'd1Count': available['count'],
                            'matchedCount': matched, 'sha256': online_digest.hexdigest()}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    temp = manifest_path.with_suffix('.tmp'); temp.write_text(encoded, encoding='utf-8'); temp.replace(manifest_path)
    print(json.dumps({'added': added, 'archived': result['count'], 'd1Count': available['count'], 'matched': matched, 'sourceFailures': result['sourceFailures'], 'gaps': result['gaps']}))
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', default=str(pathlib.Path(__file__).resolve().parent.parent))
    parser.add_argument('--url', default=DEFAULT_URL); parser.add_argument('--public-dns', action='store_true'); parser.add_argument('--full', action='store_true')
    args = parser.parse_args(); archive(args.root, args.url, public_dns=args.public_dns, full=args.full)


if __name__ == '__main__':
    main()
