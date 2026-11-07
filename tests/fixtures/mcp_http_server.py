"""Owned loopback MCP contract server. No remote services or credentials."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def send(self,status,body=b'',kind='application/json',headers=None):
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers()
        try: self.wfile.write(body);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError): pass
    def do_DELETE(self):
        self.server.records.append({'method':'DELETE','path':self.path,'headers':dict(self.headers)})
        self.send(405)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.records.append({'body':body,'path':self.path,'headers':dict(self.headers)})
        method=body.get('method')
        if method=='notifications/initialized' or method is None:
            self.send(202);return
        if method=='initialize':
            capabilities={'tools':{}}
            if getattr(self.server,'resources',False):capabilities['resources']={}
            result={'protocolVersion':self.server.version,'capabilities':capabilities,
                    'serverInfo':{'name':'synthetic-http','version':'1'}}
        elif method.startswith('resources/') and getattr(self.server,'resources',False):
            from tests.fixtures.mcp_resource_server import resource_result
            result=resource_result(method,body.get('params',{}))
        elif method=='tools/list':
            paged=self.server.paginated and not body.get('params',{}).get('cursor')
            tool='inspect' if self.server.paginated and not paged else 'echo'
            result={'tools':[{'name':tool,'description':'Public synthetic HTTP tool',
                             'inputSchema':{'type':'object'}}]}
            if paged:result['nextCursor']='second-page'
        else:
            self.server.effects.append(body.get('params',{}))
            mode=self.server.mode
            if mode in ('redirect','expired','accepted'):
                self.send({'redirect':307,'expired':404,'accepted':202}[mode],
                          headers={'Location':self.server.url+'/other'} if mode=='redirect' else None)
                return
            if mode=='effect_timeout':
                self.server.effect_ready.set();self.server.release.wait(getattr(self.server,'effect_wait_seconds',3))
                late=getattr(self.server,'late_effect_path',None)
                if late is not None:Path(late).write_text('external effect after local cancel\n')
            result={'content':[{'type':'text','text':'synthetic HTTP result'}],
                    'structuredContent':body['params']['arguments']}
        packet={'jsonrpc':'2.0','id':body['id'],'result':result}
        mode=self.server.mode if method=='tools/call' or method.startswith('resources/') else 'json'
        if mode=='wrong_id': packet['id']=True
        raw=json.dumps(packet).encode()
        if mode=='duplicate_key': raw=raw.replace(b'"jsonrpc": "2.0"',b'"jsonrpc":"2.0","jsonrpc":"2.0"')
        if mode=='overflow': raw=b'x'*8193
        headers={'Mcp-Session-Id':'synthetic-session'} if method=='initialize' and not self.server.stateless else {}
        if mode=='changed_session': headers['Mcp-Session-Id']='different-session'
        if mode in ('sse','persistent_sse','partial_sse','sse_cr'):
            before=(b': keepalive\n\nevent: message\ndata: '+json.dumps(
                {'jsonrpc':'2.0','method':'notifications/progress','params':{}}).encode()+b'\n\n'
                b'data: '+json.dumps({'jsonrpc':'2.0','id':'server-ping','method':'ping'}).encode()+b'\n\n')
            raw=before+b'event: message\ndata: '+raw+b'\n\n'
            if mode=='partial_sse': raw=raw[:-1]
            if mode=='sse_cr': raw=raw.replace(b'\n',b'\r')
            if mode=='persistent_sse':
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Transfer-Encoding','chunked');self.end_headers()
                self.wfile.write(('%x\r\n'%len(raw)).encode()+raw+b'\r\n');self.wfile.flush()
                self.server.result_ready.set();self.server.release.wait(3)
                try: self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError): pass
                return
            self.send(200,raw,'text/event-stream',headers);return
        self.send(200,raw,headers=headers)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def handle_error(self,*args): pass


@contextmanager
def serving():
    server=Server(('127.0.0.1',0),Handler)
    server.url='http://127.0.0.1:%d/mcp'%server.server_port
    server.records=[];server.effects=[];server.mode='json'
    server.version='2025-11-25';server.stateless=False;server.paginated=False
    server.release=threading.Event();server.effect_ready=threading.Event();server.result_ready=threading.Event()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield server
    finally:
        server.release.set();server.shutdown();server.server_close();thread.join(3)
