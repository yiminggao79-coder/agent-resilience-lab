"""One-attempt possible-exposure gate over synthetic IDs; no provider or suite."""
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


CHILD = '''import select,sys
from scripts.holdout_gate import ExposureGate
with ExposureGate(sys.argv[1], 'a'*64, 'synthetic_case', 'child_attempt') as gate:
 print('entered',flush=True)
 if sys.argv[2]=='after':
  if not select.select([sys.stdin],[],[],5)[0]:raise SystemExit(2)
  sys.stdin.readline()
  gate.mark_exposure()
  print('marked',flush=True)
 select.select([sys.stdin],[],[],5)
'''


class HoldoutGateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.holdout_gate'),
                             'durable possible-exposure gate is missing')
        self.module = importlib.import_module('scripts.holdout_gate')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'private'; self.root.mkdir(mode=0o700)
        self.suite = 'a' * 64
        self.case = 'synthetic_case'
        self.key = hashlib.sha256((self.suite + self.case).encode('ascii')).hexdigest()
        self.marker = self.root / (self.key + '.exposure.json')
        self.lock = self.root / (self.key + '.lock')

    def gate(self, **kwargs):
        return self.module.ExposureGate(**{'root': self.root, 'suite_sha': self.suite,
                                           'case_id': self.case, 'run_id': 'synthetic_attempt', **kwargs})

    def test_mark_precedes_delegate_is_metadata_only_and_same_active_attempt_repeats(self):
        gate = self.gate(); events = []
        class Delegate:
            identity = 'transparent-model-identity'
            api_key_env = 'synthetic-provider-variable-name'
            def complete(inner, *args, **kwargs):
                self.assertTrue(gate.exposure_possible)
                self.assertTrue(self.marker.exists())
                events.append((args, kwargs))
                return {'synthetic': True}
        delegate = Delegate(); model = self.module.ExposureModel(delegate, gate)
        self.assertEqual(model.identity, delegate.identity)
        self.assertEqual(model.api_key_env, delegate.api_key_env)
        delegate.identity = 'updated-identity'
        self.assertEqual(model.identity, 'updated-identity')
        with gate:
            self.assertFalse(gate.exposure_possible)
            self.assertFalse(self.marker.exists())
            self.assertEqual(model.complete(['PRIVATE_PROMPT'], ['PRIVATE_TOOL'], 3, max_tokens=7), {'synthetic': True})
            before = self.marker.stat()
            model.complete([], [], timeout=2, max_tokens=1)
            self.assertEqual(self.marker.stat().st_ino, before.st_ino)
            self.assertEqual(self.marker.stat().st_mtime_ns, before.st_mtime_ns)
        metadata = json.loads(self.marker.read_bytes())
        self.assertEqual(set(metadata), {'version', 'suite_sha', 'case_id', 'run_id', 'state'})
        self.assertEqual(metadata['state'], 'possible_provider_exposure')
        self.assertNotIn('PRIVATE', self.marker.read_text())
        self.assertLessEqual(self.marker.stat().st_size, 1024)
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(events), 2)
        with self.assertRaises(self.module.ExposureBlocked):
            with self.gate(): pass

    def test_closed_unentered_reentered_and_other_process_gate_are_rejected(self):
        gate = self.gate()
        with self.assertRaises(self.module.ExposureError): gate.mark_exposure()
        with gate:
            with patch.object(self.module.os, 'getpid', return_value=os.getpid() + 1):
                with self.assertRaises(self.module.ExposureError): gate.mark_exposure()
            self.assertFalse(gate.exposure_possible)
        with self.assertRaises(self.module.ExposureError): gate.mark_exposure()
        with self.assertRaises(self.module.ExposureError):
            with gate: pass
        with self.gate() as next_gate:
            self.assertFalse(next_gate.exposure_possible)

    def test_concurrent_same_case_blocks_but_distinct_scope_is_independent(self):
        with self.gate():
            with self.assertRaises(self.module.ExposureBlocked):
                with self.gate(run_id='other_attempt'): pass
            with self.gate(case_id='different_synthetic_case'): pass
            with self.gate(suite_sha='b' * 64): pass
        with self.gate(): pass  # Lock alone did not consume an attempt.
        self.assertFalse(self.marker.exists())

    def child(self, mode):
        process = subprocess.Popen([sys.executable, '-c', CHILD, str(self.root), mode],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_child, process)
        self.line(process, 'entered')
        if mode == 'after':
            process.stdin.write('mark now\n'); process.stdin.flush()
        return process

    @staticmethod
    def stop_child(process):
        if process.poll() is None: process.kill()
        process.communicate(timeout=2)

    def line(self, process, expected):
        self.assertTrue(select.select([process.stdout], [], [], 3)[0], 'trusted gate fixture handshake timed out')
        self.assertEqual(process.stdout.readline().strip(), expected)

    def test_actual_process_death_before_mark_allows_later_attempt(self):
        process = self.child('before')
        with self.assertRaises(self.module.ExposureBlocked):
            with self.gate(): pass
        process.kill(); process.communicate(timeout=2)
        self.assertEqual(process.returncode, -9)
        with self.gate() as gate: self.assertFalse(gate.exposure_possible)
        self.assertFalse(self.marker.exists())

    def test_actual_process_death_after_mark_before_provider_still_blocks_later_attempt(self):
        process = self.child('after'); self.line(process, 'marked')
        process.kill(); process.communicate(timeout=2)
        self.assertEqual(process.returncode, -9)
        gate = self.gate(run_id='new_attempt')
        with self.assertRaises(self.module.ExposureBlocked):
            with gate: pass
        self.assertTrue(gate.exposure_possible)
        self.assertTrue(self.marker.exists())

    def test_delegate_exception_preserves_possible_marker_without_relabeling_delivery(self):
        class Failure(Exception): pass
        class Delegate:
            def complete(self, *args, **kwargs): raise Failure('synthetic callback/transport failure')
        with self.gate() as gate:
            with self.assertRaises(Failure): self.module.ExposureModel(Delegate(), gate).complete([], [], 1, 1)
            self.assertTrue(gate.exposure_possible)
        with self.assertRaises(self.module.ExposureBlocked):
            with self.gate(run_id='retry'): pass

    def test_partial_write_or_fsync_failure_poison_same_active_attempt_before_delegate(self):
        for fault in ('partial_write', 'file_fsync', 'directory_fsync'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                calls = []
                class Delegate:
                    def complete(self, *args, **kwargs): calls.append(True)
                gate = self.gate(root=Path(directory))
                actual_write, actual_fsync = os.write, os.fsync
                count = [0]
                def partial(fd, data):
                    actual_write(fd, data[:max(1, len(data) // 2)])
                    raise OSError('synthetic private write failure')
                def failed_fsync(fd):
                    count[0] += 1
                    if count[0] == (1 if fault == 'file_fsync' else 2):
                        raise OSError('synthetic private fsync failure')
                    return actual_fsync(fd)
                with gate:
                    model = self.module.ExposureModel(Delegate(), gate)
                    target = 'write' if fault == 'partial_write' else 'fsync'
                    with patch.object(self.module.os, target, side_effect=partial if fault == 'partial_write' else failed_fsync):
                        with self.assertRaises(self.module.ExposureUnavailable): model.complete([], [], 1, 1)
                    self.assertTrue(gate.exposure_possible)
                    with self.assertRaises(self.module.ExposureError): model.complete([], [], 1, 1)
                    self.assertEqual(calls, [])
                with self.assertRaises(self.module.ExposureBlocked):
                    with self.gate(root=Path(directory), run_id='retry'): pass

    def test_invalid_types_and_unsafe_private_controls_fail_closed(self):
        for changes in ({'suite_sha': 'A' * 64}, {'suite_sha': True}, {'case_id': '../private'},
                        {'case_id': 'x' * 129}, {'run_id': []}, {'root': 1}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '^invalid_holdout_exposure_gate$'):
                self.gate(**changes)
        self.root.chmod(0o755)
        with self.assertRaises(self.module.ExposureUnavailable):
            with self.gate(): pass
        self.root.chmod(0o700)
        target = self.root.parent / 'unrelated'; target.write_text('unchanged')
        self.lock.symlink_to(target)
        with self.assertRaises(self.module.ExposureError):
            with self.gate(): pass
        self.assertEqual(target.read_text(), 'unchanged')
        self.lock.unlink(); self.lock.write_bytes(b''); self.lock.chmod(0o644)
        with self.assertRaises(self.module.ExposureError):
            with self.gate(): pass
        self.lock.chmod(0o600); os.link(self.lock, self.root / 'alias')
        with self.assertRaises(self.module.ExposureError):
            with self.gate(): pass

    def test_wrong_file_types_symlinked_root_and_marker_are_never_followed(self):
        alias = self.root.parent / 'root-alias'; alias.symlink_to(self.root)
        with self.assertRaises(self.module.ExposureUnavailable):
            with self.gate(root=alias): pass
        self.lock.mkdir()
        with self.assertRaises(self.module.ExposureUnavailable):
            with self.gate(): pass
        self.lock.rmdir(); os.mkfifo(self.lock, 0o600)
        with self.assertRaises(self.module.ExposureUnavailable):
            with self.gate(): pass
        self.lock.unlink()
        target = self.root.parent / 'private-target'; target.write_text('unchanged')
        self.marker.symlink_to(target)
        gate = self.gate()
        with self.assertRaises(self.module.ExposureBlocked):
            with gate: pass
        self.assertTrue(gate.exposure_possible)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_marker_replacement_after_durable_mark_is_not_an_active_retry(self):
        with self.gate() as gate:
            gate.mark_exposure()
            original = self.marker.read_bytes()
            replacement = self.root / 'synthetic-replacement'
            replacement.write_bytes(original); replacement.chmod(0o600)
            os.replace(replacement, self.marker)
            with self.assertRaises(self.module.ExposureUnavailable): gate.mark_exposure()
            self.assertTrue(gate.exposure_possible)

    def test_existing_partial_marker_or_changed_successful_marker_never_delegates(self):
        self.marker.write_bytes(b'{partial'); self.marker.chmod(0o600)
        with self.assertRaises(self.module.ExposureBlocked):
            with self.gate(): pass
        self.marker.unlink()  # Controlled synthetic fixture setup, no release API.
        with self.gate() as gate:
            gate.mark_exposure()
            self.marker.write_bytes(b'changed metadata')
            with self.assertRaises(self.module.ExposureError): gate.mark_exposure()


if __name__ == '__main__': unittest.main()
