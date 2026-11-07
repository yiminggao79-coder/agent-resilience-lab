"""Readonly follow CLI with a real second process; no model or tools run."""
import contextlib
import io
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from agent_lab.__main__ import main
from agent_lab.store import Store
from tests.dev import test_event_reader as reader_fixture

TASK = reader_fixture.TASK


class EventFollowCLITests(unittest.TestCase):
    setUp = reader_fixture.EventReaderTests.setUp
    event = reader_fixture.EventReaderTests.event

    def call(self, options):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
             patch('agent_lab.__main__.OpenAIModel', side_effect=AssertionError('no model')), \
             patch('agent_lab.__main__.Tools', side_effect=AssertionError('no tool runtime')):
            try: code = main(['--db', str(self.path), 'events', TASK, *options])
            except SystemExit as error: code = error.code
        return code, out.getvalue(), err.getvalue()

    def test_default_single_page_and_explicit_follow_format_remain_distinct(self):
        self.event(1)
        before = self.path.read_bytes(); mode = self.path.stat().st_mode
        code, out, err = self.call([]); self.assertEqual(code, 0, err)
        default = json.loads(out); self.assertNotIn('type', default)
        code, out, err = self.call(['--follow-seconds', '.01', '--follow-max-pages', '1'])
        self.assertEqual(code, 0, err)
        frames = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([frame['type'] for frame in frames], ['event_page', 'event_follow_finished'])
        self.assertEqual(frames[-1]['reason'], 'page_limit')
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(self.path.stat().st_mode, mode)
        self.assertNotIn('PRIVATE_HISTORY', out)

    def test_invalid_options_do_not_create_missing_database(self):
        for options in (['--follow-max-pages', '1'], ['--follow-seconds', '0'],
                        ['--follow-seconds', '31'], ['--follow-seconds', 'nan'],
                        ['--follow-seconds', '1', '--follow-max-pages', '0']):
            with self.subTest(options=options): self.assertEqual(self.call(options)[0], 2)
        missing = self.path.with_name('missing.sqlite')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--db', str(missing), 'events', TASK, '--follow-seconds', '.1'])
        self.assertEqual(code, 2); self.assertFalse(missing.exists())

    def test_keyboard_interrupt_closes_readonly_store_without_stopping_task(self):
        opened = []
        original = Store.open_readonly
        def capture(path):
            store = original(path); opened.append(store); return store
        def interrupted(*args, **kwargs):
            yield {'type': 'event_page', 'events': []}
            raise KeyboardInterrupt()
        before = self.path.read_bytes()
        with patch('agent_lab.event_reader.follow_events', side_effect=interrupted, create=True), \
             patch.object(Store, 'open_readonly', side_effect=capture):
            code, out, err = self.call(['--follow-seconds', '1'])
        self.assertEqual(code, 130, err)
        self.assertNotIn('Traceback', err); self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError): opened[0].db.execute('SELECT 1')

    def test_second_process_emits_before_exit_and_observes_a_subsequent_commit(self):
        repo = Path(__file__).resolve().parents[2]
        proc = subprocess.Popen([sys.executable, '-m', 'agent_lab', '--db', str(self.path),
                                 'events', TASK, '--follow-seconds', '2', '--follow-max-pages', '2'],
                                cwd=repo, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 3)
            self.assertTrue(ready, 'no flushed first observation')
            first_line = proc.stdout.readline()
            first = json.loads(first_line)
            self.assertEqual(first['type'], 'event_page'); self.assertEqual(first['events'], [])
            self.assertIsNone(proc.poll(), 'first page was buffered until completion')
            self.event(5, meta={'tool': 'read_file'})
            tail, error = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 0, error)
            frames = [first, *[json.loads(line) for line in tail.splitlines()]]
            self.assertEqual([item['type'] for item in frames], ['event_page', 'event_page', 'event_follow_finished'])
            self.assertEqual([event['seq'] for event in frames[1]['events']], [5])
            self.assertEqual(frames[-1]['last_seq'], 5); self.assertEqual(frames[-1]['event_count'], 1)
            self.assertNotIn('PRIVATE_HISTORY', first_line + tail + error)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=2)
            proc.stdout.close(); proc.stderr.close()


if __name__ == '__main__': unittest.main()
