"""Real loopback protocol continuity from a resolved failed source into a fork."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_lab.engine import Engine,Limits
from agent_lab.coding_tools import CodingTools
from agent_lab.model import OpenAIModel
from agent_lab.store import Store
from tests.dev.test_terminal_history_http import HistoryHTTP


class SessionForkHTTPTests(unittest.TestCase):
    def test_fork_has_no_inference_and_retains_three_protocol_history_on_explicit_run(self):
        for protocol in ('chat','responses','anthropic'):
            with self.subTest(protocol=protocol),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'work';root.mkdir();shared=root/'shared';shared.write_text('original')
                server=ThreadingHTTPServer(('127.0.0.1',0),HistoryHTTP);server.requests=[];server.protocol=protocol
                threading.Thread(target=server.serve_forever,daemon=True).start()
                try:
                    with patch.dict(os.environ,{'LAB_FORK_KEY':'synthetic-fork-only'}),Store(Path(tmp)/'state.sqlite') as store:
                        model=OpenAIModel('fixture','http://127.0.0.1:%d/v1'%server.server_port,'LAB_FORK_KEY',True,protocol=protocol)
                        engine=Engine(store,CodingTools(root),model);tid=engine.create('Original',Limits(max_errors=1))
                        source=engine.run(tid);self.assertEqual(source['reason'],'error_limit');events=store.events(tid)
                        child_id=Engine(store,CodingTools(root),None).fork_session(tid,'Try another continuation')
                        self.assertEqual(len(server.requests),1,'fork must not invoke provider')
                        child=store.load(child_id);self.assertEqual(child['messages'][:len(source['messages'])],source['messages'])
                        shared.write_text('current external version')
                        result=engine.run(child_id)
                        self.assertEqual(result['phase'],'completed');self.assertEqual(len(server.requests),2)
                        self.assertEqual(shared.read_text(),'current external version');self.assertFalse((root/'never').exists())
                        self.assertEqual(store.load(tid),source);self.assertEqual(store.events(tid),events)
                        self.assertEqual(result['metrics']['model_calls'],1);self.assertEqual(result['session_metrics']['model_calls'],1)
                        wire=server.requests[1];self.assertNotIn('_runtime_control',json.dumps(wire))
                        self.assertIn('Try another continuation',json.dumps(wire))
                        if protocol=='chat':
                            outputs=[(m['tool_call_id'],json.loads(m['content'])) for m in wire['messages'] if m['role']=='tool']
                        elif protocol=='responses':
                            outputs=[(m['call_id'],json.loads(m['output'])) for m in wire['input'] if m.get('type')=='function_call_output']
                            self.assertEqual([m['id'] for m in wire['input'] if m.get('type')=='function_call'],['fc_bad','fc_tail'])
                        else:
                            outputs=[(b['tool_use_id'],json.loads(b['content'])) for m in wire['messages'] for b in m.get('content',[]) if b.get('type')=='tool_result']
                        self.assertEqual([i for i,_ in outputs],['bad','tail'])
                        self.assertEqual(outputs[1][1],{'ok':False,'error':'not_executed','reason':'error_limit'})
                finally:server.shutdown();server.server_close()
