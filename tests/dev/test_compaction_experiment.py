import sys
import json
import types
import unittest
from unittest.mock import patch


class CompactionOracleTests(unittest.TestCase):
    def test_old_summary_cannot_count_as_a_successful_new_compaction(self):
        from scripts.evaluate_compaction import installed_this_attempt
        record={'generation':2,'source_sha256':'new-prefix','covered_until':20}
        state={'semantic_context':record,'last_compaction':{'status':'installed','source_sha256':'new-prefix','covered_until':20}}
        self.assertTrue(installed_this_attempt(state,1))
        self.assertFalse(installed_this_attempt(state,2))
        state['last_compaction']['status']='failed'
        self.assertFalse(installed_this_attempt(state,1))
        state['last_compaction']['status']='installed'
        state['last_compaction']['source_sha256']='old-prefix'
        self.assertFalse(installed_this_attempt(state,1))

    def test_summary_usage_requires_exact_user_data_in_the_actual_request(self):
        from scripts.evaluate_compaction import summary_in_request
        record={'summary':'decision','source_sha256':'prefix-hash','covered_until':17}
        payload={'semantic_context':True,**record}
        request={'messages':[{'role':'assistant','content':'I used the summary'}]}
        self.assertFalse(summary_in_request(request,record))
        request={'messages':[{'role':'user','content':'Boundary\n'+json.dumps(payload)}]}
        self.assertTrue(summary_in_request(request,record))
        request['messages'][0]['role']='system'
        self.assertFalse(summary_in_request(request,record))

    def test_correct_values_cannot_hide_input_mutation(self):
        from scripts.evaluate_compaction import ORACLE_WRAPPER
        module=types.ModuleType('normalizer')
        def mutating(values,exclude=None):
            values.clear()
            if exclude is not None:exclude.clear()
            return ['a']
        module.normalize=mutating
        scope={}
        with patch.dict(sys.modules,{'normalizer':module}):exec(ORACLE_WRAPPER,scope)
        observed=scope['inspect'](['a','b'],['b'])
        self.assertEqual(observed['result'],['a'])
        self.assertFalse(observed['values_unchanged'])
        self.assertFalse(observed['exclude_unchanged'])
        module.normalize=lambda values,exclude=None:list(values)
        good=scope['inspect'](['a'],None)
        self.assertTrue(good['values_unchanged'])
        self.assertTrue(good['exclude_unchanged'])
