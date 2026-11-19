import gzip
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('build_series', pathlib.Path(__file__).resolve().parents[1] / 'scripts/build_series.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
B = module.BUCKET


def write_gz(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


class SeriesTest(unittest.TestCase):
    def test_archive_wins_per_field_chain_fills_gaps_and_nothing_is_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); t0 = module.START + 100 * B
            snap = lambda at, lp, btc_long: {'collectedAt': at, 'snapshot': {'balances': {'lp': lp, 'tvl': 10.0}, 'markets': [{'coin': 'BTC', 'mid': 100.0}, {'coin': 'ETH', 'mid': 10.0}],
                'trading': {'instruments': [{'symbol': 'ETH', 'longOi': 5, 'shortOi': 6}] + ([{'symbol': 'BTC', 'longOi': btc_long, 'shortOi': 1}] if btc_long is not None else [])}}}
            write_gz(root / 'snapshots/2026/10/11/00.jsonl.gz', [snap(t0, 1.0, 3), snap(t0 + 60_000, 3.0, 5), snap(t0 + B, 7.0, None)])
            raw = lambda v: str(int(v * 10**18))
            chain = lambda at, lp: {'target': at // 1000, 'at': at, 'block': 1, 'implementation': '0x0', 'lpRaw': raw(lp), 'tvlRaw': raw(20),
                'instruments': [{'id': 0, 'longOiRaw': raw(9), 'shortOiRaw': raw(2)}, {'id': 1, 'longOiRaw': raw(7), 'shortOiRaw': raw(8)}]}
            write_gz(root / 'chain-backfill/exchange.jsonl.gz', [chain(t0, 99.0), chain(t0 + B, 98.0), chain(t0 + 3 * B, 50.0)])
            fetch = lambda body: [{'t': t0 + k * B, 'o': '200' if body['req']['coin'] == 'BTC' else '20'} for k in range(4)]
            series = module.build(root, now=t0 + 4 * B, fetch=fetch)
            by_t = {p[0]: p for p in series['points']}
            self.assertEqual(by_t[t0][1:9], [2.0, 10.0, 4.0, 1, 5, 6, 100.0, 10.0]); self.assertEqual(by_t[t0][9], 0)
            self.assertEqual(by_t[t0 + B][3:5], [9.0, 2.0]); self.assertEqual(by_t[t0 + B][1], 7.0); self.assertEqual(by_t[t0 + B][9], 2)
            self.assertEqual(by_t[t0 + 3 * B][1], 50.0); self.assertEqual(by_t[t0 + 3 * B][7], 200.0); self.assertEqual(by_t[t0 + 3 * B][9], 1)
            self.assertNotIn(t0 + 2 * B, by_t)
            self.assertEqual(len(series['prices']['BTC']), 4)


if __name__ == '__main__':
    unittest.main()
