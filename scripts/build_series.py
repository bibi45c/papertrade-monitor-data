"""Build series/exposure-series.json: 15-minute trader open interest, LP bankroll and treasury for the monitor chart.

Sources, per field and per 15-minute bucket:
  1. archived minute snapshots (snapshots/**.jsonl.gz): official API values and the Hyperliquid mid of that minute
  2. on-chain backfill (chain-backfill/*.jsonl.gz): Exchange state read at a block for minutes the archive lacks
A field the archive lacks in a bucket falls back to chain samples; each value is the median of that source's samples.
Prices for the continuous price lines are Hyperliquid 15-minute candle opens fetched at build time.
Buckets with no data stay absent. Nothing is interpolated. Standard library only.
"""
import argparse
import gzip
import json
import pathlib
import statistics
import time
import urllib.request

BUCKET = 15 * 60_000
FIELDS = ['lp', 'tvl', 'btcLong', 'btcShort', 'ethLong', 'ethShort', 'btcPx', 'ethPx']
HL_INFO = 'https://api.hyperliquid.xyz/info'
START = 1791590400000  # 2026-10-10T00:00:00Z, before the first archived minute


def num(v):
    try:
        f = float(v)
        return f if f == f and abs(f) != float('inf') else None
    except (TypeError, ValueError):
        return None


def archive_samples(root):
    for path in sorted((root / 'snapshots').glob('*/*/*/*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line); s = row['snapshot']
                inst = {i.get('symbol'): i for i in (s.get('trading') or {}).get('instruments') or []}
                mid = {m.get('coin'): m.get('mid') for m in s.get('markets') or []}
                bal = s.get('balances') or {}
                book = s.get('traderBook') if ((s.get('sources') or {}).get('traderBook') or {}).get('ok') else None
                yield row['collectedAt'], {
                    'lp': num(bal.get('lp')), 'tvl': num(bal.get('tvl')),
                    'btcLong': num((inst.get('BTC') or {}).get('longOi')), 'btcShort': num((inst.get('BTC') or {}).get('shortOi')),
                    'ethLong': num((inst.get('ETH') or {}).get('longOi')), 'ethShort': num((inst.get('ETH') or {}).get('shortOi')),
                    'btcPx': num(mid.get('BTC')), 'ethPx': num(mid.get('ETH')), 'upnl': num((book or {}).get('unrealizedPnl')),
                }


def chain_samples(root, opens):
    for path in sorted((root / 'chain-backfill').glob('*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            for line in source:
                if not line.strip():
                    continue
                r = json.loads(line); legs = {i['id']: i for i in r['instruments']}
                key = r['at'] // BUCKET * BUCKET
                e18 = lambda v: None if v is None else int(v) / 1e18
                yield r['at'], {
                    'lp': e18(r['lpRaw']), 'tvl': e18(r['tvlRaw']),
                    'btcLong': e18(legs.get(0, {}).get('longOiRaw')), 'btcShort': e18(legs.get(0, {}).get('shortOiRaw')),
                    'ethLong': e18(legs.get(1, {}).get('longOiRaw')), 'ethShort': e18(legs.get(1, {}).get('shortOiRaw')),
                    'btcPx': opens.get('BTC', {}).get(key), 'ethPx': opens.get('ETH', {}).get(key), 'upnl': None,
                }


def fetch_candles(coin, start, end, fetch=None):
    """15-minute candles [t, open] from start to end, in windows below the 5,000-candle response cap."""
    def default_fetch(body):
        req = urllib.request.Request(HL_INFO, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'papertrade-monitor-data'})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    fetch = fetch or default_fetch
    out = {}; window = 4000 * BUCKET; t = start
    while t < end:
        for c in fetch({'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': '15m', 'startTime': t, 'endTime': min(end, t + window)}}):
            out[int(c['t'])] = float(c['o'])
        t += window
    return out


def build(root, now=None, fetch=None):
    now = now or int(time.time() * 1000)
    opens = {coin: fetch_candles(coin, START, now, fetch) for coin in ('BTC', 'ETH')}
    buckets = {}
    for priority, samples in ((0, archive_samples(root)), (1, chain_samples(root, opens))):
        for at, values in samples:
            buckets.setdefault(at // BUCKET * BUCKET, ([], []))[priority].append(values)
    points = []
    for key in sorted(buckets):
        archive, chain = buckets[key]; used_chain = False; row = [key]
        for f in FIELDS:
            a = [v[f] for v in archive if v[f] is not None]
            if a:
                row.append(statistics.median(a)); continue
            c = [v[f] for v in chain if v[f] is not None]
            row.append(statistics.median(c) if c else None); used_chain = used_chain or bool(c)
        source = 0 if archive and not used_chain else 2 if archive else 1
        upnl = [v['upnl'] for v in archive if v['upnl'] is not None]
        row += [source, len(archive) + len(chain), statistics.median(upnl) if upnl else None]
        points.append([round(v, 6) if isinstance(v, float) else v for v in row])
    return {
        'version': 1, 'generatedAt': now, 'bucketMs': BUCKET,
        'columns': ['t (bucket start)', 'lp', 'tvl', 'btcLongOi', 'btcShortOi', 'ethLongOi', 'ethShortOi', 'btcPx', 'ethPx',
                    'source (0 archive, 1 chain, 2 archive with chain-filled fields)', 'samples', 'traderUnrealizedPnl'],
        'aggregation': 'per-field median of the best source within each 15-minute bucket',
        'prices': {'interval': '15m', 'field': 'open', 'source': HL_INFO + ' candleSnapshot',
                   **{coin: [[t, v] for t, v in sorted(o.items())] for coin, o in opens.items()}},
        'points': points,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(pathlib.Path(__file__).resolve().parent.parent))
    parser.add_argument('--out', default=None)
    args = parser.parse_args(); root = pathlib.Path(args.root)
    out = pathlib.Path(args.out) if args.out else root / 'series' / 'exposure-series.json'
    result = build(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix('.tmp'); temp.write_text(json.dumps(result, separators=(',', ':')), encoding='utf-8'); temp.replace(out)
    print(json.dumps({'points': len(result['points']), 'chain': sum(p[9] == 1 for p in result['points']), 'from': result['points'][0][0], 'to': result['points'][-1][0]}))


if __name__ == '__main__':
    main()
