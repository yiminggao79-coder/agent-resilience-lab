import copy
import unittest
from discipline import review


def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)


def by_team(rows):
    return {row['team']: row for row in rows}


class DisciplineReview(unittest.TestCase):
    def test_sequential_replace_and_void(self):
        rows = [match('1', 'A', 'B', 1, 0), match('2', 'C', 'D', 2, 2)]
        actions = [
            {'kind': 'replace', 'match_id': '1', 'home_goals': 0, 'away_goals': 4},
            {'kind': 'void', 'match_id': '2'},
        ]
        result = review(rows, actions, ['A', 'B', 'C', 'D'])
        self.assertEqual([row['id'] for row in result['matches']], ['1'])
        self.assertEqual(result['matches'][0], dict(id='1', home='A', away='B',
                                                    home_goals=0, away_goals=4))
        standings = by_team(result['standings'])
        self.assertEqual((standings['A']['points'], standings['B']['points']), (0, 3))
        self.assertEqual((standings['C']['played'], standings['C']['points']), (0, 0))
        self.assertEqual(standings['A']['difference'], -4)

    def test_replace_then_penalty_accumulates_negative(self):
        rows = [match('1', 'A', 'B', 2, 1)]
        actions = [
            {'kind': 'penalty', 'team': 'A', 'points': 0},
            {'kind': 'penalty', 'team': 'A', 'points': 3},
            {'kind': 'penalty', 'team': 'A', 'points': 5},
        ]
        result = review(rows, actions)
        standings = by_team(result['standings'])
        self.assertEqual(standings['A']['points'], -5)
        self.assertEqual(result['penalties'], {'A': 8, 'B': 0})
        self.assertEqual(standings['B']['points'], 0)

    def test_penalty_on_voided_team_and_unplayed(self):
        result = review([match('1', 'A', 'B', 3, 0)],
                        [{'kind': 'void', 'match_id': '1'},
                         {'kind': 'penalty', 'team': 'B', 'points': 2}],
                        ['X'])
        standings = by_team(result['standings'])
        self.assertEqual(standings['B']['points'], -2)
        self.assertEqual(standings['X']['played'], 0)
        self.assertEqual(result['penalties'], {'A': 0, 'B': 2, 'X': 0})
        self.assertEqual(result['matches'], [])

    def test_original_order_preserved_after_replacement(self):
        rows = [match('1', 'A', 'B', 1, 1), match('2', 'B', 'C', 0, 2),
                match('3', 'C', 'A', 3, 0)]
        result = review(rows, [{'kind': 'replace', 'match_id': '2',
                                'home_goals': 5, 'away_goals': 0}])
        self.assertEqual([row['id'] for row in result['matches']], ['1', '2', '3'])
        self.assertEqual(result['matches'][1]['home_goals'], 5)

    def test_errors_leave_input_untouched(self):
        rows = [match('1', 'A', 'B', 1, 0)]
        cases = [
            [{'kind': 'void', 'match_id': '1'}, {'kind': 'void', 'match_id': '1'}],
            [{'kind': 'void', 'match_id': '1'},
             {'kind': 'replace', 'match_id': '1', 'home_goals': 1, 'away_goals': 0}],
            [{'kind': 'void', 'match_id': 'zzz'}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': -1, 'away_goals': 0}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': True, 'away_goals': 0}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': 1}],
            [{'kind': 'replace', 'home_goals': 1, 'away_goals': 0}],
            [{'kind': 'penalty', 'team': 'Q', 'points': 1}],
            [{'kind': 'penalty', 'team': 'A', 'points': -1}],
            [{'kind': 'penalty', 'team': 'A', 'points': 1.5}],
            [{'kind': 'penalty', 'team': 'A'}],
            [{'kind': 'explode', 'match_id': '1'}],
            [{'kind': 'penalty', 'team': 'A ', 'points': 1}],
            ['not a dict'],
        ]
        for actions in cases:
            original_rows = copy.deepcopy(rows)
            original_actions = copy.deepcopy(actions)
            with self.assertRaises(ValueError):
                review(rows, actions, ['A', 'B'])
            self.assertEqual(rows, original_rows)
            self.assertEqual(actions, original_actions)

    def test_bad_canonical_input(self):
        with self.assertRaises(ValueError):
            review([match('1', 'A', 'A')], [])
        with self.assertRaises(ValueError):
            review([match('1', 'A', 'B'), match('1', 'C', 'D')], [])
        with self.assertRaises(ValueError):
            review([], [], ['A', 'A'])
        with self.assertRaises(ValueError):
            review([], [], ['A', ''])

    def test_registered_team_zero_row_and_sorting(self):
        rows = [match('1', 'A', 'B', 1, 0), match('2', 'B', 'C', 0, 2)]
        result = review(rows, [], ['Zed'])
        self.assertEqual([row['team'] for row in result['standings']],
                         ['C', 'A', 'Zed', 'B'])
        self.assertEqual(by_team(result['standings'])['Zed']['difference'], 0)


if __name__ == '__main__':
    unittest.main()
