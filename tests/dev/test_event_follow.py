"""Bounded readonly follow over synthetic committed SQLite events."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from agent_lab.store import StorageUnavailable
from tests.dev import test_event_reader as reader_fixture

TASK = reader_fixture.TASK


class Clock:
    def __init__(self, on_sleep=None): self.now = 0.; self.sleeps = []; self.on_sleep = on_sleep
    def monotonic(self): return self.now
    def sleep(self, seconds):
        if not 0 < seconds <= .25: raise AssertionError('unbounded/nonpositive poll sleep')
        self.sleeps.append(seconds); self.now += seconds
        if self.on_sleep: self.on_sleep(len(self.sleeps))


class EventFollowTests(unittest.TestCase):
    setUp = reader_fixture.EventReaderTests.setUp
    event = reader_fixture.EventReaderTests.event

    def follow(self, **kwargs):
        function = getattr(self.reader, 'follow_events', None)
        self.assertTrue(callable(function), 'bounded event follow is missing')
        return function(self.store, TASK, **kwargs)

    def collect(self, clock=None, **kwargs):
        clock = clock or Clock()
        with patch.object(self.reader, 'time', clock, create=True): return list(self.follow(**kwargs))

    def test_backlog_pages_have_no_duplicates_and_page_cap_bounds_continuous_events(self):
        for seq in (1, 4, 7, 12): self.event(seq)
        clock = Clock(); frames = self.collect(clock, limit=2, max_pages=2, seconds=30)
        self.assertEqual([frame['type'] for frame in frames], ['event_page', 'event_page', 'event_follow_finished'])
        self.assertEqual([event['seq'] for frame in frames[:-1] for event in frame['events']], [1, 4, 7, 12])
        self.assertEqual(frames[-1], {'type': 'event_follow_finished', 'task_id': TASK,
            'database_identity': frames[0]['database_identity'], 'last_seq': 12,
            'pages': 2, 'event_count': 4, 'reason': 'page_limit'})
        self.assertEqual(clock.sleeps, [])

    def test_empty_observation_is_emitted_once_then_live_commit_is_visible(self):
        def append_on_sleep(count):
            if count == 1: self.event(8, meta={'phase': 'ready'})
        clock = Clock(append_on_sleep)
        frames = self.collect(clock, after=7, seconds=.6)
        self.assertEqual(frames[0]['events'], [])
        self.assertEqual(frames[0]['last_seq'], 7)
        self.assertEqual([event['seq'] for event in frames[1]['events']], [8])
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[-1]['reason'], 'deadline')
        self.assertEqual(frames[-1]['pages'], 2); self.assertEqual(frames[-1]['event_count'], 1)
        self.assertAlmostEqual(sum(clock.sleeps), .6)

    def test_empty_pages_do_not_imply_terminal_or_repeat_until_deadline(self):
        frames = self.collect(seconds=.1)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[-1]['reason'], 'deadline')
        self.assertEqual(frames[-1]['last_seq'], 0)
        self.assertNotIn('phase', frames[-1]); self.assertNotIn('success', frames[-1])
        self.assertNotIn('PRIVATE_HISTORY', json.dumps(frames))

    def test_invalid_follow_and_query_bounds_fail_before_read(self):
        for kwargs in ({'seconds': 0}, {'seconds': -1}, {'seconds': 31}, {'seconds': True},
                       {'seconds': float('inf')}, {'seconds': float('nan')}, {'seconds': '1'},
                       {'max_pages': 0}, {'max_pages': 101}, {'max_pages': True}, {'max_pages': 1.5}):
            with self.subTest(kwargs=kwargs), patch.object(self.reader, 'read_events', side_effect=AssertionError('must validate first')):
                with self.assertRaisesRegex(ValueError, '^invalid_event_follow$'): self.collect(**kwargs)
        for kwargs in ({'after': -1}, {'limit': 101}, {'expected_database': 'bad'}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, '^invalid_event_query$'):
                self.collect(**kwargs)

    def test_database_source_is_implicitly_pinned_after_first_page(self):
        clock = Clock()
        with patch.object(self.reader, 'time', clock, create=True):
            stream = self.follow(seconds=1); first = next(stream)
            self.path.rename(self.path.with_name('old.sqlite'))
            with sqlite3.connect(self.path) as db:
                db.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL);'
                    'CREATE TABLE events(seq INTEGER PRIMARY KEY,task_id TEXT,at REAL,kind TEXT,meta TEXT);')
                db.execute('INSERT INTO tasks VALUES(?,?)', (TASK, '{}'))
            with self.assertRaisesRegex(StorageUnavailable, '^storage_unavailable$'): next(stream)
        self.assertEqual(first['events'], [])

    def test_follow_preserves_caller_owned_transaction(self):
        self.store.db.execute('BEGIN')
        with self.assertRaisesRegex(ValueError, '^invalid_event_query$'): self.collect(seconds=.1)
        self.assertTrue(self.store.db.in_transaction)
        self.store.db.rollback()


if __name__ == '__main__': unittest.main()
