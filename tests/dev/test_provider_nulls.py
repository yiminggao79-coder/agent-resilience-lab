import unittest
from agent_lab.protocols import normalize_response


class ProviderNullTests(unittest.TestCase):
    def test_documented_nullable_chat_reasoning_is_omitted_from_history(self):
        body={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'answer','reasoning_content':None}}],
              'usage':{'prompt_tokens':5,'completion_tokens':2}}
        result=normalize_response('chat',body)
        self.assertEqual(result['message'],{'role':'assistant','content':'answer'})
        self.assertEqual(result['usage'],{'input_tokens':5,'output_tokens':2})
