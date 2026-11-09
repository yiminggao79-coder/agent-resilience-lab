import copy
import unittest
from results import matches
from scoring import table
from ordering import rank
from presentation import season

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

def by_team(rows):
    return {row['team']: row for row in rows}

class LeagueRules(unittest.TestCase):
    def test_draw_row_consistent(self):
        rows = by_team(table([match('d', 'A', 'B', 2, 2)]))
        for team in ('A', 'B'):
            row = rows[team]
            self.assertEqual((row['played'], row['won'], row['drawn'], row['lost'], row['points']),
                             (1, 0, 1, 0, 1))
            self.assertEqual((row['goals_for'], row['goals_against'], row['difference']), (2, 2, 0))

    def test_all_fields_consistent_over_mixed_season(self):
        rows = by_team(table([match('1', 'A', 'B', 3, 0),
                              match('2', 'B', 'C', 1, 1),
                              match('3', 'C', 'A', 0, 2)]))
        expected = {'A': (2, 2, 0, 0, 5, 0, 5, 6),
                    'B': (2, 0, 1, 1, 1, 4, -3, 1),
                    'C': (2, 0, 1, 1, 1, 3, -2, 1)}
        for team, values in expected.items():
            row = rows[team]
            got = (row['played'], row['won'], row['drawn'], row['lost'],
                   row['goals_for'], row['goals_against'], row['difference'], row['points'])
            self.assertEqual(got, values, team)
            self.assertEqual(row['played'], row['won'] + row['drawn'] + row['lost'])
            self.assertEqual(row['difference'], row['goals_for'] - row['goals_against'])

    def test_rank_points_then_difference_then_goals_then_name(self):
        rows = [
            dict(team='P', points=4, difference=5, goals_for=9),
            dict(team='Q', points=6, difference=1, goals_for=2),
            dict(team='A', points=4, difference=5, goals_for=9),
            dict(team='R', points=4, difference=5, goals_for=1),
            dict(team='S', points=4, difference=7, goals_for=0),
        ]
        self.assertEqual([row['team'] for row in rank(rows)], ['Q', 'S', 'A', 'P', 'R'])

    def test_unplayed_team_gets_zero_row(self):
        result = rank(table([match('x', 'A', 'B', 1, 0)], ['Z']))
        # Z (0 pts, 0 diff, 0 GF) sorts above B (-1 diff) under the required
        # points/difference/goals_for/name ordering; only the zero row is asserted.
        self.assertIn('Z', [row['team'] for row in result])
        zero = by_team(result)['Z']
        self.assertEqual((zero['played'], zero['won'], zero['drawn'], zero['lost'],
                          zero['goals_for'], zero['goals_against'], zero['difference'], zero['points']),
                         (0, 0, 0, 0, 0, 0, 0, 0))

    def test_summary_with_draws_and_registrations(self):
        summary = season([match('1', 'A', 'B', 2, 2), match('2', 'B', 'C', 0, 3)], ['D'])['summary']
        self.assertEqual(summary, {'games': 2, 'teams': 4, 'goals': 7})

    def test_no_mutation_through_season(self):
        rows = [match('1', 'A', 'B', 2, 2), match('2', 'C', 'D', 1, 3)]
        teams = ['E', 'A']
        before, teams_before = copy.deepcopy(rows), copy.deepcopy(teams)
        result = season(rows, teams)
        self.assertEqual(rows, before)
        self.assertEqual(teams, teams_before)
        result['standings'][0]['points'] = 999
        result['standings'].reverse()
        self.assertEqual(rows, before)
        self.assertEqual(teams, teams_before)

    def test_no_mutation_through_rank(self):
        rows = table([match('1', 'A', 'B', 2, 2)], ['Z'])
        before = copy.deepcopy(rows)
        out = rank(rows)
        self.assertEqual(rows, before)
        for original, ranked in zip(sorted(rows, key=lambda r: r['team']), sorted(out, key=lambda r: r['team'])):
            self.assertEqual(original, ranked)
            self.assertIsNot(original, ranked)

    def test_validation_preserved(self):
        for bad in [match(home=' A'), match(away='B '), match(home=''), match(hg=True),
                    match(ag=False), match(hg=-1), match(ag=1.0), match(identity='')]:
            with self.assertRaises(ValueError):
                table([bad])
        with self.assertRaises(ValueError):
            table([match('s', 'A', 'B'), dict(id=7, home='A', away='B', home_goals=0, away_goals=0)])
        with self.assertRaises(ValueError):
            table([], ['A', 'A'])
        with self.assertRaises(ValueError):
            table([], [' A'])
        self.assertEqual(len(matches([])), 0)

if __name__ == '__main__':
    unittest.main()
