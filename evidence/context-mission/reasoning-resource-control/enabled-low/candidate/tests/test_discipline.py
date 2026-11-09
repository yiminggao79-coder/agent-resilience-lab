import copy
import unittest
from discipline import review

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

def by_team(rows):
    return {row['team']: row for row in rows}

class Discipline(unittest.TestCase):
    def test_sequential_replace_and_void(self):
        rows = [match('1', 'A', 'B', 1, 0), match('2', 'B', 'C', 2, 2), match('3', 'C', 'A', 0, 3)]
        actions = [{'kind': 'replace', 'match_id': '1', 'home_goals': 0, 'away_goals': 1},
                   {'kind': 'void', 'match_id': '2'},
                   {'kind': 'replace', 'match_id': '3', 'home_goals': 1, 'away_goals': 1}]
        out = review(rows, actions)
        self.assertEqual([(m['id'], m['home_goals'], m['away_goals']) for m in out['matches']],
                         [('1', 0, 1), ('3', 1, 1)])
        table = by_team(out['standings'])
        self.assertEqual((table['A']['played'], table['A']['points']), (2, 1))
        self.assertEqual((table['B']['played'], table['B']['points']), (1, 3))
        self.assertEqual((table['C']['played'], table['C']['points']), (1, 1))
        self.assertEqual(sorted(out['penalties']), ['A', 'B', 'C'])
        self.assertEqual(set(out['penalties'].values()), {0})

    def test_voided_match_cannot_be_touched_again(self):
        rows = [match('1')]
        with self.assertRaises(ValueError):
            review(rows, [{'kind': 'void', 'match_id': '1'},
                          {'kind': 'void', 'match_id': '1'}])
        with self.assertRaises(ValueError):
            review(rows, [{'kind': 'void', 'match_id': '1'},
                          {'kind': 'replace', 'match_id': '1', 'home_goals': 2, 'away_goals': 2}])

    def test_repeated_penalties_accumulate_and_can_go_negative(self):
        rows = [match('1', 'A', 'B', 1, 0)]
        actions = [{'kind': 'penalty', 'team': 'B', 'points': 1},
                   {'kind': 'penalty', 'team': 'B', 'points': 2},
                   {'kind': 'penalty', 'team': 'A', 'points': 5}]
        out = review(rows, actions)
        table = by_team(out['standings'])
        self.assertEqual(table['A']['points'], -2)
        self.assertEqual(table['B']['points'], -3)
        self.assertEqual(table['B']['difference'], -1)
        self.assertEqual(out['penalties'], {'A': 5, 'B': 3})
        self.assertEqual([r['team'] for r in out['standings']], ['A', 'B'])

    def test_unplayed_participants_remain(self):
        rows = [match('1', 'A', 'B', 1, 1)]
        actions = [{'kind': 'void', 'match_id': '1'},
                   {'kind': 'penalty', 'team': 'A', 'points': 2}]
        out = review(rows, actions, ['Zed'])
        self.assertEqual(out['matches'], [])
        self.assertEqual(out['penalties'], {'A': 2, 'B': 0, 'Zed': 0})
        table = by_team(out['standings'])
        self.assertEqual(sorted(table), ['A', 'B', 'Zed'])
        for team, row in table.items():
            self.assertEqual((row['played'], row['difference']), (0, 0))
            self.assertEqual(row['points'], -out['penalties'][team])
        self.assertEqual([r['team'] for r in out['standings']], ['B', 'Zed', 'A'])

    def test_validation_errors(self):
        rows = [match('1', 'A', 'B', 1, 0)]
        bad_actions = [
            [{'kind': 'erase', 'match_id': '1'}],
            [{'kind': 'void'}],
            [{'kind': 'void', 'match_id': 'nope'}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': 1}],
            [{'kind': 'replace', 'match_id': 'nope', 'home_goals': 0, 'away_goals': 0}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': True, 'away_goals': 0}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': -1, 'away_goals': 0}],
            [{'kind': 'replace', 'match_id': '1', 'home_goals': 1.0, 'away_goals': 0}],
            [{'kind': 'penalty', 'team': 'A'}],
            [{'kind': 'penalty', 'team': 'Q', 'points': 1}],
            [{'kind': 'penalty', 'team': 'A', 'points': -1}],
            [{'kind': 'penalty', 'team': 'A', 'points': False}],
            [{'kind': 'penalty', 'team': ' A', 'points': 1}],
            [{'kind': 'penalty', 'team': 'A', 'points': 1}, 'void'],
        ]
        for actions in bad_actions:
            with self.assertRaises(ValueError):
                review(rows, actions)
        self.assertEqual(rows, [match('1', 'A', 'B', 1, 0)])

    def test_caller_objects_untouched(self):
        rows = [match('1', 'A', 'B', 3, 1)]
        actions = [{'kind': 'replace', 'match_id': '1', 'home_goals': 0, 'away_goals': 2},
                   {'kind': 'penalty', 'team': 'A', 'points': 1}]
        teams = ['A']
        rows_before, actions_before, teams_before = copy.deepcopy((rows, actions, teams))
        out = review(rows, actions, teams)
        self.assertEqual((rows, actions, teams), (rows_before, actions_before, teams_before))
        self.assertIsNot(out['matches'][0], rows[0])
        self.assertIsNot(out['standings'][0], out['matches'][0])

        failing = copy.deepcopy(actions) + [{'kind': 'void', 'match_id': 'missing'}]
        with self.assertRaises(ValueError):
            review(rows, failing, teams)
        self.assertEqual((rows, actions, teams), (rows_before, actions_before, teams_before))

if __name__ == '__main__':
    unittest.main()
