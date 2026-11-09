"""Bounded synthetic Responses SSE peer for isolated reference-runtime experiments."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading

from scripts.reference_cli import FIXTURE_TOKEN

MAX_REQUEST_BYTES=1048576


def response_events(items,*,complete=True):
    response={'id':'resp_owned_fixture','object':'response','status':'completed','output':items,
              'usage':{'input_tokens':12,'output_tokens':3,'total_tokens':15}}
    events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}]
    for index,item in enumerate(items):
        started={**item,'status':'in_progress'}
        started['arguments' if item['type']=='function_call' else 'content']='' if item['type']=='function_call' else []
        events.append({'type':'response.output_item.added','output_index':index,'item':started})
        if item['type']=='function_call':
            events.append({'type':'response.function_call_arguments.delta','output_index':index,'item_id':item['id'],'delta':item['arguments']})
            events.append({'type':'response.function_call_arguments.done','output_index':index,'item_id':item['id'],'arguments':item['arguments']})
        else:
            for content_index,part in enumerate(item['content']):
                indices={'output_index':index,'item_id':item['id'],'content_index':content_index}
                events.append({'type':'response.content_part.added',**indices,'part':{**part,'text':''}})
                events.append({'type':'response.output_text.delta',**indices,'delta':part['text']})
                events.append({'type':'response.output_text.done',**indices,'text':part['text']})
                events.append({'type':'response.content_part.done',**indices,'part':part})
        events.append({'type':'response.output_item.done','output_index':index,'item':item})
    if complete:events.append({'type':'response.completed','response':response})
    for sequence,event in enumerate(events):event['sequence_number']=sequence
    return [('event: '+event['type']+'\ndata: '+json.dumps(event,separators=(',',':'))+'\n\n').encode() for event in events]


def final_item(text='owned reference final'):
    return {'id':'msg_owned_fixture','type':'message','role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':text,'annotations':[]}]}


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def setup(self):
        super().setup();self.connection.settimeout(3)
    def reject(self,status):
        self.send_response(status);self.send_header('Content-Length','0');self.send_header('Connection','close');self.end_headers();self.close_connection=True
    def do_GET(self):self.reject(404)
    def do_POST(self):
        if self.path!='/v1/responses':self.reject(404);return
        if self.headers.get('Authorization')!='Bearer '+FIXTURE_TOKEN:self.reject(401);return
        try:
            length=int(self.headers.get('Content-Length','-1'))
            if not 0<length<=MAX_REQUEST_BYTES:raise ValueError()
            body=json.loads(self.rfile.read(length))
            if type(body) is not dict:raise ValueError()
        except (ValueError,UnicodeError,OSError):self.reject(400);return
        with self.server.lock:
            index=len(self.server.requests)
            if index>=self.server.max_requests:self.reject(429);return
            self.server.requests.append(body)
        items=self.server.build_items(index,body)
        frames=response_events(items,complete=self.server.complete)
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
        try:
            delayed=frames[-1:] if self.server.completion_gate and index==0 and self.server.complete else []
            immediate=frames[:-1] if delayed else frames
            for frame in immediate:self.wfile.write(frame);self.wfile.flush()
            if self.server.completion_gate and index==0:
                self.server.item_done.set();self.server.release.wait(4)
            for frame in delayed:self.wfile.write(frame);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
        self.close_connection=True


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def handle_error(self,*args):pass


@contextmanager
def serving(*,max_requests=4,build_items=None,complete=True,completion_gate=False):
    server=Server(('127.0.0.1',0),Handler);server.requests=[];server.lock=threading.Lock();server.max_requests=max_requests
    server.build_items=build_items or (lambda index,body:[final_item()]);server.complete=complete
    server.completion_gate=completion_gate;server.item_done=threading.Event();server.release=threading.Event()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:server.release.set();server.shutdown();server.server_close();thread.join(4)
