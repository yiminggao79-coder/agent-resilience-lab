"""Tests for group qualification."""
import copy
import unittest

import qualification


def match(identity, home, away, home_goals=0, away_goals=0):
    return {'id': identity, 'home': home, 'away': away,
            'home_goals': home_goals, 'away_goals': away_goals}


class QualificationTests(unittest.TestCase):
    def test_cross_group_matches_are_ignored(self):
        rows = [match('1', 'A1', 'B1', 5, 0), match('2', 'A2', 'A1', 1, 0)]
        groups = {'A': ['A1', 'A2'], 'B': ['B1']}
        out = qualification.select(rows, [], groups, slots=1, wildcards=0)
        a_rows = {row['team']: row for row in out['groups']['A']}
        self.assertEqual(a_rows['A1']['played'], 1)
        self.assertEqual(a_rows['A1']['points'], 0)
        self.assertEqual(a_rows['A1']['goals_for'], 0)
        self.assertEqual(a_rows['A2']['points'], 3)
        self.assertEqual(out['automatic'], ['A2', 'B1'])
        self.assertEqual(out['wildcards'], [])
        self.assertEqual(out['qualified'], ['A2', 'B1'])

    def test_penalty_changes_qualifier(self):
        rows = [match('1', 'A1', 'A2', 1, 0), match('2', 'A3', 'A2', 1, 0)]
        groups = {'A': ['A1', 'A2', 'A3']}
        actions = [{'kind': 'penalty', 'team': 'A1', 'points': 4}]
        out = qualification.select(rows, actions, groups)
        order = [row['team'] for row in out['groups']['A']]
        self.assertEqual(order, ['A3', 'A2', 'A1'])
        self.assertEqual(out['automatic'], ['A3'])
        self.assertEqual(out['qualified'], ['A3'])

    def test_empty_schedules_give_zero_rows(self):
        out = qualification.select([], [], {'A': ['A1', 'A2']}, slots=1, wildcards=1)
        for name in ('A1', 'A2'):
            row = [r for r in out['groups']['A'] if r['team'] == name][0]
            self.assertEqual(row['played'], 0)
            self.assertEqual(row['points'], 0)
            self.assertEqual(row['difference'], 0)
        self.assertEqual(out['automatic'], ['A1'])
        self.assertEqual(out['wildcards'], ['A2'])
        self.assertEqual(out['qualified'], ['A1', 'A2'])

    def test_zero_slots(self):
        rows = [match('1', 'A1', 'A2', 2, 0)]
        out = qualification.select(rows, [], {'A': ['A1', 'A2']}, slots=0, wildcards=0)
        self.assertEqual(out['automatic'], [])
        self.assertEqual(out['wildcards'], [])
        self.assertEqual(out['qualified'], [])

    def test_duplicate_membership_rejected(self):
        with self.assertRaises(ValueError):
            qualification.select([], [], {'A': ['T'], 'B': ['T']})
        with self.assertRaises(ValueError):
            qualification.select([], [], {'A': ['T', 'T']})

    def test_voided_match_still_requires_assignment(self):
        rows = [match('1', 'A1', 'X1', 1, 0)]
        actions = [{'kind': 'void', 'match_id': '1'}]
        with self.assertRaises(ValueError):
            qualification.select(rows, actions, {'A': ['A1']})

    def test_invalid_slots(self):
        groups = {'A': ['A1']}
        for bad in (True, -1, 1.0, '1'):
            with self.assertRaises(ValueError):
                qualification.select([], [], groups, slots=bad)
        with self.assertRaises(ValueError):
            qualification.select([], [], groups, wildcards=False)
        with self.assertRaises(ValueError):
            qualification.select([], [], {})

    def test_wildcards_rank_remaining_teams_together(self):
        rows = [match('1', 'A1', 'A2', 3, 0),
                match('2', 'B1', 'B2', 1, 0),
                match('3', 'B1', 'B2', 1, 0)]
        groups = {'A': ['A1', 'A2'], 'B': ['B1', 'B2']}
        out = qualification.select(rows, [], groups, slots=1, wildcards=2)
        self.assertEqual(out['automatic'], ['A1', 'B1'])
        self.assertEqual(out['wildcards'], ['B2', 'A2'])
        self.assertEqual(out['qualified'], ['A1', 'A2', 'B1', 'B2'])

    def test_outputs_and_inputs_preserved(self):
        rows = [match('1', 'A1', 'A2', 1, 0)]
        actions = [{'kind': 'replace', 'match_id': '1', 'home_goals': 0, 'away_goals': 0}]
        groups = {'A': ['A1', 'A2']}
        before = copy.deepcopy((rows, actions, groups))
        out = qualification.select(rows, actions, groups)
        self.assertEqual((rows, actions, groups), before)
        self.assertEqual(sorted(out), ['automatic', 'groups', 'qualified', 'wildcards'])
        self.assertEqual(list(out['groups']), ['A'])

    def test_bad_actions_raise_value_error(self):
        rows = [match('1', 'A1', 'A2', 1, 0)]
        groups = {'A': ['A1', 'A2']}
        cases = [
            [{'kind': 'void', 'match_id': '1'}, {'kind': 'replace', 'match_id': '1',
                                                'home_goals': 1, 'away_goals': 1}],
            [{'kind': 'penalty', 'team': 'Z', 'points': 1}],
            [{'kind': 'bogus'}],
        ]
        for actions in cases:
            before = copy.deepcopy(actions)
            with self.assertRaises(ValueError):
                qualification.select(rows, actions, groups)
            self.assertEqual(actions, before)


if __name__ == '__main__':
    unittest.main()
