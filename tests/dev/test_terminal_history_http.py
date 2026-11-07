"""Three wire protocols across resolved failed/new Turns; synthetic server only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
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


class HistoryHTTP(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body);first=len(self.server.requests)==1
        calls=[('bad','read_file',{'path':'missing'}),('tail','write_file',{'path':'never','content':'no'})]
        protocol=self.server.protocol
        if protocol=='chat':
            message={'role':'assistant','content':None if first else 'done'}
            if first:message['tool_calls']=[{'id':i,'type':'function','function':{'name':n,'arguments':json.dumps(a)}} for i,n,a in calls]
            result={'id':'fixture','choices':[{'message':message,'finish_reason':'tool_calls' if first else 'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5}}
        elif protocol=='responses':
            output=([{'id':'fc_'+i,'type':'function_call','call_id':i,'name':n,'arguments':json.dumps(a),'status':'completed'} for i,n,a in calls] if first else
                    [{'id':'msg_done','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'done','annotations':[]}]}])
            result={'id':'fixture','status':'completed','output':output,'usage':{'input_tokens':10,'output_tokens':5}}
        else:
            content=([{'type':'tool_use','id':i,'name':n,'input':a} for i,n,a in calls] if first else [{'type':'text','text':'done'}])
            result={'id':'fixture','content':content,'stop_reason':'tool_use' if first else 'end_turn','usage':{'input_tokens':10,'output_tokens':5}}
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps(result).encode())


class TerminalHistoryHTTPTests(unittest.TestCase):
    def test_failed_then_explicit_new_turn_sends_paired_real_http_feedback_in_all_protocols(self):
        for protocol in ('chat','responses','anthropic'):
            with self.subTest(protocol=protocol),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'work';root.mkdir()
                server=ThreadingHTTPServer(('127.0.0.1',0),HistoryHTTP);server.requests=[];server.protocol=protocol
                threading.Thread(target=server.serve_forever,daemon=True).start()
                try:
                    with patch.dict(os.environ,{'LAB_HISTORY_KEY':'synthetic-history-only'}),Store(Path(tmp)/'state.sqlite') as store:
                        model=OpenAIModel('fixture','http://127.0.0.1:%d/v1'%server.server_port,'LAB_HISTORY_KEY',True,protocol=protocol)
                        engine=Engine(store,CodingTools(root),model);tid=engine.create('Work',Limits(max_errors=1))
                        failed=engine.run(tid);self.assertEqual(failed['reason'],'error_limit')
                        engine.continue_turn(tid,'Do a new task');state=engine.run(tid)
                        self.assertEqual(state['phase'],'completed');self.assertEqual(len(server.requests),2)
                        wire=server.requests[1];self.assertNotIn('_runtime_control',json.dumps(wire))
                        if protocol=='chat':
                            outputs=[(m['tool_call_id'],json.loads(m['content'])) for m in wire['messages'] if m['role']=='tool']
                        elif protocol=='responses':
                            outputs=[(m['call_id'],json.loads(m['output'])) for m in wire['input'] if m.get('type')=='function_call_output']
                            originals=[m for m in wire['input'] if m.get('type')=='function_call']
                            self.assertEqual([m['id'] for m in originals],['fc_bad','fc_tail'])
                        else:
                            outputs=[(b['tool_use_id'],json.loads(b['content'])) for m in wire['messages'] for b in m.get('content',[]) if b.get('type')=='tool_result']
                            self.assertTrue(all(b['is_error'] for m in wire['messages'] for b in m.get('content',[]) if b.get('type')=='tool_result'))
                        self.assertEqual([i for i,_ in outputs],['bad','tail'])
                        self.assertEqual(outputs[0][1]['error'],'not_found')
                        self.assertEqual(outputs[1][1],{'ok':False,'error':'not_executed','reason':'error_limit'})
                        self.assertFalse((root/'never').exists());self.assertEqual(state['session_metrics']['model_calls'],2)
                finally:server.shutdown();server.server_close()
