from pathlib import Path
import tempfile
import unittest
from agent_lab.sandbox import observe_function


class StructuredOracleTests(unittest.TestCase):
    def test_structured_observations_are_data_for_external_parent_judgement(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'candidate.py').write_text("def analyze(value):\n return {'sum':sum(value),'items':[{'value':x,'valid':True} for x in value]}\n")
            result=observe_function(root,'candidate','analyze',[[[2,3]],[[]]],5)
            self.assertTrue(result['complete'])
            self.assertEqual(result['observations'],[{'sum':5,'items':[{'value':2,'valid':True},{'value':3,'valid':True}]},{'sum':0,'items':[]}])
