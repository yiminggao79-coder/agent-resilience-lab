import copy
import unittest
from requests import allocate

def row(identity, start, end, room='main', day='2027-05-10', attendees=1, **extra):
    result = dict(id=identity, room=room, day=day, start=start, end=end, attendees=attendees)
    result.update(extra)
    return result

def block(room, start, end, day='2027-05-10'):
    return dict(room=room, day=day, start=start, end=end)

class PriorityOrder(unittest.TestCase):
    def test_higher_first_ties_in_input_order(self):
        rooms = {'main': 3}
        result = allocate([], [row('a', '08:00', '09:00'),
                               row('b', '08:00', '09:00', priority=5),
                               row('c', '08:00', '09:00', priority=5)], rooms)
        self.assertEqual(result['accepted'], ['b', 'c', 'a'])
        self.assertEqual(result['rejected'], [])

    def test_negative_priority_allowed(self):
        result = allocate([], [row('a', '08:00', '09:00', priority=-3),
                               row('b', '08:00', '09:00')], {'main': 2})
        self.assertEqual(result['accepted'], ['b', 'a'])

    def test_priority_preserves_input_order_for_equal_default(self):
        result = allocate([], [row('z', '08:00', '09:00'), row('y', '08:00', '09:00')], {'main': 2})
        self.assertEqual(result['accepted'], ['z', 'y'])

    def test_lower_priority_loses_slot_to_higher(self):
        result = allocate([], [row('a', '08:00', '09:00'), row('b', '08:00', '09:00', priority=1)], {})
        self.assertEqual(result['accepted'], ['b'])
        self.assertEqual(result['rejected'], [{'id': 'a', 'reason': 'capacity'}])

class Capacity(unittest.TestCase):
    def test_default_capacity_one_blocks_overlap_wins_by_priority(self):
        result = allocate([], [row('a', '08:00', '09:00'), row('b', '08:30', '09:30')], {})
        self.assertEqual(result['accepted'], ['a'])
        self.assertEqual(result['rejected'], [{'id': 'b', 'reason': 'capacity'}])

    def test_adjacent_does_not_consume_capacity(self):
        result = allocate([], [row('a', '08:00', '09:00'), row('b', '09:00', '10:00')], {})
        self.assertEqual(result['accepted'], ['a', 'b'])

    def test_simultaneous_sum_not_pairwise(self):
        # Each pair overlaps only partially; capacity 4 is exceeded only mid-request.
        existing = [row('e1', '08:00', '09:00', attendees=2),
                    row('e2', '08:30', '10:00', attendees=2)]
        result = allocate(existing, [row('a', '08:45', '09:15', attendees=1)], {'MAIN ': 4})
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['rejected'], [{'id': 'a', 'reason': 'capacity'}])

    def test_simultaneous_sum_fits(self):
        existing = [row('e1', '08:00', '09:00', attendees=2),
                    row('e2', '08:30', '10:00', attendees=1)]
        result = allocate(existing, [row('a', '08:45', '09:15', attendees=1)], {'main': 4})
        self.assertEqual(result['accepted'], ['a'])

    def test_single_request_larger_than_capacity(self):
        result = allocate([], [row('a', '08:00', '09:00', attendees=3)], {'main': 2})
        self.assertEqual(result['rejected'], [{'id': 'a', 'reason': 'capacity'}])

    def test_existing_over_capacity_does_not_block_unrelated_request(self):
        existing = [row('e1', '08:00', '09:00', attendees=5),
                    row('e2', '08:00', '09:00', attendees=5)]
        result = allocate(existing, [row('a', '10:00', '11:00')], {'main': 2})
        self.assertEqual(result['accepted'], ['a'])
        self.assertEqual(result['rejected'], [])

    def test_earlier_accepted_requests_count(self):
        result = allocate([], [row('a', '08:00', '09:00', attendees=2, priority=1),
                               row('b', '08:30', '09:30')], {'main': 2})
        self.assertEqual(result['accepted'], ['a'])
        self.assertEqual(result['rejected'], [{'id': 'b', 'reason': 'capacity'}])

    def test_room_day_partition(self):
        result = allocate([], [row('a', '08:00', '09:00'),
                               row('b', '08:00', '09:00', 'annex'),
                               row('c', '08:00', '09:00', day='2027-05-11')], {})
        self.assertEqual(result['accepted'], ['a', 'b', 'c'])

class Blocks(unittest.TestCase):
    def test_blocked_beats_capacity(self):
        result = allocate([], [row('a', '08:00', '09:00'),
                               row('b', '08:00', '09:00')],
                          {}, [block(' MAIN ', '08:30', '10:00')])
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['rejected'], [{'id': 'a', 'reason': 'blocked'},
                                              {'id': 'b', 'reason': 'blocked'}])

    def test_block_endpoint_touch_is_free(self):
        result = allocate([], [row('a', '08:00', '09:00')], {}, [block('main', '09:00', '10:00')])
        self.assertEqual(result['accepted'], ['a'])

    def test_block_other_room_or_day_ignored(self):
        result = allocate([], [row('a', '08:00', '09:00')], {},
                          [block('annex', '08:00', '09:00'), block('main', '08:00', '09:00', '2027-05-11')])
        self.assertEqual(result['accepted'], ['a'])

    def test_optional_blocks_argument(self):
        self.assertEqual(allocate([], [row('a', '08:00', '09:00')], {})['accepted'], ['a'])

class Validation(unittest.TestCase):
    def test_duplicate_anywhere(self):
        with self.assertRaises(ValueError):
            allocate([row('a', '08:00', '09:00')], [row('a', '10:00', '11:00')], {})
        with self.assertRaises(ValueError):
            allocate([], [row('a', '08:00', '09:00'), row('a', '10:00', '11:00')], {})
        with self.assertRaises(ValueError):
            allocate([row('a', '08:00', '09:00'), row('a', '10:00', '11:00')], [], {})

    def test_bad_priority(self):
        for bad in (True, False, 1.0, '1'):
            with self.assertRaises(ValueError):
                allocate([], [dict(row('a', '08:00', '09:00'), priority=bad)], {})

    def test_bad_capacity(self):
        for bad in (0, -1, True, 2.0, '2'):
            with self.assertRaises(ValueError):
                allocate([], [], {'main': bad})

    def test_duplicate_normalized_capacity(self):
        with self.assertRaises(ValueError):
            allocate([], [], {'Main': 2, ' main ': 3})

    def test_bad_block(self):
        with self.assertRaises(ValueError):
            allocate([], [], {}, [block('main', '10:00', '09:00')])
        with self.assertRaises(ValueError):
            allocate([], [], {}, [dict(room='main', day='2027-05-10', start='08:00')])
        with self.assertRaises(ValueError):
            allocate([], [], {}, [dict(room='  ', day='2027-05-10', start='08:00', end='09:00')])

    def test_invalid_request_interval_rejected_before_allocation(self):
        with self.assertRaises(ValueError):
            allocate([], [row('a', '08:00', '09:00'), row('b', '10:00', '10:00')], {})

class Output(unittest.TestCase):
    def test_schedule_sorted_and_canonical(self):
        result = allocate([row('e', '09:00', '10:00', ' Annex ')],
                          [row('a', '08:00', '09:00', day='2027-05-11', priority=1),
                           row('b', '08:00', '09:00')], {' annex ': 4})
        self.assertEqual(result['schedule'],
            [{'id': 'e', 'room': 'annex', 'day': '2027-05-10', 'start': 540, 'end': 600, 'attendees': 1},
             {'id': 'b', 'room': 'main', 'day': '2027-05-10', 'start': 480, 'end': 540, 'attendees': 1},
             {'id': 'a', 'room': 'main', 'day': '2027-05-11', 'start': 480, 'end': 540, 'attendees': 1}])
        self.assertEqual(result['accepted'], ['a', 'b'])

    def test_caller_objects_unchanged(self):
        existing = [row('e', '09:00', '10:00', ' Annex ', attendees=2)]
        requests = [row('a', '08:00', '09:00', priority=3)]
        capacities = {'Annex': 4}
        blocks = [block('main', '20:00', '21:00')]
        snapshot = copy.deepcopy((existing, requests, capacities, blocks))
        result = allocate(existing, requests, capacities, blocks)
        self.assertEqual((existing, requests, capacities, blocks), snapshot)
        result['schedule'][0]['room'] = 'mutated'
        self.assertEqual(existing[0]['room'], ' Annex ')

    def test_empty_input(self):
        self.assertEqual(allocate([], [], {}), {'accepted': [], 'rejected': [], 'schedule': []})

if __name__ == '__main__':
    unittest.main()
