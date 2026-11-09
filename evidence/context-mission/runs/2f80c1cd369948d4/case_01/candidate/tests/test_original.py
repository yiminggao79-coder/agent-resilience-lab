import copy
import unittest
from rows import owned
from joins import left_join
from grouping import summarize
def T(columns,rows): return {'columns':columns,'rows':rows}
class OriginalContracts(unittest.TestCase):
    def test_duplicate_join_matches(self):
        got=left_join(T(['k'],[[1]]),T(['k','v'],[[1,2],[1,3]]),['k'],['k'])
        self.assertEqual(got['rows'],[[1,1,2],[1,1,3]])
    def test_null_keys_do_not_join(self):
        got=left_join(T(['k'],[[None]]),T(['k','v'],[[None,9]]),['k'],['k'])
        self.assertEqual(got['rows'],[[None,None,None]])
    def test_left_padding(self):
        self.assertEqual(left_join(T(['k'],[[2]]),T(['k'],[[1]]),['k'],['k'])['rows'],[[2,None]])
    def test_present_count(self):
        got=summarize(T(['g','v'],[['a',None],['a',4]]),['g'],'v')
        self.assertEqual(got['rows'],[['a',2,1,4]])
    def test_null_group_key(self):
        self.assertEqual(summarize(T(['g','v'],[[None,2],[None,3]]),['g'],'v')['rows'],[[None,2,2,5]])
    def test_first_seen_groups(self):
        self.assertEqual(summarize(T(['g','v'],[['z',1],['a',2]]),['g'],'v')['rows'],[['z',1,1,1],['a',1,1,2]])
    def test_empty_table(self):
        self.assertEqual(summarize(T(['v'],[]),[],'v')['rows'],[])
    def test_owned_rows(self):
        data=T(['x'],[[1]])
        before=copy.deepcopy(data)
        owned(data)['rows'][0][0]=7
        self.assertEqual(data,before)
    def test_join_no_alias(self):
        left=T(['k'],[[1]])
        result=left_join(left,T(['k'],[]),['k'],['k'])
        result['rows'][0][0]=99
        self.assertEqual(left['rows'],[[1]])
    def test_duplicate_columns(self):
        with self.assertRaises(ValueError): owned(T(['x','x'],[[1,2]]))
    def test_invalid_cells(self):
        for value in [True,1.5,[]]:
            with self.assertRaises(ValueError): owned(T(['x'],[[value]]))
    def test_join_collision(self):
        with self.assertRaises(ValueError): left_join(T(['k'],[]),T(['k'],[]),['k'],['k'],'')
if __name__=='__main__': unittest.main()
