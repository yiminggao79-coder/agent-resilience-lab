import copy
import unittest
from reporting import day_report, union_minutes
from schedule import conflicts, overlap

def row(identity, start, end, room='main', day='2027-05-10', attendees=1):
    return dict(id=identity, room=room, day=day, start=start, end=end, attendees=attendees)

class HalfOpenConflicts(unittest.TestCase):
    def test_adjacent_both_directions(self):
        rows = [row('a', '08:00', '09:00'), row('b', '09:00', '10:00'),
                row('c', '07:00', '08:00')]
        self.assertEqual(conflicts(rows), [])

    def test_actual_overlap_detected(self):
        rows = [row('b', '08:30', '09:30'), row('a', '08:00', '09:00')]
        self.assertEqual(conflicts(rows), [['a', 'b']])

    def test_containment_and_identical(self):
        rows = [row('a', '08:00', '10:00'), row('b', '08:30', '09:00'),
                row('c', '08:00', '10:00')]
        self.assertEqual(conflicts(rows), [['a', 'b'], ['a', 'c'], ['b', 'c']])

    def test_pairs_sorted_unique_and_normalized_room(self):
        rows = [row('z', '08:00', '09:00', ' MAIN '), row('y', '08:30', '09:30', 'main'),
                row('x', '08:30', '09:30', 'Annex')]
        self.assertEqual(conflicts(rows), [['y', 'z']])

    def test_interval_touching_at_end_of_day(self):
        self.assertFalse(overlap({'room': 'r', 'day': '2027-05-10', 'id': 'a', 'start': 1380, 'end': 1440},
                                 {'room': 'r', 'day': '2027-05-10', 'id': 'b', 'start': 1440, 'end': 1500}))

class UnionMinutes(unittest.TestCase):
    def test_nested_not_summed(self):
        rows = [row('a', '08:00', '10:00'), row('b', '08:30', '09:00')]
        self.assertEqual(day_report(rows, '2027-05-10')['rooms']['main']['minutes'], 120)

    def test_partial_overlap(self):
        rows = [row('a', '08:00', '09:00'), row('b', '08:30', '10:00')]
        self.assertEqual(day_report(rows, '2027-05-10')['rooms']['main']['minutes'], 120)

    def test_chain_merge_and_ids_order(self):
        rows = [row('c', '10:00', '11:00'), row('a', '08:00', '09:30'),
                row('b', '09:00', '10:30')]
        self.assertEqual(day_report(rows, '2027-05-10'),
            {'day': '2027-05-10', 'rooms': {'main': {'minutes': 180, 'ids': ['a', 'b', 'c']}}})

    def test_identical_duplicate_spans(self):
        self.assertEqual(union_minutes([(0, 60), (0, 60), (30, 90)]), 90)

    def test_rooms_independent(self):
        rows = [row('a', '08:00', '09:00', 'main'), row('b', '08:00', '09:00', 'annex')]
        report = day_report(rows, '2027-05-10')
        self.assertEqual(report['rooms']['main'], {'minutes': 60, 'ids': ['a']})
        self.assertEqual(report['rooms']['annex'], {'minutes': 60, 'ids': ['b']})

    def test_union_helper_boundaries(self):
        self.assertEqual(union_minutes([]), 0)
        self.assertEqual(union_minutes([(60, 120), (0, 60)]), 120)
        self.assertEqual(union_minutes([(0, 60), (61, 120)]), 119)

class CallerInputsPreserved(unittest.TestCase):
    def test_rows_untouched(self):
        rows = [row('b', '08:30', '09:30', ' MAIN '), row('a', '08:00', '09:00')]
        before = copy.deepcopy(rows)
        conflicts(rows)
        day_report(rows, '2027-05-10')
        self.assertEqual(rows, before)

    def test_validation_retained(self):
        with self.assertRaises(ValueError):
            conflicts([row('a', '09:00', '08:00')])
        with self.assertRaises(ValueError):
            day_report([row('a', '08:00', '09:00')], '2027-5-10')
        with self.assertRaises(ValueError):
            day_report([row('a', '08:00', '09:00'), row('a', '10:00', '11:00')], '2027-05-10')

if __name__ == '__main__':
    unittest.main()
