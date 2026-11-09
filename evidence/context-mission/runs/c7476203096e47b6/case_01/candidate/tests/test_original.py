import copy
import unittest
from clockwork import minute
from records import canonical
from schedule import conflicts
from reporting import day_report

def row(identity, start, end, room='main', day='2027-05-10', attendees=1):
    return dict(id=identity, room=room, day=day, start=start, end=end, attendees=attendees)

class OriginalContracts(unittest.TestCase):
    def test_midnight_boundary(self):
        self.assertEqual(minute('24:00'), 1440)
        with self.assertRaises(ValueError):
            minute('24:01')
    def test_canonical_copy(self):
        rows = [row('x', '08:00', '08:30', ' MAIN ')]
        before = copy.deepcopy(rows)
        result = canonical(rows)
        self.assertEqual(result[0]['room'], 'main')
        result[0]['room'] = 'elsewhere'
        self.assertEqual(rows, before)
    def test_duplicate_identity(self):
        with self.assertRaises(ValueError):
            canonical([row('x', '08:00', '09:00'), row('x', '10:00', '11:00')])
    def test_adjacent_is_free(self):
        self.assertEqual(conflicts([row('a', '08:00', '09:00'), row('b', '09:00', '10:00')]), [])
    def test_room_and_day_partition(self):
        self.assertEqual(conflicts([row('a', '08:00', '09:00'), row('b', '08:00', '09:00', 'annex'),
                                    row('c', '08:00', '09:00', day='2027-05-11')]), [])
    def test_union_minutes(self):
        rows = [row('b', '08:15', '08:45'), row('a', '08:00', '09:00'), row('c', '09:00', '09:30')]
        self.assertEqual(day_report(rows, '2027-05-10'),
            {'day': '2027-05-10', 'rooms': {'main': {'minutes': 90, 'ids': ['a', 'b', 'c']}}})
    def test_empty_day(self):
        self.assertEqual(day_report([], '2027-05-10'), {'day': '2027-05-10', 'rooms': {}})
    def test_invalid_interval(self):
        for start, end in [('10:00', '09:00'), ('10:00', '10:00')]:
            with self.assertRaises(ValueError):
                canonical([row('a', start, end)])

if __name__ == '__main__':
    unittest.main()
