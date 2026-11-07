"""Bounded MCP stdio for explicitly trusted host configuration, not a sandbox."""
import copy
import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading
import time

from .tool_control import CancellationState,CheckFailed

PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18')
_NAME = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
_MISSING = object()
_EFFECT_METHODS = ('tools/call','resources/list','resources/templates/list','resources/read')


def _resource_string(value,*,uri=False):
    if (type(value) is not str or not 1<=len(value)<=4096
            or any(ord(c)<32 or ord(c)==127 for c in value)
            or (uri and (any(c.isspace() for c in value) or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:',value) is None))):
        raise MCPError('invalid_resource_arguments')
    try:value.encode('utf-8')
    except UnicodeError:raise MCPError('invalid_resource_arguments') from None
    return value


class MCPError(Exception):
    """Sanitized protocol/transport failure; uncertainty forbids automatic replay."""
    def __init__(self, code, uncertain=False, rpc_code=None):
        self.code, self.uncertain, self.rpc_code = code, uncertain, rpc_code
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError()


class MCPClient:
    def __init__(self, command, cwd, *, timeout=5.0, max_bytes=1048576,
                 max_pages=16, max_tools=256, env=None, env_allowlist=()):
        if os.name != 'posix':
            raise ValueError('unsupported_mcp_platform')
        if (not isinstance(command, tuple) or not command or len(command) > 128
                or any(not isinstance(arg, str) or '\x00' in arg or len(arg) > 65536 for arg in command)
                or not Path(command[0]).is_absolute() or not Path(command[0]).is_file()
                or not os.access(command[0], os.X_OK)):
            raise ValueError('invalid_mcp_command')
        cwd = Path(cwd)
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError('invalid_mcp_cwd')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError('invalid_mcp_timeout')
        if type(max_bytes) is not int or not 128 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError('invalid_mcp_byte_limit')
        if type(max_pages) is not int or not 1 <= max_pages <= 128 or type(max_tools) is not int or not 1 <= max_tools <= 4096:
            raise ValueError('invalid_mcp_catalog_limit')
        if (not isinstance(env_allowlist, tuple) or any(not isinstance(k, str) or not k or '=' in k or '\x00' in k for k in env_allowlist)
                or (env is not None and not isinstance(env, dict))):
            raise ValueError('invalid_mcp_environment')
        environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8'}
        for key, value in (env or {}).items():
            if (not isinstance(key, str) or key not in env_allowlist or '=' in key or '\x00' in key
                    or not isinstance(value, str) or '\x00' in value):
                raise ValueError('mcp_environment_not_allowlisted')
            environment[key] = value
        self._command, self._cwd, self._environment = command, str(cwd.resolve()), environment
        self.timeout, self.max_bytes = timeout, max_bytes
        self.max_pages, self.max_tools = max_pages, max_tools
        self._lock, self._state, self._process = threading.RLock(), 'new', None
        self._next_id, self._buffer, self._catalog = 1, bytearray(), None
        self._capabilities = {}
        self._cancellation=CancellationState()
        self.protocol_version, self.server_info, self.stderr_bytes = None, None, 0

    def cancellation_scope(self,check):
        return self._cancellation.scope(check)

    def _check_cancelled(self):
        try:
            if self._cancellation.requested():raise MCPError('request_cancelled')
        except CheckFailed:
            raise MCPError('request_cancel_check_failed') from None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def start(self):
        with self._lock:
            if self._state != 'new':
                raise MCPError('client_not_new')
            self._state = 'starting'
            try:
                self._process = subprocess.Popen(self._command, cwd=self._cwd, env=self._environment,
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                 start_new_session=True, close_fds=True, bufsize=0)
                for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
                    os.set_blocking(pipe.fileno(), False)
                deadline = time.monotonic() + self.timeout
                return self._initialize(deadline)
            except MCPError:
                self._close_locked()
                raise
            except (OSError, ValueError, TypeError):
                self._close_locked()
                raise MCPError('server_start_failed') from None

    def _initialize(self, deadline):
        """Shared protocol lifecycle; transports retain ownership of their I/O."""
        result = self._request('initialize', {'protocolVersion': PROTOCOL_VERSIONS[0], 'capabilities': {},
            'clientInfo': {'name': 'agent-resilience-lab', 'version': '0.1.0'}}, deadline)
        if result.get('protocolVersion') not in PROTOCOL_VERSIONS:
            raise MCPError('unsupported_protocol_version')
        info, capabilities = result.get('serverInfo'), result.get('capabilities')
        if (not isinstance(info, dict) or not isinstance(info.get('name'), str)
                or not isinstance(info.get('version'), str) or not isinstance(capabilities, dict)
                or any(feature in capabilities and not isinstance(capabilities[feature],dict) for feature in ('tools','resources'))):
            raise MCPError('invalid_initialize_result')
        self.protocol_version, self.server_info = result['protocolVersion'], copy.deepcopy(info)
        self._capabilities = capabilities
        self._io(self._encode({'jsonrpc': '2.0', 'method': 'notifications/initialized'}), None, deadline)
        self._state = 'ready'
        return copy.deepcopy(result)

    def _ready(self):
        if self._state != 'ready':
            raise MCPError('client_not_ready')
        if 'tools' not in self._capabilities:
            raise MCPError('tools_not_supported')

    def list_tools(self):
        with self._lock:
            self._ready()
            deadline, tools, names, cursors, cursor = time.monotonic() + self.timeout, [], set(), set(), None
            try:
                for _ in range(self.max_pages):
                    result = self._request('tools/list', {} if cursor is None else {'cursor': cursor}, deadline)
                    page = result.get('tools')
                    if not isinstance(page, list):
                        raise MCPError('invalid_tool_catalog')
                    for tool in page:
                        if (not isinstance(tool, dict) or not isinstance(tool.get('name'), str)
                                or not _NAME.fullmatch(tool['name']) or tool['name'] in names
                                or not isinstance(tool.get('inputSchema'), dict)):
                            raise MCPError('invalid_tool_catalog')
                        names.add(tool['name'])
                        tools.append(tool)
                        if len(tools) > self.max_tools:
                            raise MCPError('tool_catalog_limit')
                    if len(self._encode(tools)) > self.max_bytes:
                        raise MCPError('tool_catalog_limit')
                    cursor = result.get('nextCursor')
                    if cursor is None:
                        self._catalog = names
                        return copy.deepcopy(tools)
                    if not isinstance(cursor, str) or not cursor or cursor in cursors:
                        raise MCPError('invalid_pagination')
                    cursors.add(cursor)
                raise MCPError('tool_catalog_limit')
            except MCPError:
                self._close_locked()
                raise

    def supports(self,feature):
        if feature not in ('tools','resources'):raise ValueError('invalid_mcp_feature')
        return self._state=='ready' and feature in self._capabilities

    def _resources_ready(self):
        if self._state!='ready':raise MCPError('client_not_ready')
        if 'resources' not in self._capabilities:raise MCPError('resources_not_supported')

    def _list_resources_page(self,templates,cursor):
        with self._lock:
            self._resources_ready()
            params={} if cursor is None else {'cursor':_resource_string(cursor)}
            method='resources/templates/list' if templates else 'resources/list'
            field,key=('resourceTemplates','uriTemplate') if templates else ('resources','uri')
            self._encode({'jsonrpc':'2.0','id':self._next_id,'method':method,'params':params})
            received=False
            try:
                result=self._request(method,params,time.monotonic()+self.timeout)
                received=True
                items=result.get(field)
                if type(items) is not list or len(items)>self.max_tools:raise MCPError('invalid_resource_catalog')
                seen=set()
                for entry in items:
                    if type(entry) is not dict:raise MCPError('invalid_resource_catalog')
                    value=_resource_string(entry.get(key),uri=True)
                    _resource_string(entry.get('name'))
                    if value in seen:raise MCPError('invalid_resource_catalog')
                    seen.add(value)
                    for optional in ('description','mimeType','title'):
                        if optional in entry and type(entry[optional]) is not str:raise MCPError('invalid_resource_catalog')
                if 'nextCursor' in result and result['nextCursor'] is not None:
                    _resource_string(result['nextCursor'])
                    if result['nextCursor']==cursor:raise MCPError('invalid_pagination')
                self._encode(result)
                return copy.deepcopy(result)
            except MCPError as error:
                error.uncertain=error.uncertain or received
                self._close_locked();raise

    def list_resources(self,cursor=None):
        return self._list_resources_page(False,cursor)

    def list_resource_templates(self,cursor=None):
        return self._list_resources_page(True,cursor)

    def read_resource(self,uri):
        with self._lock:
            self._resources_ready();uri=_resource_string(uri,uri=True)
            self._encode({'jsonrpc':'2.0','id':self._next_id,'method':'resources/read','params':{'uri':uri}})
            received=False
            try:
                result=self._request('resources/read',{'uri':uri},time.monotonic()+self.timeout)
                received=True
                contents=result.get('contents')
                if type(contents) is not list or len(contents)>self.max_tools:raise MCPError('invalid_resource_result')
                for block in contents:
                    if type(block) is not dict:raise MCPError('invalid_resource_result')
                    try:_resource_string(block.get('uri'),uri=True)
                    except MCPError:raise MCPError('invalid_resource_result') from None
                    if ('mimeType' in block and type(block['mimeType']) is not str
                            or ('text' in block)==('blob' in block)):
                        raise MCPError('invalid_resource_result')
                    field='text' if 'text' in block else 'blob'
                    if type(block[field]) is not str:raise MCPError('invalid_resource_result')
                    if field=='blob':
                        try:base64.b64decode(block[field],validate=True)
                        except (ValueError,binascii.Error):raise MCPError('invalid_resource_result') from None
                self._encode(result)
                return copy.deepcopy(result)
            except MCPError as error:
                error.uncertain=error.uncertain or received
                self._close_locked();raise

    def call_tool(self, name, arguments):
        with self._lock:
            self._ready()
            if not isinstance(name, str) or self._catalog is None or name not in self._catalog:
                raise MCPError('tool_not_discovered')
            if not isinstance(arguments, dict):
                raise MCPError('invalid_tool_arguments')
            # Validate/size before dispatch, so local errors never imply side effects.
            self._encode(arguments)
            result = self._request('tools/call', {'name': name, 'arguments': arguments}, time.monotonic() + self.timeout)
            if (not isinstance(result.get('content'), list)
                    or any(not isinstance(block, dict) or not isinstance(block.get('type'), str) for block in result['content'])
                    or ('isError' in result and type(result['isError']) is not bool)
                    or ('structuredContent' in result and not isinstance(result['structuredContent'], dict))):
                self._close_locked()
                raise MCPError('invalid_tool_result', uncertain=True)
            return result

    def _encode(self, value):
        try:
            packet = (json.dumps(value, allow_nan=False, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf-8')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise MCPError('invalid_json_value') from None
        if len(packet) > self.max_bytes:
            raise MCPError('request_limit')
        return packet

    def _request(self, method, params, deadline):
        rid = self._next_id
        packet = self._encode({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        self._next_id += 1
        self._sent_any = False
        try:
            return self._io(packet, rid, deadline)
        except MCPError as error:
            if method != 'initialize' and (error.code == 'request_timeout' or
                    (error.code in ('request_cancelled','request_cancel_check_failed') and self._sent_any)):
                self._cancel_request(rid)
            error.uncertain = error.uncertain or (method in _EFFECT_METHODS and self._sent_any)
            self._close_locked()
            raise
        except (OSError, ValueError):
            uncertain = method in _EFFECT_METHODS and self._sent_any
            self._close_locked()
            raise MCPError('server_closed', uncertain=uncertain) from None
        except BaseException:
            # Interrupts must not leave a ready client which a surrounding
            # finally could reuse or close by starting another HTTP request.
            self._close_locked()
            raise

    def _cancel_request(self, rid):
        try:
            os.write(self._process.stdin.fileno(), self._encode({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
                'params': {'requestId': rid, 'reason': 'client stopped waiting'}}))
        except (OSError, MCPError):
            pass

    def _message(self, line, rid, outgoing):
        try:
            message = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise MCPError('invalid_jsonrpc') from None
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise MCPError('invalid_jsonrpc')
        if 'method' in message:
            if (not isinstance(message['method'], str) or 'result' in message or 'error' in message
                    or ('params' in message and not isinstance(message['params'], (dict, list)))):
                raise MCPError('invalid_jsonrpc')
            if 'id' in message:
                if type(message['id']) not in (int, str):
                    raise MCPError('invalid_jsonrpc')
                response = {'jsonrpc': '2.0', 'id': message['id']}
                if message['method'] == 'ping':
                    response['result'] = {}
                else:
                    response['error'] = {'code': -32601, 'message': 'Method not supported'}
                outgoing.extend(self._encode(response))
            elif message['method'] == 'notifications/tools/list_changed':
                self._catalog = None
            return _MISSING
        if (rid is None or type(message.get('id')) is not int or message['id'] != rid
                or ('result' in message) == ('error' in message)):
            raise MCPError('unexpected_response')
        if 'error' in message:
            error = message['error']
            if not isinstance(error, dict) or type(error.get('code')) is not int or not isinstance(error.get('message'), str):
                raise MCPError('invalid_jsonrpc')
            raise MCPError('rpc_error', rpc_code=error['code'])
        if not isinstance(message['result'], dict):
            raise MCPError('invalid_result')
        return message['result']

    def _io(self, packet, rid, deadline):
        process, outgoing, received, result = self._process, bytearray(packet), 0, _MISSING
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            writing = False
            while True:
                self._check_cancelled()
                if time.monotonic() >= deadline:
                    raise MCPError('request_timeout')
                while b'\n' in self._buffer:
                    line, _, rest = self._buffer.partition(b'\n')
                    self._buffer = bytearray(rest)
                    next_result = self._message(line, rid, outgoing)
                    if next_result is not _MISSING:
                        if result is not _MISSING:
                            raise MCPError('unexpected_response')
                        result = next_result
                if not outgoing and (result is not _MISSING or rid is None):
                    return None if rid is None else result
                if outgoing and not writing:
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                    writing = True
                elif not outgoing and writing:
                    selector.unregister(process.stdin)
                    writing = False
                remaining=max(0,deadline-time.monotonic())
                for key, _ in selector.select(min(.05,remaining) if self._cancellation.checks.get() else remaining):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fd, outgoing[:65536])
                        except BlockingIOError:
                            continue
                        self._sent_any = True
                        del outgoing[:count]
                        continue
                    try:
                        chunk = os.read(key.fd, min(65536, self.max_bytes + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if key.data == 'stdout':
                            raise MCPError('server_closed')
                        continue
                    received += len(chunk)
                    if key.data == 'stderr':
                        self.stderr_bytes += len(chunk)
                    if received > self.max_bytes:
                        raise MCPError('output_limit')
                    if key.data == 'stdout':
                        self._buffer.extend(chunk)

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._state == 'closed':
            return
        self._state, self._catalog = 'closed', None
        process = self._process
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            pass
        # Always target the group: the leader can die while descendants retain
        # the pipes. This is cleanup, not isolation against deliberate setsid().
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                # Reap a TERM-killed leader before the final group signal.
                # macOS returns EPERM for a group containing only that zombie.
                try:
                    process.wait(timeout=.05)
                except subprocess.TimeoutExpired:
                    pass
        try:
            process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (process.stdout, process.stderr):
            pipe.close()
        self._buffer.clear()
