import copy
import unittest
from changes import apply_groups

def row(identity, start, end, room='main', day='2027-05-10', attendees=1, **extra):
    result = dict(id=identity, room=room, day=day, start=start, end=end, attendees=attendees)
    result.update(extra)
    return result

def block(room, start, end, day='2027-05-10'):
    return dict(room=room, day=day, start=start, end=end)

def group(identity, remove=(), add=()):
    return {'id': identity, 'remove': list(remove), 'add': [dict(item) for item in add]}

class Basics(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(apply_groups([], [], {}), {'accepted': [], 'rejected': [], 'schedule': []})

    def test_defaults_are_optional(self):
        result = apply_groups([], [{'id': 'g'}], {})
        self.assertEqual(result['accepted'], ['g'])

    def test_move_reservation_atomically(self):
        existing = [row('a', '08:00', '09:00')]
        g = group('move', ['a'], [row('a', '10:00', '11:00')])
        result = apply_groups(existing, [g], {})
        self.assertEqual(result['accepted'], ['move'])
        self.assertEqual(result['schedule'],
            [{'id': 'a', 'room': 'main', 'day': '2027-05-10', 'start': 600, 'end': 660, 'attendees': 1}])

    def test_schedule_sorted_canonical(self):
        existing = [row('z', '09:00', '10:00', ' MAIN '), row('a', '08:00', '09:00', 'annex')]
        result = apply_groups(existing, [group('g', add=[row('b', '07:00', '08:00')])], {})
        # Sorted by room, day, start, id: annex 'a', then main 'b' at 07:00, then 'z'.
        self.assertEqual([item['id'] for item in result['schedule']], ['a', 'b', 'z'])
        self.assertEqual([item['room'] for item in result['schedule']], ['annex', 'main', 'main'])
        for item in result['schedule']:
            self.assertEqual(sorted(item), ['attendees', 'day', 'end', 'id', 'room', 'start'])

class AtomicRollback(unittest.TestCase):
    def test_missing_removal_keeps_schedule(self):
        existing = [row('a', '08:00', '09:00')]
        result = apply_groups(existing, [group('g', ['a', 'nope'], [row('b', '08:00', '09:00', room='annex')])], {})
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'missing:nope'}])
        self.assertEqual([item['id'] for item in result['schedule']], ['a'])

    def test_first_missing_in_remove_order(self):
        result = apply_groups([row('a', '08:00', '09:00')],
                              [group('g', ['q', 'a', 'p'])], {})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'missing:q'}])

    def test_capacity_rejection_rolls_back_all(self):
        existing = [row('e', '08:00', '09:00')]
        g = group('g', add=[row('n1', '08:00', '09:00', room='annex'),
                            row('n2', '08:00', '09:00')])
        result = apply_groups(existing, [g], {})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'capacity'}])
        self.assertEqual([item['id'] for item in result['schedule']], ['e'])

    def test_removal_not_applied_when_addition_rejected(self):
        # Narrow capacity rejects the main-room addition; the annex addition and
        # the removal of 'e' must not survive on their own.
        existing = [row('e', '08:00', '09:00')]
        g = group('g', ['e'], [row('n', '08:00', '09:00', room='annex'),
                               row('m', '08:00', '09:00', room='main', attendees=2)])
        result = apply_groups(existing, [g], {'main': 1})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'capacity'}])
        self.assertEqual([item['id'] for item in result['schedule']], ['e'])

    def test_rejected_group_does_not_hide_later_change(self):
        # Capacity is measured on the post-removal schedule: moving 'e' onto the
        # crowded slot fails for g1, so g2 must still find and remove 'e'.
        existing = [row('e', '08:00', '09:00'), row('a', '10:00', '10:30')]
        result = apply_groups(existing,
                              [group('g1', ['e'], [row('e', '10:00', '10:30')]),
                               group('g2', ['e'])], {'main': 1})
        self.assertEqual(result['rejected'], [{'id': 'g1', 'reason': 'capacity'}])
        self.assertEqual(result['accepted'], ['g2'])
        self.assertEqual([item['id'] for item in result['schedule']], ['a'])

class SequentialVisibility(unittest.TestCase):
    def test_next_group_sees_accepted_change(self):
        result = apply_groups([row('e', '08:00', '09:00')],
                              [group('g1', ['e'], [row('e', '10:00', '11:00')]),
                               group('g2', ['e'])], {})
        self.assertEqual(result['accepted'], ['g1', 'g2'])
        self.assertEqual(result['schedule'], [])

    def test_next_group_does_not_see_rejected_change(self):
        # Capacity is measured on the post-removal schedule, so g1 moves 'e' to a
        # crowded slot and fails; g2 must still find the original 'e'.
        result = apply_groups([row('e', '08:00', '09:00'), row('a', '10:00', '10:30')],
                              [group('g1', ['e'], [row('e', '10:00', '10:30')]),
                               group('g2', ['e'])], {'main': 2})
        self.assertEqual(result['rejected'], [{'id': 'g1', 'reason': 'capacity'}])
        self.assertEqual(result['accepted'], ['g2'])

    def test_rejected_group_does_not_free_its_id(self):
        # g1 re-adds the retained 'e' without removing it: duplicate, not capacity.
        result = apply_groups([row('e', '08:00', '09:00')],
                              [group('g1', [], [row('e', '08:00', '09:00', attendees=5)]),
                               group('g2', add=[row('e', '12:00', '13:00')])], {})
        self.assertEqual(result['rejected'], [{'id': 'g1', 'reason': 'duplicate:e'},
                                              {'id': 'g2', 'reason': 'duplicate:e'}])
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['schedule'],
            [{'id': 'e', 'room': 'main', 'day': '2027-05-10', 'start': 480, 'end': 540, 'attendees': 1}])

class Duplicates(unittest.TestCase):
    def test_readd_own_removed_id_allowed(self):
        result = apply_groups([row('a', '08:00', '09:00')],
                              [group('g', ['a'], [row('a', '09:00', '10:00')])], {})
        self.assertEqual(result['accepted'], ['g'])
        self.assertEqual(result['schedule'][0]['start'], 540)

    def test_duplicate_against_retained(self):
        result = apply_groups([row('a', '08:00', '09:00'), row('b', '10:00', '11:00')],
                              [group('g', add=[row('c', '12:00', '13:00'), row('b', '12:00', '13:00', room='annex')])], {})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'duplicate:b'}])
        self.assertEqual([item['id'] for item in result['schedule']], ['a', 'b'])

    def test_duplicate_within_add_order(self):
        result = apply_groups([], [group('g', add=[row('n', '08:00', '09:00', room='annex'),
                                                   row('n', '08:00', '09:00')])], {})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'duplicate:n'}])

    def test_removal_duplicate_within_group_is_value_error(self):
        with self.assertRaises(ValueError):
            apply_groups([row('a', '08:00', '09:00')], [group('g', ['a', 'a'])], {})

    def test_missing_beats_duplicate(self):
        result = apply_groups([row('a', '08:00', '09:00')],
                              [group('g', ['gone'], [row('a', '12:00', '13:00')])], {})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'missing:gone'}])

class BlocksAndCapacity(unittest.TestCase):
    def test_blocked(self):
        result = apply_groups([], [group('g', add=[row('a', '08:00', '09:00')])], {},
                              [block(' MAIN ', '08:30', '09:30')])
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'blocked'}])

    def test_block_endpoint_touch_free(self):
        result = apply_groups([], [group('g', add=[row('a', '08:00', '09:00')])], {},
                              [block('main', '09:00', '10:00')])
        self.assertEqual(result['accepted'], ['g'])

    def test_blocked_beats_capacity(self):
        existing = [row('e', '08:00', '09:00')]
        result = apply_groups(existing, [group('g', add=[row('n', '08:00', '09:00')])], {},
                              [block('main', '08:30', '09:00')])
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'blocked'}])

    def test_cancellation_succeeds_despite_existing_over_capacity(self):
        existing = [row('e1', '08:00', '09:00', attendees=5),
                    row('e2', '08:00', '09:00', attendees=5)]
        result = apply_groups(existing, [group('g', ['e1'])], {'main': 2})
        self.assertEqual(result['accepted'], ['g'])
        self.assertEqual([item['id'] for item in result['schedule']], ['e2'])

    def test_noop_group_succeeds_when_over_capacity(self):
        existing = [row('e1', '08:00', '09:00', attendees=5),
                    row('e2', '08:00', '09:00', attendees=5)]
        result = apply_groups(existing, [group('g')], {'main': 2})
        self.assertEqual(result['accepted'], ['g'])

    def test_capacity_checks_use_custom_and_default(self):
        existing = [row('e', '08:00', '09:00', attendees=2)]
        swap = group('swap', ['e'], [row('n', '08:00', '09:00', attendees=2)])
        self.assertEqual(apply_groups(existing, [swap], {'MAIN': 2})['accepted'], ['swap'])
        hot = group('hot', [], [row('n', '08:00', '09:00', attendees=2)])
        self.assertEqual(apply_groups(existing, [hot], {'main': 4})['accepted'], ['hot'])
        self.assertEqual(apply_groups(existing, [hot], {'main': 3})['rejected'],
                         [{'id': 'hot', 'reason': 'capacity'}])
        self.assertEqual(apply_groups(existing, [hot], {})['rejected'],
                         [{'id': 'hot', 'reason': 'capacity'}])

    def test_capacity_counts_whole_group(self):
        g = group('g', add=[row('n1', '08:00', '09:00'), row('n2', '09:00', '10:00'),
                            row('n3', '08:30', '09:30')])
        result = apply_groups([], [g], {'main': 2})
        self.assertEqual(result['accepted'], ['g'])
        result = apply_groups([], [g], {'main': 1})
        self.assertEqual(result['rejected'], [{'id': 'g', 'reason': 'capacity'}])

class Validation(unittest.TestCase):
    def test_malformed_groups(self):
        bad = [None, [], 'g', {}, {'id': ''}, {'id': '  '}, {'id': 1},
               {'id': 'g', 'remove': 'a'}, {'id': 'g', 'remove': [1]},
               {'id': 'g', 'remove': ['']}, {'id': 'g', 'add': {}},
               {'id': 'g', 'add': [dict(row('a', '08:00', '09:00'), attendees=0)]},
               {'id': 'g', 'extra': 1}]
        for item in bad:
            with self.assertRaises(ValueError):
                apply_groups([], [item], {})

    def test_group_collection_must_be_list(self):
        with self.assertRaises(ValueError):
            apply_groups([], {'id': 'g'}, {})

    def test_duplicate_group_ids(self):
        with self.assertRaises(ValueError):
            apply_groups([], [group('g'), group('g')], {})

    def test_bad_booking_interval_before_processing(self):
        with self.assertRaises(ValueError):
            apply_groups([row('a', '08:00', '09:00')],
                         [group('ok', ['a']), group('bad', add=[row('b', '10:00', '10:00')])], {})

    def test_bad_capacity_and_block(self):
        with self.assertRaises(ValueError):
            apply_groups([], [], {'Main': 2, ' main ': 3})
        with self.assertRaises(ValueError):
            apply_groups([], [], {}, [block('main', '10:00', '09:00')])

    def test_priority_ignored(self):
        result = apply_groups([], [group('g', add=[row('a', '08:00', '09:00', priority=9)])], {})
        self.assertEqual(result['accepted'], ['g'])
        self.assertEqual(sorted(result['schedule'][0]), ['attendees', 'day', 'end', 'id', 'room', 'start'])

class CallerObjects(unittest.TestCase):
    def test_unchanged_on_success_and_rejection(self):
        existing = [row('e', '09:00', '10:00', ' Annex ', attendees=2)]
        groups = [group('g1', ['e'], [row('n', '08:00', '09:00', ' annex ', attendees=2)]),
                  group('g2', ['absent'], [row('m', '08:00', '09:00')])]
        capacities = {'Annex': 2}
        blocks = [block('main', '20:00', '21:00')]
        snapshot = copy.deepcopy((existing, groups, capacities, blocks))
        result = apply_groups(existing, groups, capacities, blocks)
        self.assertEqual((existing, groups, capacities, blocks), snapshot)
        result['schedule'][0]['room'] = 'mutated'
        self.assertEqual(existing[0]['room'], ' Annex ')

    def test_unchanged_on_validation_failure(self):
        existing = [row('e', '09:00', '10:00')]
        groups = [group('g', ['e']), group('bad', add=[dict(row('x', '08:00', '09:00'), day='nope')])]
        capacities = {'main': 1}
        blocks = [block('main', '20:00', '21:00')]
        snapshot = copy.deepcopy((existing, groups, capacities, blocks))
        with self.assertRaises(ValueError):
            apply_groups(existing, groups, capacities, blocks)
        self.assertEqual((existing, groups, capacities, blocks), snapshot)

    def test_missing_optional_arguments(self):
        result = apply_groups([row('a', '08:00', '09:00')], [group('g', ['a'])], {})
        self.assertEqual(result['schedule'], [])

if __name__ == '__main__':
    unittest.main()
