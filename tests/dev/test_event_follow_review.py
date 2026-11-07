"""Independent bounded follow checks, reusing only synthetic SQLite fixtures."""
import unittest
from unittest.mock import patch

from tests.dev import test_event_follow as follow_fixture
from tests.dev import test_event_reader as reader_fixture


class EventFollowReviewTests(unittest.TestCase):
    setUp = reader_fixture.EventReaderTests.setUp
    event = reader_fixture.EventReaderTests.event

    def test_slow_consumer_and_sustained_writer_respect_deadline_and_page_cap(self):
        self.event(1); self.event(2)
        clock = follow_fixture.Clock()
        with patch.object(self.reader,'time',clock), \
             patch.object(self.reader,'read_events',wraps=self.reader.read_events) as reads:
            stream = self.reader.follow_events(self.store,reader_fixture.TASK,limit=1,seconds=.1)
            first = next(stream)
            self.assertTrue(first['has_more'])
            self.assertFalse(self.store.db.in_transaction)
            clock.now = .2  # Consumer holds the first yielded page past deadline.
            tail = list(stream)
            self.assertEqual(reads.call_count,1)
        self.assertEqual(len(tail),1)
        self.assertEqual(tail[0]['reason'],'deadline')
        self.assertEqual(tail[0]['last_seq'],1)
        self.assertEqual(clock.sleeps,[])

        def committed_writer(count):
            self.event(10+count)
        clock = follow_fixture.Clock(committed_writer)
        with patch.object(self.reader,'time',clock), \
             patch.object(self.reader,'read_events',wraps=self.reader.read_events) as reads:
            frames = list(self.reader.follow_events(self.store,reader_fixture.TASK,
                          after=2,limit=1,seconds=30,max_pages=3))
            self.assertEqual(reads.call_count,3)
            self.assertTrue(all(call.kwargs['expected_database']==frames[0]['database_identity']
                                for call in reads.call_args_list[1:]))
        self.assertEqual(frames[0]['events'],[])
        self.assertEqual([event['seq'] for frame in frames[:-1] for event in frame['events']],[11,12])
        self.assertEqual(frames[-1]['pages'],3)  # Initial empty page consumes one.
        self.assertEqual(frames[-1]['event_count'],2)
        self.assertEqual(frames[-1]['reason'],'page_limit')
        self.assertEqual(clock.sleeps,[.25,.25])

    def test_huge_numeric_arguments_have_fixed_validation_before_query(self):
        for options in ({'seconds':10**500}, {'seconds':-(10**500)}, {'max_pages':10**500}):
            with self.subTest(options=options), \
                 patch.object(self.reader,'read_events',side_effect=AssertionError('must reject before query')) as reads:
                with self.assertRaisesRegex(ValueError,'^invalid_event_follow$'):
                    list(self.reader.follow_events(self.store,reader_fixture.TASK,**options))
                reads.assert_not_called()
