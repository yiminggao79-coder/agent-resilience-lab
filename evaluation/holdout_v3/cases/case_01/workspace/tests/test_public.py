import unittest
from report import summarize


class OccupancyTests(unittest.TestCase):
    def test_overlap_is_busy_once(self):
        records = [{"room": "west", "start": 2, "end": 7},
                   {"room": "west", "start": 4, "end": 9}]
        self.assertEqual(summarize(records, 0, 10),
                         [{"room": "west", "busy_minutes": 7, "peak": 2}])

    def test_empty(self):
        self.assertEqual(summarize([], 0, 10), [])

