import unittest

from report import summarize


class OccupancyEdgeTests(unittest.TestCase):
    def test_clipped_to_window(self):
        records = [{"room": "a", "start": -5, "end": 100}]
        self.assertEqual(summarize(records, 10, 20),
                         [{"room": "a", "busy_minutes": 10, "peak": 1}])

    def test_reservation_ending_before_window_omitted(self):
        records = [{"room": "a", "start": 0, "end": 5},
                   {"room": "b", "start": 0, "end": 10}]
        self.assertEqual(summarize(records, 5, 15),
                         [{"room": "b", "busy_minutes": 5, "peak": 1}])

    def test_touching_intervals_do_not_overlap(self):
        records = [{"room": "a", "start": 0, "end": 5},
                   {"room": "a", "start": 5, "end": 10}]
        self.assertEqual(summarize(records, 0, 10),
                         [{"room": "a", "busy_minutes": 10, "peak": 1}])

    def test_duplicates_count_as_separate_reservations(self):
        records = [{"room": "a", "start": 0, "end": 5},
                   {"room": "a", "start": 0, "end": 5},
                   {"room": "a", "start": 0, "end": 5}]
        self.assertEqual(summarize(records, 0, 10),
                         [{"room": "a", "busy_minutes": 5, "peak": 3}])

    def test_cancelled_and_zero_length_ignored(self):
        records = [{"room": "a", "start": 0, "end": 5, "cancelled": True},
                   {"room": "b", "start": 3, "end": 3},
                   {"room": "c", "start": 3, "end": 3, "cancelled": False}]
        self.assertEqual(summarize(records, 0, 10), [])

    def test_nested_union_and_peak(self):
        records = [{"room": "a", "start": 0, "end": 10},
                   {"room": "a", "start": 2, "end": 4},
                   {"room": "a", "start": 3, "end": 6},
                   {"room": "a", "start": 9, "end": 12}]
        self.assertEqual(summarize(records, 1, 11),
                         [{"room": "a", "busy_minutes": 10, "peak": 3}])

    def test_sorted_by_room(self):
        records = [{"room": "west", "start": 0, "end": 2},
                   {"room": "east", "start": 0, "end": 2},
                   {"room": "North", "start": 0, "end": 2}]
        self.assertEqual([e["room"] for e in summarize(records, 0, 10)],
                         ["North", "east", "west"])

    def test_cancelled_default_false(self):
        records = [{"room": "a", "start": 0, "end": 5}]
        self.assertEqual(summarize(records, 0, 5),
                         [{"room": "a", "busy_minutes": 5, "peak": 1}])

    def test_inputs_unchanged(self):
        records = [{"room": "a", "start": 0, "end": 5},
                   {"room": "a", "start": 3, "end": 8, "cancelled": False}]
        snapshot = [dict(r) for r in records]
        summarize(records, 1, 7)
        self.assertEqual(records, snapshot)
        self.assertEqual(records[0], {"room": "a", "start": 0, "end": 5})

    def test_empty_window(self):
        records = [{"room": "a", "start": 0, "end": 5}]
        self.assertEqual(summarize(records, 3, 3), [])


if __name__ == "__main__":
    unittest.main()
