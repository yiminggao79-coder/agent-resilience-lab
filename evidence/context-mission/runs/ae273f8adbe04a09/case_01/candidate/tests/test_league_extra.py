import copy
import unittest
from results import matches, participants
from scoring import table
from ordering import rank
from presentation import season

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

def by_team(rows):
    return {row['team']: row for row in rows}

class ScoringRules(unittest.TestCase):
    def test_draw_points_and_totals(self):
        rows = by_team(table([match(hg=2, ag=2)]))
        for team in ('A', 'B'):
            self.assertEqual(rows[team]['played'], 1)
            self.assertEqual(rows[team]['drawn'], 1)
            self.assertEqual(rows[team]['points'], 1)
            self.assertEqual(rows[team]['won'], 0)
            self.assertEqual(rows[team]['lost'], 0)
            self.assertEqual(rows[team]['goals_for'], 2)
            self.assertEqual(rows[team]['goals_against'], 2)
            self.assertEqual(rows[team]['difference'], 0)

    def test_win_loss_points(self):
        rows = by_team(table([match(hg=3, ag=1)]))
        self.assertEqual((rows['A']['points'], rows['A']['won'], rows['A']['difference']),
                         (3, 1, 2))
        self.assertEqual((rows['B']['points'], rows['B']['lost'], rows['B']['difference']),
                         (0, 1, -2))

    def test_mixed_season_consistency(self):
        rows = table([
            match('1', 'A', 'B', 2, 2),
            match('2', 'A', 'C', 1, 0),
            match('3', 'B', 'C', 0, 1),
            match('4', 'A', 'B', 0, 3),
        ])
        for row in rows:
            self.assertEqual(row['played'], row['won'] + row['drawn'] + row['lost'])
            self.assertEqual(row['difference'], row['goals_for'] - row['goals_against'])
            self.assertEqual(row['points'], 3 * row['won'] + row['drawn'])
        totals = by_team(rows)
        self.assertEqual(totals['A']['points'], 4)   # win, draw, loss
        self.assertEqual(totals['B']['points'], 4)   # win, draw, loss
        self.assertEqual(totals['C']['points'], 3)   # win, loss
        self.assertEqual(sum(r['played'] for r in rows), 8)
        self.assertEqual(sum(r['goals_for'] for r in rows), 9)

    def test_zero_rows_for_unplayed_teams(self):
        rows = by_team(table([match()], ['Z', 'Y']))
        for team in ('Z', 'Y'):
            self.assertEqual(rows[team], dict(team=team, played=0, won=0, drawn=0,
                                              lost=0, goals_for=0, goals_against=0,
                                              difference=0, points=0))

class RankingRules(unittest.TestCase):
    def test_difference_before_goals_for(self):
        rows = [match('x', 'A', 'B', 2, 0), match('y', 'C', 'D', 4, 3)]
        self.assertEqual([r['team'] for r in rank(table(rows))], ['A', 'C', 'D', 'B'])

    def test_points_before_difference(self):
        rows = [match('x', 'A', 'B', 1, 0), match('y', 'C', 'D', 5, 0),
                match('z', 'C', 'D', 5, 0)]
        ranked = rank(table(rows))
        self.assertEqual([r['team'] for r in ranked], ['C', 'A', 'B', 'D'])
        self.assertEqual(ranked[0]['points'], 6)
        self.assertEqual(ranked[1]['points'], 3)

    def test_name_breaks_full_tie(self):
        rows = [match('x', 'B', 'D', 2, 0), match('y', 'A', 'C', 2, 0)]
        self.assertEqual([r['team'] for r in rank(table(rows))], ['A', 'B', 'C', 'D'])

    def test_rank_returns_independent_copies(self):
        rows = table([match()])
        before = copy.deepcopy(rows)
        result = rank(rows)
        result[0]['team'] = 'changed'
        result[0]['played'] = 99
        self.assertEqual(rows, before)
        self.assertIsNot(result[0], rows[0])

    def test_rank_does_not_reorder_caller(self):
        rows = table([match('x', 'A', 'B', 1, 0), match('y', 'C', 'D', 5, 0)])
        before = copy.deepcopy(rows)
        rank(rows)
        self.assertEqual(rows, before)

    def test_rank_preserves_duplicate_key_rows(self):
        rows = [dict(team='A', points=1, difference=0, goals_for=0),
                dict(team='A', points=1, difference=0, goals_for=0)]
        self.assertEqual(len(rank(rows)), 2)

class ImmutabilityAndValidation(unittest.TestCase):
    def test_table_preserves_inputs(self):
        rows = [match('x', 'A', 'B', 2, 2), match('y', 'A', 'C', 0, 1)]
        teams = ['Z', 'C']
        before_rows, before_teams = copy.deepcopy(rows), list(teams)
        result = table(rows, teams)
        result[0]['points'] = 999
        self.assertEqual(rows, before_rows)
        self.assertEqual(teams, before_teams)

    def test_season_preserves_inputs(self):
        payload = {'matches': [match('x', 'A', 'B', 1, 1)], 'teams': ['Q']}
        before = copy.deepcopy(payload)
        out = season(payload['matches'], payload['teams'])
        out['standings'][0]['points'] = 42
        self.assertEqual(payload, before)
        self.assertEqual(out['summary'], {'games': 1, 'teams': 3, 'goals': 2})

    def test_summary_counts(self):
        rows = [match('x', 'A', 'B', 2, 2), match('y', 'C', 'A', 0, 1)]
        self.assertEqual(season(rows, ['Z'])['summary'],
                         {'games': 2, 'teams': 4, 'goals': 5})

    def test_invalid_goals(self):
        for value in (True, False, -1, 1.5, '1', None):
            with self.assertRaises(ValueError):
                table([match(hg=value)])
            with self.assertRaises(ValueError):
                table([match(ag=value)])

    def test_invalid_names(self):
        for bad in ('', ' A', 'A ', '   ', 7, None, True):
            with self.assertRaises(ValueError):
                participants([], [bad])
            with self.assertRaises(ValueError):
                matches([match(home=bad)])
            with self.assertRaises(ValueError):
                matches([match(away=bad)])

    def test_duplicate_teams_and_matches(self):
        rows = [match('a'), match('b', 'C', 'D')]
        with self.assertRaises(ValueError):
            table(rows, ['Q', 'Q'])
        with self.assertRaises(ValueError):
            table([match('a'), match('a', 'C', 'D')])
        with self.assertRaises(ValueError):
            table([match(home='A', away='A')])
        with self.assertRaises(ValueError):
            table([match(), match('b', 'C', 'C')])

    def test_missing_and_non_dict_rows(self):
        with self.assertRaises(ValueError):
            table([{'id': 'a', 'home': 'A', 'away': 'B'}])
        with self.assertRaises(ValueError):
            table(['not a dict'])
        with self.assertRaises(ValueError):
            table([{'id': '', 'home': 'A', 'away': 'B', 'home_goals': 1, 'away_goals': 0}])

if __name__ == '__main__':
    unittest.main()
