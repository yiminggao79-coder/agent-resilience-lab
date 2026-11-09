import copy
import unittest
from results import matches
from scoring import table
from ordering import rank
from presentation import season

def match(identity='m', home='A', away='B', hg=1, ag=0):
    return dict(id=identity, home=home, away=away, home_goals=hg, away_goals=ag)

class OriginalContracts(unittest.TestCase):
    def test_win_loss(self):
        rows = table([match()])
        self.assertEqual([(r['team'],r['points'],r['won'],r['lost']) for r in rows],
                         [('A',3,1,0),('B',0,0,1)])
    def test_draw_credit(self):
        self.assertEqual([r['points'] for r in table([match(hg=2,ag=2)])], [1,1])
    def test_difference_precedes_goals(self):
        rows = [match('x','A','B',2,0),match('y','C','D',4,3)]
        self.assertEqual([r['team'] for r in rank(table(rows))], ['A','C','D','B'])
    def test_goals_then_name(self):
        rows = [match('x','A','B',1,0),match('y','C','D',2,1)]
        self.assertEqual([r['team'] for r in rank(table(rows))], ['C','A','D','B'])
    def test_unplayed_registration(self):
        row = table([],['Z'])[0]
        self.assertEqual((row['team'],row['played'],row['points']), ('Z',0,0))
    def test_summary(self):
        self.assertEqual(season([match(hg=3,ag=1)],['C'])['summary'], {'games':1,'teams':3,'goals':4})
    def test_match_copy(self):
        rows = [match()]
        before = copy.deepcopy(rows)
        matches(rows)[0]['home'] = 'changed'
        self.assertEqual(rows,before)
    def test_rank_copy(self):
        rows = table([match()])
        before = copy.deepcopy(rows)
        rank(rows)[0]['points'] = 999
        self.assertEqual(rows,before)
    def test_invalid_score(self):
        for value in [True,-1,1.2]:
            with self.assertRaises(ValueError):
                table([match(hg=value)])
    def test_duplicate_and_self(self):
        for rows in [[match(),match()],[match(home='A',away='A')]]:
            with self.assertRaises(ValueError):
                table(rows)

if __name__ == '__main__':
    unittest.main()
