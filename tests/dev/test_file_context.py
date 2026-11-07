"""Bounded known-read observations over temporary files, without Engine/model."""
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools


def sha(data): return hashlib.sha256(data).hexdigest()


class FileContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.file_context'), 'bounded known-read file context is missing')
        self.helper = importlib.import_module('agent_lab.file_context')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        self.tools = CodingTools(self.root)

    def read_record(self, path='a.txt', data=b'original', source='read_file', index=0, record=None):
        target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        record = self.helper.new_record() if record is None else record
        return self.helper.record_read(record, {'name': source, 'args': {'path': path}},
                                       {'ok': True, 'sha256': sha(data)}, index)

    def snapshot(self, notice): return json.loads(notice.split('\n', 1)[1])

    def test_new_record_exact_validation_and_pure_inputs(self):
        record = self.helper.new_record()
        self.assertEqual(record, {'version': 1, 'reads': [], 'coverage_limited': False, 'last_snapshot': None})
        self.helper.validate_record(record, 0)
        valid = self.read_record(); before = copy.deepcopy(valid)
        self.helper.validate_record(valid, 1)
        self.assertEqual(valid, before)
        invalid = [None, {**valid, 'version': True}, {**valid, 'coverage_limited': 1},
                   {**valid, 'extra': True}, {**valid, 'last_snapshot': 'A' * 64},
                   {**valid, 'reads': valid['reads'] * 2}]
        for key, value in (('path', '.hidden'), ('path', 'a/../x'), ('path', './x'), ('path', 'x\\y'),
                           ('path', 'é' * 257), ('path', '/'.join(['x'] * 33)),
                           ('sha256', 'A' * 64), ('source', 'search_files'), ('message_index', True)):
            changed = copy.deepcopy(valid); changed['reads'][0][key] = value; invalid.append(changed)
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaisesRegex(ValueError, '^invalid_file_context$'):
                self.helper.validate_record(value)
        with self.assertRaisesRegex(ValueError, '^invalid_file_context$'): self.helper.validate_record(valid, 0)
        with self.assertRaisesRegex(ValueError, '^invalid_file_context$'): self.helper.validate_record(valid, True)

    def test_successful_builtin_reads_update_lru_and_never_mutate_input(self):
        record = self.read_record('a', b'a', source='read_file', index=1)
        record = self.read_record('b', b'b', source='read_lines', index=2, record=record)
        before = copy.deepcopy(record)
        updated = self.helper.record_read(record, {'name': 'read_file', 'args': {'path': 'a'}},
                                          {'ok': True, 'sha256': sha(b'new a')}, 3)
        self.assertEqual(record, before)
        self.assertEqual([entry['path'] for entry in updated['reads']], ['b', 'a'])
        self.assertEqual(updated['reads'][-1], {'path': 'a', 'sha256': sha(b'new a'), 'source': 'read_file', 'message_index': 3})

    def test_failed_other_and_aliased_tools_do_not_ingest_and_invalid_index_always_rejected(self):
        record = self.read_record()
        for intent, result in (({'name': 'write_file', 'args': {'path': 'a'}}, {'ok': True, 'sha256': 'a' * 64}),
                               ({'name': 'read_file', 'args': {'path': 'a'}}, {'ok': 1, 'sha256': 'a' * 64}),
                               ({'name': 'read_file', 'args': {'path': 'a'}}, {'ok': False}),
                               ({'name': 'read_file', 'requested_tool': 'external', 'args': {'path': 'a'}}, {'ok': True, 'sha256': 'a' * 64})):
            self.assertEqual(self.helper.record_read(record, intent, result, 1), record)
        for index in (True, -1, 1 << 63, None):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, '^invalid_file_context$'):
                self.helper.record_read(record, {'name': 'not_a_read'}, {'ok': False}, index)

    def test_invalid_eligible_reads_and_lru_eviction_mark_sticky_coverage_without_fabrication(self):
        record = self.helper.new_record()
        for path, digest in (('.hidden', 'a' * 64), ('é' * 257, 'a' * 64), ('valid', 'BAD')):
            record = self.helper.record_read(record, {'name': 'read_lines', 'args': {'path': path}},
                                            {'ok': True, 'sha256': digest}, 0)
        self.assertTrue(record['coverage_limited']); self.assertEqual(record['reads'], [])
        for index in range(9): record = self.read_record(str(index), b'data', index=index, record=record)
        self.assertEqual([entry['path'] for entry in record['reads']], [str(i) for i in range(1, 9)])
        self.assertTrue(record['coverage_limited'])
        updated, notice, metadata = self.helper.inspect(self.tools, record)
        self.assertTrue(self.snapshot(notice)['coverage_limited'])
        self.assertEqual(metadata['file_notices'], 8)
        self.assertTrue(updated['coverage_limited'])

    def test_same_bytes_no_initial_notice_then_same_size_mtime_change_and_restoration(self):
        record = self.read_record(); before = copy.deepcopy(record)
        unchanged, notice, metadata = self.helper.inspect(self.tools, record)
        self.assertIsNone(notice); self.assertEqual(unchanged, record)
        self.assertEqual(metadata, {'checked_files': 1, 'file_notices': 0, 'retained_bytes': 0})
        target = self.root / 'a.txt'; info = target.stat()
        target.write_bytes(b'changed!'); os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        changed, notice, metadata = self.helper.inspect(self.tools, record)
        self.assertEqual(record, before)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'changed')
        self.assertEqual(self.snapshot(notice)['files'][0]['current_sha256'], sha(b'changed!'))
        self.assertEqual(changed['reads'][0]['sha256'], sha(b'original'))
        self.assertEqual(metadata['retained_bytes'], len(notice.encode()))
        self.assertIsNone(self.helper.inspect(self.tools, changed)[1])
        target.write_bytes(b'original')
        restored, notice, _ = self.helper.inspect(self.tools, changed)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'same')
        self.assertIsNone(self.helper.inspect(self.tools, restored)[1])

    def test_deleted_and_unsafe_entries_are_reported_without_reading_aliases_or_fifo(self):
        record = self.read_record(); target = self.root / 'a.txt'
        target.unlink()
        _, notice, _ = self.helper.inspect(self.tools, record)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'deleted')
        outside = self.root.parent / 'outside'; outside.write_bytes(b'PRIVATE_OUTSIDE')
        for kind in ('symlink', 'hardlink', 'fifo'):
            if kind == 'symlink': target.symlink_to(outside)
            elif kind == 'hardlink': os.link(outside, target)
            else: os.mkfifo(target)
            with self.subTest(kind=kind), patch.object(self.tools, '_read_at', side_effect=AssertionError('unsafe file read')):
                _, notice, _ = self.helper.inspect(self.tools, record)
                item = self.snapshot(notice)['files'][0]
                self.assertEqual(item['status'], 'unavailable'); self.assertIsNone(item['current_sha256'])
                self.assertNotIn('PRIVATE_OUTSIDE', notice)
            target.unlink()

    def test_signature_ignores_message_index_and_lru_order_but_not_new_read_baseline(self):
        record = self.read_record('b', b'b', index=1)
        record = self.read_record('a', b'a', index=2, record=record)
        (self.root / 'a').write_bytes(b'changed')
        record, notice, _ = self.helper.inspect(self.tools, record)
        self.assertEqual([item['path'] for item in self.snapshot(notice)['files']], ['a', 'b'])
        reread_same = self.helper.record_read(record, {'name': 'read_file', 'args': {'path': 'b'}},
                                             {'ok': True, 'sha256': sha(b'b')}, 5)
        self.assertIsNone(self.helper.inspect(self.tools, reread_same)[1])
        reread_changed = self.helper.record_read(record, {'name': 'read_file', 'args': {'path': 'a'}},
                                                {'ok': True, 'sha256': sha(b'changed')}, 6)
        record, notice, _ = self.helper.inspect(self.tools, reread_changed)
        snapshot = self.snapshot(notice)
        self.assertEqual([item['status'] for item in snapshot['files']], ['same', 'same'])
        self.assertEqual(snapshot['files'][0]['message_index'], 6)
        self.assertIsNone(self.helper.inspect(self.tools, record)[1])

    def test_oversize_current_file_marks_only_snapshot_coverage_and_recovers(self):
        record = self.read_record()
        with (self.root / 'a.txt').open('wb') as target: target.truncate(2 * 1024 * 1024 + 1)
        observed, notice, _ = self.helper.inspect(self.tools, record)
        self.assertFalse(observed['coverage_limited'])
        self.assertTrue(self.snapshot(notice)['coverage_limited'])
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'unavailable')
        (self.root / 'a.txt').write_bytes(b'original')
        _, notice, _ = self.helper.inspect(self.tools, observed)
        self.assertFalse(self.snapshot(notice)['coverage_limited'])
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'same')

    def test_final_filename_replacement_after_stable_fd_read_is_unavailable(self):
        record = self.read_record(); actual = self.tools._read_at
        def replaced(directory, name, limit):
            data, info = actual(directory, name, limit)
            replacement = self.root / 'replacement'; replacement.write_bytes(b'new version')
            os.replace(replacement, self.root / name)
            return data, info
        with patch.object(self.tools, '_read_at', side_effect=replaced):
            _, notice, _ = self.helper.inspect(self.tools, record)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'], 'unavailable')

    def test_eight_max_size_reads_and_notice_size_are_bounded(self):
        record = self.helper.new_record(); data = b'x\n' * (1024 * 1024)
        for index in range(8): record = self.read_record(str(index), data, index=index, record=record)
        actual, limits, total = self.tools._read_at, [], []
        def bounded(directory, name, limit):
            limits.append(limit); result = actual(directory, name, limit); total.append(len(result[0])); return result
        with patch.object(self.tools, '_read_at', side_effect=bounded):
            _, notice, _ = self.helper.inspect(self.tools, record)
        self.assertIsNone(notice); self.assertEqual(sum(total), 16 * 1024 * 1024)
        self.assertEqual(limits, [2 * 1024 * 1024] * 8)
        record = self.helper.new_record()
        for index in range(8):
            path = str(index) + '\x01' * 254 + '/' + '\x02' * 256
            record = self.helper.record_read(record, {'name': 'read_file', 'args': {'path': path}},
                                             {'ok': True, 'sha256': 'a' * 64}, index)
        _, notice, metadata = self.helper.inspect(self.tools, record)
        self.assertLessEqual(len(notice.encode()), 32768)
        self.assertEqual(metadata['retained_bytes'], len(notice.encode()))
        for phrase in ('untrusted', 'historical', 'not read', 'edit guards'):
            self.assertIn(phrase, notice)
        self.assertEqual(set(metadata), {'checked_files', 'file_notices', 'retained_bytes'})


    def test_read_api_switch_same_version_is_not_a_new_freshness_notice(self):
        record=self.read_record('a',b'original',index=1)
        (self.root/'a').write_bytes(b'changed')
        record,notice,_=self.helper.inspect(self.tools,record)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'],'changed')
        record=self.read_record('a',b'changed',index=2,record=record)
        record,notice,_=self.helper.inspect(self.tools,record)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'],'same')
        for index,source in enumerate(('read_lines','read_file','read_lines'),start=3):
            record=self.read_record('a',b'changed',source=source,index=index,record=record)
            record,notice,_=self.helper.inspect(self.tools,record)
            self.assertIsNone(notice)
            self.assertEqual(record['reads'][0]['source'],source)
            self.assertEqual(record['reads'][0]['message_index'],index)
        (self.root/'a').write_bytes(b'genuine-new-change')
        _,notice,_=self.helper.inspect(self.tools,record)
        self.assertEqual(self.snapshot(notice)['files'][0]['status'],'changed')


if __name__ == '__main__': unittest.main()
