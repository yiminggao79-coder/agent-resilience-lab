"""Bounded private output transport; deltas never attest tool completion."""
from contextlib import contextmanager
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class OutputTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agent_lab.tool_output'),
                             'bounded private native-output transport is missing')
        self.output = importlib.import_module('agent_lab.tool_output')

    @contextmanager
    def producer(self, pipe, producer=True):
        descriptor = os.dup(pipe.pass_fds[0])
        with patch.dict(os.environ, {'AGENT_LAB_TOOL_OUTPUT_FD': str(descriptor)}):
            with self.output.inherited_output(producer=producer):
                yield descriptor

    def test_disabled_does_not_allocate_pipe_or_emit_end(self):
        with self.output.NativeOutputPipe(None) as pipe:
            self.assertEqual(pipe.environment, {})
            self.assertEqual(pipe.pass_fds, ())
            pipe.drain(); pipe.finish(); pipe.finish()

    def test_raw_bytes_both_streams_and_single_complete_end(self):
        events = []
        with self.output.NativeOutputPipe(events.append) as pipe:
            with self.producer(pipe):
                self.output.emit_output('stdout', b'\xf0')
                self.output.emit_output('stderr', b'bad\xff\x00')
                self.output.emit_output('stdout', b'\x9f\x98\x80')
            pipe.drain(); pipe.finish(); pipe.finish()
        self.assertEqual(b''.join(e['data'] for e in events if e.get('stream') == 'stdout'), '😀'.encode())
        self.assertEqual(b''.join(e['data'] for e in events if e.get('stream') == 'stderr'), b'bad\xff\x00')
        ends = [e for e in events if e['type'] == 'tool_output_end']
        self.assertEqual(len(ends), 1)
        self.assertFalse(ends[0]['incomplete'])
        self.assertEqual(ends[0]['stdout_bytes'], 4)
        self.assertEqual(ends[0]['stderr_bytes'], 5)

    def test_worker_only_forwards_noninheritable_fifo_and_does_not_send_end(self):
        events = []
        with self.output.NativeOutputPipe(events.append) as pipe:
            with self.producer(pipe, producer=False) as descriptor:
                env, descriptors = self.output.forwarded_output()
                self.assertEqual(descriptors, (descriptor,))
                self.assertEqual(env, {'AGENT_LAB_TOOL_OUTPUT_FD': str(descriptor)})
                self.assertFalse(os.get_inheritable(descriptor))
                self.assertNotIn('AGENT_LAB_TOOL_OUTPUT_FD', os.environ)
                self.output.emit_output('stdout', b'outer JSON must not show')
            pipe.finish()
        self.assertEqual([e['type'] for e in events], ['tool_output_end'])
        self.assertTrue(events[0]['incomplete'])
        self.assertEqual(self.output.forwarded_output(), ({}, ()))

    def test_backpressure_and_cap_drop_without_blocking_and_report_incomplete(self):
        events = []
        with self.output.NativeOutputPipe(events.append) as pipe:
            with self.producer(pipe):
                self.output.emit_output('stdout', b'a' * 300000)
                self.output.emit_output('stderr', b'b' * 300000)
                pipe.drain()  # Let the bounded summary fit after saturated data.
            pipe.finish()
        for stream in ('stdout', 'stderr'):
            self.assertLessEqual(sum(len(e['data']) for e in events if e.get('stream') == stream), 65536)
        self.assertTrue(events[-1]['incomplete'])
        self.assertGreater(events[-1]['dropped_stdout_bytes'], 0)
        self.assertGreater(events[-1]['dropped_stderr_bytes'], 0)

    def test_frame_is_atomic_bounded_and_small_chunk_flood_has_write_budget(self):
        events, lengths = [], []
        real_write = os.write
        with self.output.NativeOutputPipe(events.append) as pipe:
            def write(fd, data):
                lengths.append(len(data))
                return real_write(fd, data)
            with self.producer(pipe), patch.object(self.output.os, 'write', side_effect=write):
                for _ in range(70000): self.output.emit_output('stdout', b'x')
                pipe.drain()
            pipe.finish()
        self.assertLessEqual(len(lengths), 1025)
        self.assertLessEqual(max(lengths), 512)
        self.assertTrue(events[-1]['incomplete'])

    def test_malformed_or_lost_frames_and_missing_end_are_incomplete(self):
        for raw in (b'not json\n', b'x' * 600 + b'\n',
                    b'{"v":1,"t":"d","s":"stdout","q":1,"d":"YQ=="}\n',
                    b'{"v":1,"v":1,"t":"e","q":0,"o":[0,0,0],"e":[0,0,0]}\n'):
            with self.subTest(raw=raw[:30]):
                events = []
                with self.output.NativeOutputPipe(events.append) as pipe:
                    os.write(pipe.pass_fds[0], raw)
                    pipe.finish()  # The writer remains open: finish must not wait for EOF.
                self.assertTrue(events[-1]['incomplete'])

    def test_cap_is_exact_even_when_reader_keeps_up_and_summary_accounts_omission(self):
        events = []
        with self.output.NativeOutputPipe(events.append) as pipe:
            with self.producer(pipe):
                for _ in range(80):
                    for stream in ('stdout', 'stderr'):
                        self.output.emit_output(stream, b'x' * 1024)
                        pipe.drain()
            pipe.finish()
        end = events[-1]
        self.assertTrue(end['summary_received'])
        self.assertTrue(end['incomplete'])
        for stream in ('stdout', 'stderr'):
            self.assertEqual(end[stream + '_bytes'], 65536)
            self.assertEqual(end['dropped_' + stream + '_bytes'], 16384)

    def test_strict_summary_types_sequence_and_reader_work_are_bounded(self):
        records = [
            {'v': True, 't': 'e', 'q': 0, 'o': [0, 0, 0], 'e': [0, 0, 0]},
            {'v': 1, 't': 'e', 'q': False, 'o': [0, 0, 0], 'e': [0, 0, 0]},
            {'v': 1, 't': 'e', 'q': 0, 'o': [False, 0, 0], 'e': [0, 0, 0]},
            {'v': 1, 't': 'e', 'q': 0, 'o': [1, 0, 0], 'e': [0, 0, 0]},
            {'v': 1, 't': 'd', 'q': 0, 's': 'stdout', 'd': '***'},
        ]
        for record in records:
            with self.subTest(record=record):
                events = []
                with self.output.NativeOutputPipe(events.append) as pipe:
                    os.write(pipe.pass_fds[0], json.dumps(record).encode() + b'\n')
                    pipe.finish()
                self.assertTrue(events[-1]['incomplete'])
        events = []
        with self.output.NativeOutputPipe(events.append) as pipe:
            with patch.object(self.output.os, 'read', return_value=b'x' * 8192) as read:
                pipe.drain()
                self.assertLessEqual(read.call_count, 128)
            self.assertLess(len(pipe.buffer), 512)
            pipe.finish()
        self.assertTrue(events[-1]['incomplete'])

    def test_callback_failure_disables_display_and_keyboardinterrupt_propagates(self):
        calls = []
        def broken(event):
            calls.append(event)
            raise RuntimeError('private display error')
        with self.output.NativeOutputPipe(broken) as pipe:
            with self.producer(pipe):
                self.output.emit_output('stdout', b'one')
                self.output.emit_output('stderr', b'two')
            pipe.finish()
        self.assertEqual(len(calls), 1)
        def interrupt(event): raise KeyboardInterrupt()
        with self.output.NativeOutputPipe(interrupt) as pipe:
            with self.producer(pipe): self.output.emit_output('stdout', b'one')
            with self.assertRaises(KeyboardInterrupt): pipe.drain()

    def test_invalid_inherited_descriptor_rejected_and_closed_reader_is_harmless(self):
        with tempfile.TemporaryFile() as file:
            with patch.dict(os.environ, {'AGENT_LAB_TOOL_OUTPUT_FD': str(os.dup(file.fileno()))}):
                with self.assertRaisesRegex(ValueError, '^invalid_tool_output$'):
                    with self.output.inherited_output(producer=True): pass
        with self.output.NativeOutputPipe(lambda event: None) as pipe:
            descriptor = os.dup(pipe.pass_fds[0])
        with patch.dict(os.environ, {'AGENT_LAB_TOOL_OUTPUT_FD': str(descriptor)}):
            with self.output.inherited_output(producer=True):
                self.output.emit_output('stdout', b'closed parent display')

    def test_real_sandbox_collector_displays_before_project_can_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'test_live.py').write_text('''import os,pathlib,time,unittest
class Live(unittest.TestCase):
 def test_live(self):
  assert 'AGENT_LAB_TOOL_OUTPUT_FD' not in os.environ
  print('early-native-output',flush=True)
  print('early-error',file=__import__('sys').stderr,flush=True)
  deadline=time.monotonic()+4
  while not pathlib.Path('release').exists():
   assert time.monotonic()<deadline,'private output callback did not release barrier'
   time.sleep(.01)
''')
            script = '''import json,sys
from agent_lab.tool_output import inherited_output
from agent_lab.sandbox import _run_tests
with inherited_output(producer=True):
 result=_run_tests(sys.argv[1],5)
print(json.dumps(result))
'''
            events = []
            with self.output.NativeOutputPipe(events.append) as pipe:
                env = dict(os.environ); env.update(pipe.environment)
                proc = subprocess.Popen([sys.executable, '-c', script, str(root)], env=env,
                                        pass_fds=pipe.pass_fds, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 7
                    while proc.poll() is None and time.monotonic() < deadline:
                        pipe.drain()
                        if any(b'early-native-output' in e.get('data', b'') for e in events):
                            self.assertIsNone(proc.poll(), 'delta arrived only after the project finished')
                            (root / 'release').write_text('private callback saw output')
                        time.sleep(.01)
                    stdout, stderr = proc.communicate(timeout=1)
                    pipe.finish()
                finally:
                    if proc.poll() is None: proc.kill()
                    proc.communicate()
            self.assertEqual(proc.returncode, 0, stderr.decode(errors='replace'))
            result = json.loads(stdout)
            self.assertTrue(result['ok'], result)
            self.assertIn('early-native-output', result['stdout'])
            self.assertIn('early-error', result['stderr'])
            self.assertTrue(any(e.get('stream') == 'stderr' for e in events))
            self.assertFalse(events[-1]['incomplete'])


if __name__ == '__main__': unittest.main()
