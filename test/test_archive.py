import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('archive', pathlib.Path(__file__).resolve().parents[1] / 'scripts/archive.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ArchiveTest(unittest.TestCase):
    def row(self, at):
        return {'collectedAt': at, 'snapshot': {'collectedAt': at + 1, 'rawSummary': {'amount': '99999999999999999999999999'}, 'sources': {'paperBbo': {'ok': False, 'at': None, 'error': 'Missing EVM block'}}}}

    def test_pagination_idempotence_and_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); rows = [self.row(60_000 * i) for i in range(1, 5)]
            def fetch(base, after, through, public_dns):
                remaining = [row for row in rows if after < row['collectedAt'] <= through]; page = remaining[:2]
                return {'version': 1, 'through': through, 'rows': page, 'nextAfter': page[-1]['collectedAt'] if page else after, 'complete': len(remaining) <= 2,
                        'available': {'oldestAt': 60_000, 'latestAt': 240_000, 'count': 4}}
            first = module.archive(root, fetcher=fetch, through=300_000)
            content = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            module.archive(root, fetcher=fetch, through=400_000)
            self.assertEqual(content, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertEqual(first['count'], 4); self.assertEqual(first['sourceFailures']['paperBbo'], 4)
            self.assertEqual(module.read_shard(module.shard_path(root, 60_000))[0], rows[0])

    def test_partial_run_is_recoverable_without_silent_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); row = self.row(60_000)
            module.merge_rows(root, [row]); module.merge_rows(root, [row])
            changed = json.loads(json.dumps(row)); changed['snapshot']['rawSummary']['amount'] = '0'
            with self.assertRaises(ValueError): module.merge_rows(root, [changed])
            self.assertEqual(module.manifest_for(root)['count'], 1)

    def test_missing_minutes_are_reported_and_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); module.merge_rows(root, [self.row(60_000), self.row(180_000)])
            result = module.manifest_for(root)
            self.assertEqual(result['count'], 2); self.assertEqual(result['gaps'][0]['missingMinutes'], 1)

    def test_different_gzip_header_does_not_rewrite_identical_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); row = self.row(60_000); module.merge_rows(root, [row])
            path = module.shard_path(root, 60_000); content = bytearray(path.read_bytes()); content[9] = 3 if content[9] != 3 else 255
            path.write_bytes(content); module.merge_rows(root, [row]); self.assertEqual(path.read_bytes(), bytes(content))

    def test_missing_source_and_count_mismatch_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder); first = self.row(60_000); second = self.row(120_000); second['snapshot']['sources'] = {}
            module.merge_rows(root, [first, second]); self.assertEqual(module.manifest_for(root)['sourceMissing'], {'paperBbo': 1})
            def fetch(base, after, through, public_dns):
                return {'version': 1, 'through': through, 'rows': [], 'nextAfter': after, 'complete': True, 'available': {'oldestAt': 60_000, 'latestAt': 120_000, 'count': 3}}
            with self.assertRaisesRegex(ValueError, 'count does not match'): module.archive(root, fetcher=fetch, through=300_000)
            self.assertFalse((root / 'manifest.json').exists())

    def test_invalid_cursor_and_count_do_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            def fetch(*args): return {'version': 1, 'through': 300_000, 'rows': [], 'nextAfter': 0, 'complete': False, 'available': {'count': 0}}
            with self.assertRaises(ValueError): module.archive(folder, fetcher=fetch, through=300_000)
            self.assertFalse((pathlib.Path(folder) / 'manifest.json').exists())


if __name__ == '__main__': unittest.main()
