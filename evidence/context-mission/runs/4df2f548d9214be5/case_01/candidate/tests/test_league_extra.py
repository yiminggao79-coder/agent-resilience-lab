import copy
import unittest
from results import matches, participants
from scoring import table
from ordering import rank
from presentation import season

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

def check_invariants(case, rows):
    for row in rows:
        case.assertEqual(row['played'], row['won'] + row['drawn'] + row['lost'])
        case.assertEqual(row['points'], 3 * row['won'] + row['drawn'])
        case.assertEqual(row['difference'], row['goals_for'] - row['goals_against'])

class ScoringTests(unittest.TestCase):
    def test_draw_goal_less_match(self):
        rows = table([match(hg=0, ag=0)])
        self.assertEqual([(r['points'], r['drawn']) for r in rows], [(1, 1), (1, 1)])
        check_invariants(self, rows)

    def test_mixed_results_invariants(self):
        rows = table([match('a', 'A', 'B', 2, 1), match('b', 'B', 'C', 0, 0),
                      match('c', 'C', 'A', 3, 0), match('d', 'A', 'C', 1, 1)])
        check_invariants(self, rows)
        by_team = {r['team']: r for r in rows}
        self.assertEqual(by_team['A']['points'], 3 + 1)   # win + draw
        self.assertEqual(by_team['B']['points'], 0 + 1)   # loss + draw
        self.assertEqual(by_team['C']['points'], 1 + 3 + 1)
        self.assertEqual(by_team['A']['played'], 3)

    def test_team_count_and_goals_consistent(self):
        total = sum(r['goals_for'] for r in table([match('a', 'A', 'B', 2, 2),
                                                   match('b', 'C', 'D', 1, 0)]))
        self.assertEqual(total, 5)

    def test_zero_rows_for_all_registered(self):
        rows = table([], ['Z', 'A', 'M'])
        self.assertEqual([r['team'] for r in rows], ['A', 'M', 'Z'])
        for r in rows:
            self.assertEqual((r['played'], r['won'], r['drawn'], r['lost'], r['goals_for'],
                              r['goals_against'], r['difference'], r['points']), (0,) * 8)
        check_invariants(self, rows)

    def test_unplayed_registered_team_kept(self):
        rows = table([match()], ['C'])
        played = [r for r in rows if r['team'] == 'C'][0]
        self.assertEqual(played['played'], 0)

class OrderingTests(unittest.TestCase):
    def test_points_first(self):
        rows = [dict(team='A', points=1, difference=9, goals_for=9),
                dict(team='B', points=3, difference=-5, goals_for=0)]
        self.assertEqual([r['team'] for r in rank(rows)], ['B', 'A'])

    def test_difference_before_goals_for(self):
        rows = [dict(team='A', points=1, difference=1, goals_for=5),
                dict(team='B', points=1, difference=2, goals_for=2)]
        self.assertEqual([r['team'] for r in rank(rows)], ['B', 'A'])

    def test_goals_for_before_name(self):
        rows = [dict(team='A', points=1, difference=0, goals_for=1),
                dict(team='B', points=1, difference=0, goals_for=2)]
        self.assertEqual([r['team'] for r in rank(rows)], ['B', 'A'])

    def test_name_ascending_last(self):
        rows = [dict(team='Z', points=1, difference=0, goals_for=1),
                dict(team='A', points=1, difference=0, goals_for=1)]
        self.assertEqual([r['team'] for r in rank(rows)], ['A', 'Z'])

    def test_full_tiebreak_from_table(self):
        rows = [match('x', 'A', 'B', 2, 0), match('y', 'C', 'D', 4, 3),
                match('z', 'E', 'F', 3, 1)]
        self.assertEqual([r['team'] for r in rank(table(rows))], ['E', 'A', 'C', 'D', 'F', 'B'])

    def test_draws_rank_above_losses(self):
        rows = [match('x', 'A', 'B', 0, 0), match('y', 'C', 'D', 1, 0)]
        order = [r['team'] for r in rank(table(rows))]
        self.assertEqual(order, ['C', 'A', 'B', 'D'])

    def test_no_caller_mutation(self):
        source = [dict(team='B', points=1, difference=0, goals_for=0),
                  dict(team='A', points=1, difference=0, goals_for=0)]
        before = copy.deepcopy(source)
        rank(source)[0]['team'] = 'changed'
        self.assertEqual(source, before)

class SummaryTests(unittest.TestCase):
    def test_summary_draws_and_empty(self):
        result = season([match('a', 'A', 'B', 2, 2)], ['C'])
        self.assertEqual(result['summary'], {'games': 1, 'teams': 3, 'goals': 4})
        self.assertEqual([r['team'] for r in result['standings']], ['A', 'B', 'C'])
        self.assertEqual(season([])['summary'], {'games': 0, 'teams': 0, 'goals': 0})

    def test_season_input_intact(self):
        payload = [match('a', 'A', 'B', 2, 2)]
        teams = ['C']
        before, before_teams = copy.deepcopy(payload), list(teams)
        season(payload, teams)
        self.assertEqual((payload, teams), (before, before_teams))

class ValidationTests(unittest.TestCase):
    def test_names(self):
        for home, away in [('', 'B'), ('A', 'B '), (' A', 'B')]:
            with self.assertRaises(ValueError):
                table([match(home=home, away=away)])

    def test_score_types(self):
        for value in [True, False, -1, 1.2, '1', None]:
            with self.assertRaises(ValueError):
                table([match(hg=value)])

    def test_ids_and_registration(self):
        with self.assertRaises(ValueError):
            matches([match('a'), match('a', 'C', 'D')])
        with self.assertRaises(ValueError):
            table([], ['A', 'A'])
        with self.assertRaises(ValueError):
            table([], ['A '])
        with self.assertRaises(ValueError):
            table([], [''])

    def test_unexpected_scores(self):
        with self.assertRaises(ValueError):
            table([match(ag=-3)])
        with self.assertRaises(ValueError):
            table([match(ag=True)])

if __name__ == '__main__':
    unittest.main()
