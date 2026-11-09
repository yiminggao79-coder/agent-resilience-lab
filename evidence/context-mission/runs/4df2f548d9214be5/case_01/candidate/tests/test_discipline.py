import copy
import unittest
from discipline import review

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

def check_invariants(case, rows):
    for row in rows:
        case.assertEqual(row['played'], row['won'] + row['drawn'] + row['lost'])
        case.assertEqual(row['difference'], row['goals_for'] - row['goals_against'])

class DisciplineTests(unittest.TestCase):
    def test_sequential_replace_and_void(self):
        rows = [match('a', 'A', 'B', 1, 0), match('b', 'B', 'C', 0, 0),
                match('c', 'C', 'A', 2, 0)]
        result = review(rows, [
            {'kind': 'replace', 'match_id': 'a', 'home_goals': 0, 'away_goals': 3},
            {'kind': 'replace', 'match_id': 'a', 'home_goals': 2, 'away_goals': 2},
            {'kind': 'void', 'match_id': 'b'},
            {'kind': 'replace', 'match_id': 'c', 'home_goals': 5, 'away_goals': 5},
        ])
        self.assertEqual([(m['id'], m['home_goals'], m['away_goals']) for m in result['matches']],
                         [('a', 2, 2), ('c', 5, 5)])
        by_team = {r['team']: r for r in result['standings']}
        self.assertEqual(by_team['A']['points'], 1)
        self.assertEqual(by_team['B']['points'], 1)
        self.assertEqual(by_team['C']['points'], 1)
        self.assertEqual(by_team['B']['played'], 1)
        self.assertEqual([r['team'] for r in result['standings']], ['C', 'A', 'B'])
        check_invariants(self, result['standings'])

    def test_void_all_matches_keeps_participants(self):
        rows = [match('a', 'A', 'B'), match('b', 'C', 'D')]
        result = review(rows, [{'kind': 'void', 'match_id': 'a'},
                               {'kind': 'void', 'match_id': 'b'}])
        self.assertEqual(result['matches'], [])
        self.assertEqual([r['team'] for r in result['standings']], ['A', 'B', 'C', 'D'])
        for row in result['standings']:
            self.assertEqual((row['played'], row['points']), (0, 0))
        self.assertEqual(result['penalties'], {'A': 0, 'B': 0, 'C': 0, 'D': 0})

    def test_repeated_penalties_accumulate(self):
        rows = [match('a', 'A', 'B', 2, 0)]
        result = review(rows, [
            {'kind': 'penalty', 'team': 'A', 'points': 2},
            {'kind': 'penalty', 'team': 'A', 'points': 4},
            {'kind': 'penalty', 'team': 'B', 'points': 1},
        ])
        self.assertEqual(result['penalties'], {'A': 6, 'B': 1})
        by_team = {r['team']: r for r in result['standings']}
        self.assertEqual(by_team['A']['points'], 3 - 6)
        self.assertEqual(by_team['A']['won'], 1)
        self.assertEqual(by_team['B']['points'], -1)
        self.assertEqual([r['team'] for r in result['standings']], ['B', 'A'])

    def test_unplayed_registered_and_penalised(self):
        result = review([match('a', 'A', 'B')], [
            {'kind': 'penalty', 'team': 'Z', 'points': 3}], ['Z', 'Y'])
        by_team = {r['team']: r for r in result['standings']}
        self.assertEqual((by_team['Z']['played'], by_team['Z']['points']), (0, -3))
        self.assertEqual((by_team['Y']['played'], by_team['Y']['points']), (0, 0))
        self.assertEqual(result['penalties'], {'A': 0, 'B': 0, 'Y': 0, 'Z': 3})
        self.assertEqual(sorted(result['penalties']), list(result['penalties']))

    def test_inputs_are_not_mutated(self):
        rows = [match('a', 'A', 'B'), match('b', 'B', 'C', 0, 0)]
        actions = [{'kind': 'void', 'match_id': 'a'},
                   {'kind': 'penalty', 'team': 'C', 'points': 1}]
        teams = ['C', 'Z']
        before = copy.deepcopy((rows, actions, teams))
        result = review(rows, actions, teams)
        result['matches'].clear()
        result['standings'].clear()
        self.assertEqual((rows, actions, teams), before)

    def test_errors_leave_input_untouched(self):
        cases = [
            {'kind': 'nope', 'match_id': 'a'},
            {'kind': 'void'},
            {'kind': 'void', 'match_id': 'zz'},
            {'kind': 'void', 'match_id': 'a'},
            {'kind': 'replace', 'match_id': 'b', 'home_goals': -1, 'away_goals': 0},
            {'kind': 'replace', 'match_id': 'b', 'home_goals': True, 'away_goals': 0},
            {'kind': 'replace', 'match_id': 'b', 'away_goals': 1},
            {'kind': 'penalty', 'team': 'Q', 'points': 1},
            {'kind': 'penalty', 'team': 'A', 'points': -1},
            {'kind': 'penalty', 'team': 'A', 'points': True},
            {'kind': 'penalty', 'team': ' A', 'points': 1},
            {'kind': 'penalty', 'points': 1},
        ]
        for bad in cases:
            rows = [match('a', 'A', 'B'), match('b', 'B', 'C', 0, 0)]
            actions = [{'kind': 'replace', 'match_id': 'b', 'home_goals': 4, 'away_goals': 4}]
            if not (bad['kind'] == 'void' and bad.get('match_id') == 'a'):
                actions.append(bad)
            teams = ['C']
            before = copy.deepcopy((rows, actions, teams))
            with self.assertRaises(ValueError):
                review(rows, actions, teams)
            self.assertEqual((rows, actions, teams), before)

    def test_match_validation_preserved(self):
        with self.assertRaises(ValueError):
            review([match('a'), match('a', 'C', 'D')], [])
        with self.assertRaises(ValueError):
            review([match(home=' A')], [])
        with self.assertRaises(ValueError):
            review([match(hg=True)], [])
        with self.assertRaises(ValueError):
            review([], [], ['A', 'A'])
        with self.assertRaises(ValueError):
            review([], [], ['A '])

if __name__ == '__main__':
    unittest.main()
