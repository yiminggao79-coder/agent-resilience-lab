import copy
import unittest
from qualification import select


def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)


def by_team(rows):
    return {row['team']: row for row in rows}


class Qualification(unittest.TestCase):
    def test_cross_group_matches_are_excluded(self):
        rows = [match('1', 'A', 'B', 1, 0),
                match('2', 'A', 'C', 5, 0),
                match('3', 'C', 'D', 2, 0)]
        groups = {'North': ['A', 'B'], 'South': ['C', 'D']}
        out = select(rows, [], groups, slots=1)
        north = by_team(out['groups']['North'])
        south = by_team(out['groups']['South'])
        self.assertEqual((north['A']['played'], north['A']['goals_for'], north['A']['points']), (1, 1, 3))
        self.assertEqual((north['B']['played'], north['B']['points']), (1, 0))
        self.assertEqual((south['C']['played'], south['C']['points']), (1, 3))
        self.assertEqual((south['D']['played'], south['D']['goals_for']), (1, 0))
        self.assertEqual(out['automatic'], ['A', 'C'])
        self.assertEqual(out['wildcards'], [])
        self.assertEqual(out['qualified'], ['A', 'C'])

    def test_penalty_changes_qualifier(self):
        rows = [match('1', 'A', 'B', 1, 0)]
        groups = {'G': ['A', 'B']}
        actions = [{'kind': 'penalty', 'team': 'A', 'points': 4}]
        out = select(rows, actions, groups, slots=1)
        self.assertEqual([row['team'] for row in out['groups']['G']], ['B', 'A'])
        self.assertEqual(by_team(out['groups']['G'])['A']['points'], -1)
        self.assertEqual(out['automatic'], ['B'])

    def test_empty_schedules_and_extra_slots(self):
        groups = {'X': ['B', 'A'], 'Y': ['C']}
        out = select([], [], groups, slots=2, wildcards=1)
        self.assertEqual([row['team'] for row in out['groups']['X']], ['A', 'B'])
        self.assertEqual([row['team'] for row in out['groups']['Y']], ['C'])
        self.assertEqual(out['automatic'], ['A', 'B', 'C'])
        self.assertEqual(out['wildcards'], [])
        self.assertEqual(out['qualified'], ['A', 'B', 'C'])
        for rows in out['groups'].values():
            for row in rows:
                self.assertEqual((row['played'], row['points'], row['difference']), (0, 0, 0))

    def test_zero_slots_uses_wildcards(self):
        groups = {'G': ['A', 'B', 'C']}
        rows = [match('1', 'A', 'B', 2, 0), match('2', 'B', 'C', 1, 0)]
        out = select(rows, [], groups, slots=0, wildcards=2)
        self.assertEqual(out['automatic'], [])
        self.assertEqual(out['wildcards'], ['A', 'B'])
        self.assertEqual(out['qualified'], ['A', 'B'])

    def test_duplicate_membership(self):
        bad_groups = [
            {'G': ['A', 'A']},
            {'G': ['A'], 'H': ['A']},
            {'': ['A']},
            {'G': []},
            {'G': [' A']},
            {},
        ]
        for groups in bad_groups:
            with self.assertRaises(ValueError):
                select([], [], groups)

    def test_unassigned_match_team_and_invalid_counts(self):
        with self.assertRaises(ValueError):
            select([match('1', 'A', 'B')], [], {'G': ['A']})
        for slots in (True, -1, 1.0):
            with self.assertRaises(ValueError):
                select([], [], {'G': ['A']}, slots=slots)
        for wildcards in (False, -1, 1.0):
            with self.assertRaises(ValueError):
                select([], [], {'G': ['A']}, wildcards=wildcards)

    def test_inputs_untouched_and_errors_preserve_input(self):
        rows = [match('1', 'A', 'B', 3, 1), match('2', 'C', 'D', 2, 2)]
        actions = [{'kind': 'replace', 'match_id': '1', 'home_goals': 0, 'away_goals': 1},
                   {'kind': 'penalty', 'team': 'A', 'points': 1}]
        groups = {'One': ['A', 'B'], 'Two': ['C', 'D']}
        before = copy.deepcopy((rows, actions, groups))
        out = select(rows, actions, groups, slots=1, wildcards=1)
        self.assertEqual((rows, actions, groups), before)
        out['groups']['One'][0]['points'] = 999
        out['groups']['One'].reverse()
        self.assertEqual((rows, actions, groups), before)

        failing = copy.deepcopy(actions) + [{'kind': 'void', 'match_id': 'missing'}]
        with self.assertRaises(ValueError):
            select(rows, failing, groups)
        self.assertEqual((rows, actions, groups), before)

    def test_voided_match_teams_remain_grouped(self):
        rows = [match('1', 'A', 'B', 1, 0)]
        groups = {'G': ['A', 'B'], 'H': ['C']}
        out = select(rows, [{'kind': 'void', 'match_id': '1'}], groups, slots=1, wildcards=1)
        self.assertEqual([row['team'] for row in out['groups']['G']], ['A', 'B'])
        self.assertEqual(out['automatic'], ['A', 'C'])
        self.assertEqual(out['wildcards'], ['B'])
        self.assertEqual(out['qualified'], ['A', 'B', 'C'])


if __name__ == '__main__':
    unittest.main()
