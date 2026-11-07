"""HTTP contract fixture driving real edits/tests; this is not a real LLM."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.model import OpenAIModel
from agent_lab.store import Store
from agent_lab import sandbox


class CodingHTTP(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        step=len(self.server.requests)
        message={'role':'assistant','content':None}
        action={1:('list_files',{}),2:('read_file',{'path':'calc.py'}),3:('run_tests',{}),
                4:('apply_patch',{'path':'calc.py','old':'return a-b','new':'return a+b'}),
                5:('run_tests',{}),6:('read_file',{'path':'calc.py'})}.get(step)
        if step in (4,6):
            feedback=json.loads(body['messages'][-1]['content'])
            self.server.test_results.append(feedback)
        if action:
            name,args=action
            message['tool_calls']=[{'id':'http_'+str(step),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]
        else:message['content']='Offline contract fixture completed; not real model reasoning.'
        reply={'id':'response_'+str(step),'choices':[{'message':message,'finish_reason':'tool_calls' if action else 'stop'}],
               'usage':{'prompt_tokens':10,'completion_tokens':10}}
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps(reply).encode())


class CodingEndToEnd(unittest.TestCase):
    def test_completed_http_bad_arguments_are_repaired_in_next_model_request(self):
        class RepairHTTP(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.requests.append(body)
                n=len(self.server.requests)
                message={'role':'assistant','content':'verified'}
                if n<3:
                    message={'role':'assistant','content':None,'tool_calls':[{'id':'repair_'+str(n),'type':'function',
                        'function':{'name':'read_file','arguments':'{' if n==1 else '{"path":"a"}'}}]}
                result={'choices':[{'message':message,'finish_reason':'tool_calls' if n<3 else 'stop'}],
                        'usage':{'prompt_tokens':7,'completion_tokens':3}}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(result).encode())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'work';root.mkdir();(root/'a').write_text('actual')
            server=ThreadingHTTPServer(('127.0.0.1',0),RepairHTTP);server.requests=[]
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                with patch.dict(os.environ,{'LAB_REPAIR_KEY':'fake'}),Store(Path(tmp)/'state'/'s.sqlite') as store:
                    model=OpenAIModel('fixture','http://127.0.0.1:%d/v1'%server.server_port,'LAB_REPAIR_KEY',True)
                    engine=Engine(store,CodingTools(root),model);tid=engine.create('Read a')
                    state=engine.run(tid)
                    self.assertEqual(state['phase'],'completed')
                    self.assertEqual(json.loads(server.requests[1]['messages'][-1]['content'])['error'],'invalid_arguments')
                    self.assertEqual(json.loads(server.requests[2]['messages'][-1]['content'])['content'],'actual')
                    self.assertEqual(state['metrics']['input_tokens'],21)
            finally:server.shutdown();server.server_close()

    def test_http_model_adapter_edits_real_code_and_uses_failing_then_passing_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'work';(root/'tests').mkdir(parents=True)
            (root/'calc.py').write_text('def add(a,b):\n    return a-b\n')
            (root/'tests'/'test_calc.py').write_text('import unittest\nfrom calc import add\nclass TestAdd(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n')
            try:
                preflight=sandbox.run_tests(root,5)
            except Exception as error:
                if getattr(error,'code',None)=='sandbox_unavailable':self.skipTest('OS backend unavailable, no bare fallback')
                raise
            self.assertEqual(preflight['exit_code'],1)
            server=ThreadingHTTPServer(('127.0.0.1',0),CodingHTTP)
            server.requests=[];server.test_results=[]
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch.dict(os.environ,{'LAB_HTTP_CODING_KEY':'fake-local-contract-key'}), Store(Path(tmp)/'private'/'state.sqlite') as store:
                    model=OpenAIModel('local-contract','http://127.0.0.1:%d/v1'%server.server_port,'LAB_HTTP_CODING_KEY',True)
                    engine=Engine(store,CodingTools(root),model)
                    tid=engine.create('Inspect project, fix add, run tests, read feedback and verify.',Limits(tool_timeout=8))
                    state=engine.run(tid)
                    self.assertEqual(state['phase'],'completed')
                    self.assertEqual([r['exit_code'] for r in server.test_results],[1,0])
                    self.assertIn('return a+b',(root/'calc.py').read_text())
                    self.assertEqual(len(server.requests),7)
                    self.assertEqual(len([e for e in store.events(tid) if e['kind']=='model_response']),7)
            finally:
                server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
