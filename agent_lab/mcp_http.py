"""Narrow host-configured MCP Streamable HTTP; not a command sandbox.

POST JSON/SSE, session/version headers, no auth, redirects, GET push, retries or
resumable streams. Local close/DELETE is never a remote quiescence receipt.
"""
from contextlib import contextmanager
import http.client
import ipaddress
import math
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .mcp import MCPClient, MCPError, _MISSING
from .tool_control import CancellationState


class _ExchangeControl:
    """Pinned socket objects and a stop gate retained by late I/O workers."""
    def __init__(self):
        self.stopped=threading.Event();self.lock=threading.Lock();self.sockets=set()

    def check(self):
        if self.stopped.is_set():raise MCPError('request_cancelled')

    def register(self,sock):
        with self.lock:self.sockets.add(sock)
        if self.stopped.is_set():
            self.abort();self.check()

    def unregister(self,sock):
        with self.lock:self.sockets.discard(sock)

    def abort(self):
        self.stopped.set()
        with self.lock:sockets=tuple(self.sockets)
        for sock in sockets:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass


class MCPHTTPClient(MCPClient):
    def __init__(self,url,*,allow_loopback_http=False,timeout=5.0,max_bytes=1048576,
                 max_pages=16,max_tools=256):
        if type(allow_loopback_http) is not bool:
            raise ValueError('invalid_mcp_http_url')
        try:
            if (type(url) is not str or not 1<=len(url)<=4096
                    or any(ord(c)<33 or ord(c)>126 for c in url) or '\\' in url):
                raise ValueError()
            endpoint=urlsplit(url)
            if (not endpoint.hostname or endpoint.username is not None or endpoint.password is not None
                    or endpoint.query or endpoint.fragment or '?' in url or '#' in url
                    or endpoint.scheme not in ('http','https') or '%' in endpoint.hostname
                    or (endpoint.port is not None and not 1<=endpoint.port<=65535)):
                raise ValueError()
            if endpoint.scheme=='http':
                if not allow_loopback_http or not ipaddress.ip_address(endpoint.hostname).is_loopback:
                    raise ValueError()
        except (ValueError,TypeError):
            raise ValueError('invalid_mcp_http_url') from None
        if type(timeout) not in (int,float) or not 0<timeout<=3600 or not math.isfinite(timeout):
            raise ValueError('invalid_mcp_timeout')
        if type(max_bytes) is not int or not 128<=max_bytes<=16*1024*1024:
            raise ValueError('invalid_mcp_byte_limit')
        if (type(max_pages) is not int or not 1<=max_pages<=128
                or type(max_tools) is not int or not 1<=max_tools<=4096):
            raise ValueError('invalid_mcp_catalog_limit')
        self.url,self._endpoint=url,endpoint
        self.timeout,self.max_bytes,self.max_pages,self.max_tools=timeout,max_bytes,max_pages,max_tools
        self._lock,self._state=threading.RLock(),'new'
        self._next_id,self._catalog,self._capabilities=1,None,{}
        self.protocol_version,self.server_info,self.stderr_bytes=None,None,0
        self._session_id=None
        self._cancellation=CancellationState()
        self._exchange_control=threading.local()
        # stdlib TLS verification stays enabled; no proxy/env/header discovery.
        self._tls=ssl.create_default_context() if endpoint.scheme=='https' else None

    def start(self):
        with self._lock:
            if self._state!='new': raise MCPError('client_not_new')
            self._state='starting'
            try: return self._initialize(time.monotonic()+self.timeout)
            except MCPError:
                self._close_locked();raise

    def _remaining(self,deadline):
        control=getattr(self._exchange_control,'value',None)
        if control is not None:control.check()
        remaining=deadline-time.monotonic()
        if remaining<=0: raise MCPError('request_timeout')
        return remaining

    @contextmanager
    def _exchange(self,packet,deadline,method='POST'):
        endpoint=self._endpoint
        options={'timeout':self._remaining(deadline)}
        if self._tls is not None: options['context']=self._tls
        kind=http.client.HTTPSConnection if self._tls is not None else http.client.HTTPConnection
        connection=kind(endpoint.hostname,endpoint.port,**options)
        watchdog=None;owned_socket=None;response=None
        control=getattr(self._exchange_control,'value',None)
        expired=threading.Event()
        try:
            connection.connect()
            owned_socket=connection.sock
            if control is not None:control.register(owned_socket)
            remaining=self._remaining(deadline)
            owned_socket.settimeout(remaining)
            # Header parsing inside http.client cannot check our deadline per
            # byte. A peer dripping headers can reset an inactivity timeout.
            # Shutdown this pinned socket at the total connected-I/O deadline;
            # never target a recycled fd or infer remote effect cancellation.
            def expire():
                expired.set()
                try: owned_socket.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            watchdog=threading.Timer(remaining,expire)
            watchdog.daemon=True;watchdog.start()
            headers={'Accept':'application/json, text/event-stream'}
            if packet is not None: headers['Content-Type']='application/json'
            if self._session_id is not None: headers['Mcp-Session-Id']=self._session_id
            if self.protocol_version is not None: headers['MCP-Protocol-Version']=self.protocol_version
            self._remaining(deadline)
            # A partial header/body send cannot prove absence of remote effects.
            self._sent_any=True
            connection.request(method,endpoint.path or '/',body=packet,headers=headers)
            connection.sock.settimeout(self._remaining(deadline))
            response=connection.getresponse()
            if sum(len(k)+len(v)+4 for k,v in response.getheaders())>min(self.max_bytes,65536):
                raise MCPError('output_limit')
            sessions=response.headers.get_all('Mcp-Session-Id',[])
            if sessions:
                if (len(sessions)!=1 or not 1<=len(sessions[0])<=128
                        or any(not 33<=ord(c)<=126 for c in sessions[0])):
                    raise MCPError('invalid_session')
                if self._state=='starting' and self.protocol_version is None and method=='POST':
                    self._session_id=sessions[0]
                elif sessions[0]!=self._session_id: raise MCPError('invalid_session')
            yield response,connection
        except (socket.timeout,TimeoutError):
            raise MCPError('request_timeout') from None
        except (OSError,http.client.HTTPException):
            raise MCPError('request_timeout' if expired.is_set() else 'server_closed') from None
        finally:
            if watchdog is not None:
                watchdog.cancel();watchdog.join()
            # getresponse may detach a will-close response from its connection.
            # Cancellation/errors can leave its makefile unread; closing only
            # the connection then retains the FD until response garbage collection.
            if response is not None:response.close()
            if control is not None and owned_socket is not None:control.unregister(owned_socket)
            connection.close()

    def _chunks(self,response,connection,deadline):
        received=0
        while True:
            timeout=self._remaining(deadline)
            # http.client can detach its socket for Connection: close replies.
            sock=connection.sock or getattr(getattr(response.fp,'raw',None),'_sock',None)
            if sock is not None: sock.settimeout(timeout)
            chunk=response.read1(min(65536,self.max_bytes-received+1))
            if not chunk: return
            received+=len(chunk)
            if received>self.max_bytes: raise MCPError('output_limit')
            yield chunk

    @staticmethod
    def _status(status):
        if 300<=status<400: raise MCPError('redirect_refused')
        if status==404: raise MCPError('session_expired')
        if status in (401,403): raise MCPError('authentication_required')
        if status==429: raise MCPError('rate_limited')
        if status!=200: raise MCPError('unexpected_http_status')

    def _io(self,packet,rid,deadline):
        # Nested SSE replies stay on the same I/O worker/control. The worker
        # never evaluates callbacks which may read controller-thread SQLite.
        if getattr(self._exchange_control,'value',None) is not None:
            return self._io_blocking(packet,rid,deadline)
        if not self._cancellation.checks.get():return self._io_blocking(packet,rid,deadline)
        self._check_cancelled()
        control=_ExchangeControl();done=threading.Event();outcome={}
        def work():
            self._exchange_control.value=control
            try:outcome['result']=self._io_blocking(packet,rid,deadline)
            except BaseException as error:outcome['error']=error
            finally:
                del self._exchange_control.value
                done.set()
        worker=threading.Thread(target=work,daemon=True);worker.start()
        try:
            while not done.wait(min(.05,max(0,deadline-time.monotonic()))):
                self._check_cancelled();self._remaining(deadline)
            if 'error' in outcome:raise outcome['error']
            return outcome['result']
        except BaseException:
            control.abort()
            raise
        finally:
            # Connected I/O wakes on shutdown. An unfinished DNS/connect worker
            # retains its own aborted thread-local gate and cannot forget stop
            # when the controller returns. No remote cleanup proof is emitted.
            worker.join(.2)

    def _io_blocking(self,packet,rid,deadline):
        with self._exchange(packet,deadline) as (response,connection):
            if rid is None:
                if response.status!=202: self._status(response.status);raise MCPError('unexpected_http_status')
                if any(self._chunks(response,connection,deadline)): raise MCPError('unexpected_http_body')
                return None
            self._status(response.status)
            kinds=response.headers.get_all('Content-Type',[])
            if len(kinds)!=1: raise MCPError('unsupported_content_type')
            kind=kinds[0].split(';',1)[0].strip().lower()
            chunks=self._chunks(response,connection,deadline)
            if kind=='application/json':
                raw=b''.join(chunks)
                self._remaining(deadline)
                value=self._message(raw,rid,bytearray())
                if value is _MISSING: raise MCPError('unexpected_response')
                return value
            if kind!='text/event-stream': raise MCPError('unsupported_content_type')
            return self._sse(chunks,rid,deadline)

    def _sse(self,chunks,rid,deadline):
        line,data=bytearray(),[]
        skip_lf=False;first_line=True;reply_count=0;result=_MISSING
        def consume(raw):
            nonlocal first_line,reply_count,result
            if first_line: raw=raw.removeprefix(b'\xef\xbb\xbf');first_line=False
            if not raw:
                if not data: return
                outgoing=bytearray()
                self._remaining(deadline)
                value=self._message(b'\n'.join(data),rid,outgoing);data.clear()
                if outgoing:
                    for reply in outgoing.splitlines():
                        reply_count+=1
                        if reply_count>16: raise MCPError('server_request_limit')
                        self._io(reply+b'\n',None,deadline)
                if value is not _MISSING:
                    if result is not _MISSING: raise MCPError('unexpected_response')
                    result=value
            elif raw.startswith(b'data:'):
                value=raw[5:]
                data.append(value[1:] if value.startswith(b' ') else value)
        for chunk in chunks:
            for byte in chunk:
                if skip_lf:
                    skip_lf=False
                    if byte==10: continue
                if byte in (10,13):
                    consume(bytes(line));line.clear();skip_lf=byte==13
                    if result is not _MISSING: return result
                else: line.append(byte)
        raise MCPError('incomplete_sse')

    def _cancel_request(self,rid):
        # Closing a timed-out response is local only. Do not send another request
        # after its deadline, reinitialize a session, or claim remote cancellation.
        pass

    def _close_locked(self):
        self._state,self._catalog,self._session_id='closed',None,None

    def close(self):
        with self._lock:
            if self._state=='ready' and self._session_id is not None:
                try:
                    with self._exchange(None,time.monotonic()+min(.25,self.timeout),'DELETE') as (response,_):
                        # 405 means termination unsupported. No cleanup proof is
                        # inferred from any HTTP status or an absent body.
                        pass
                except MCPError: pass
            self._close_locked()
