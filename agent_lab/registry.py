"""Opt-in host-trusted MCP registry; external processes/services are not sandboxed."""
import copy
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading

from .mcp import MCPClient, MCPError, _resource_string
from .mcp_http import MCPHTTPClient
from .tools import ToolError, UncertainEffect
from .tool_control import ToolCancelled

MAX_CONFIG_BYTES = 65536
MAX_CATALOG_BYTES = 131072
MAX_RESULT_BYTES = 65536
_SERVER_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
_MCP_ORIGINS = ('mcp_stdio','mcp_streamable_http')
_RESOURCE_METHODS={'list_mcp_resources':'resources/list','list_mcp_resource_templates':'resources/templates/list',
                   'read_mcp_resource':'resources/read'}


def _resource_schemas():
    result=[]
    for name,method in _RESOURCE_METHODS.items():
        read=method=='resources/read'
        properties={'server':{'type':'string','description':'Exact configured server name; required, no aggregate listing.'}}
        properties['uri' if read else 'cursor']={'type':'string','description':
            'Opaque absolute resource URI; forwarded only to this server, never fetched locally.' if read else
            'Optional opaque nextCursor from the same server and operation.'}
        result.append({'type':'function','function':{'name':name,
            'description':'MCP '+method+' from a Host-configured trusted external server; exact approval required. '+
                ('Text contents or binary size/SHA metadata; not image rendering.' if read else 'One bounded page; reuse nextCursor explicitly.'),
            'parameters':{'type':'object','properties':properties,'required':['server','uri'] if read else ['server'],
                          'additionalProperties':False}}})
    return result



def _client(spec):
    options={key:spec[key] for key in ('timeout','max_bytes','max_pages','max_tools')}
    if spec.get('transport')=='streamable_http':
        return MCPHTTPClient(spec['url'],allow_loopback_http=spec['allow_loopback_http'],**options)
    return MCPClient(tuple(spec['command']),Path(spec['cwd']),**options)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_mcp_config_key')
        result[key] = value
    return result


def _outside(value, root, allow_symlink=False):
    path = Path(value)
    if not path.is_absolute() or (path.is_symlink() and not allow_symlink):
        raise ValueError('mcp_path_must_be_trusted_external')
    resolved = path.resolve()
    if (resolved == root or root in resolved.parents
            or any(parent.resolve() == root or root in parent.resolve().parents for parent in path.parents)):
        raise ValueError('mcp_path_must_be_trusted_external')
    return resolved


def _read_config(path, root):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('invalid_mcp_config_file')
    path = _outside(path, root)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_CONFIG_BYTES:
                raise ValueError('invalid_mcp_config_file')
            raw = stream.read(MAX_CONFIG_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (len(raw) > MAX_CONFIG_BYTES or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('mcp_config_changed')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, RecursionError):
        raise ValueError('invalid_mcp_config_file') from None
    if not isinstance(value, dict) or set(value) != {'servers'} or not isinstance(value['servers'], list) or not 1 <= len(value['servers']) <= 8:
        raise ValueError('invalid_mcp_config')
    servers, names = [], set()
    common = {'name','transport','timeout','max_bytes','max_pages','max_tools'}
    for spec in value['servers']:
        if (not isinstance(spec, dict) or not isinstance(spec.get('name'),str)
                or not _SERVER_NAME.fullmatch(spec['name']) or spec['name'] in names):
            raise ValueError('invalid_mcp_server_config')
        transport=spec.get('transport','stdio')
        if transport=='stdio':
            if (set(spec)-(common|{'command','cwd'}) or not {'command','cwd'}<=set(spec)
                    or not isinstance(spec['command'],list) or not spec['command'] or not isinstance(spec['cwd'],str)):
                raise ValueError('invalid_mcp_server_config')
            command=spec['command']
            if any(not isinstance(arg,str) for arg in command):raise ValueError('invalid_mcp_server_command')
            _outside(command[0],root,allow_symlink=True)
            cwd=_outside(spec['cwd'],root)
            for arg in command[1:]:
                lower=arg.lower().replace('_','-')
                if any(lower==flag or lower.startswith(flag+'=') for flag in ('--api-key','--token','--password','--authorization','--secret')):
                    raise ValueError('mcp_credentials_not_supported')
            normalized={'name':spec['name'],'command':list(command),'cwd':str(cwd)}
        elif transport=='streamable_http':
            if set(spec)-(common|{'url','allow_loopback_http'}) or 'url' not in spec:
                raise ValueError('invalid_mcp_server_config')
            normalized={'name':spec['name'],'transport':transport,'url':spec['url'],
                        'allow_loopback_http':spec.get('allow_loopback_http',False)}
        else:raise ValueError('invalid_mcp_transport')
        options = {'timeout': spec.get('timeout', 3.0), 'max_bytes': spec.get('max_bytes', 65536),
                   'max_pages': spec.get('max_pages', 8), 'max_tools': spec.get('max_tools', 32)}
        if (type(options['timeout']) not in (int, float) or not math.isfinite(options['timeout']) or not 0 < options['timeout'] <= 60
                or type(options['max_bytes']) is not int or not 512 <= options['max_bytes'] <= MAX_RESULT_BYTES
                or type(options['max_pages']) is not int or not 1 <= options['max_pages'] <= 16
                or type(options['max_tools']) is not int or not 1 <= options['max_tools'] <= 64):
            raise ValueError('invalid_mcp_server_limits')
        normalized.update(options)
        # Constructor validates authority and launches/connects nothing.
        _client(normalized)
        servers.append(normalized)
        names.add(spec['name'])
    return path, hashlib.sha256(raw).hexdigest(), servers


class MCPToolRegistry:
    def __init__(self, base_tools, config_path):
        if getattr(base_tools, 'profile', None) != 'coding':
            raise ValueError('mcp_requires_coding_profile')
        self.read_only_policy = getattr(base_tools, 'read_only_policy', None)
        if self.read_only_policy is not None and self.read_only_policy.paths:
            raise ValueError('read_only_policy_mcp_unsupported')
        self.base, self.root, self.profile = base_tools, base_tools.root, base_tools.profile
        self.node_executable = base_tools.node_executable
        self.python_environment = base_tools.python_environment
        self.command_backend = base_tools.command_backend
        self.tool_executables = base_tools.tool_executables
        self.allow_workspace_executables=base_tools.allow_workspace_executables
        self.tool_read_paths=base_tools.tool_read_paths
        self.tool_sysctl_read=base_tools.tool_sysctl_read
        self.command_runtime_policy = base_tools.command_runtime_policy
        self.selected_skills = getattr(base_tools, 'selected_skills', ())
        self.config_path, self._config_hash, self._specs = _read_config(config_path, self.root)
        self._clients, self._bindings, self._schemas = {}, {}, None
        self._resource_servers={}
        self._lock, self._entered, self._closed = threading.RLock(), False, False
        self.identity, self.manifest = None, None

    def validate_command_runtime(self):
        return self.base.validate_command_runtime()

    def __enter__(self):
        with self._lock:
            if self._entered or self._closed:
                raise ValueError('mcp_registry_not_new')
            self._entered = True
            try:
                schemas = copy.deepcopy(self.base.schemas())
                names = {schema['function']['name'] for schema in schemas} | {'history_lookup'}
                manifest = {'version': 1, 'config_path': str(self.config_path), 'config_sha256': self._config_hash,
                            'builtin_schema_sha256': _hash(schemas), 'servers': [], 'tools': []}
                for spec in self._specs:
                    name = spec['name']
                    client = _client(spec)
                    self._clients[name] = client  # Own the resource before startup can fail.
                    client.start()
                    if client.supports('resources'):self._resource_servers[name]=spec
                    if not client.supports('tools') and not client.supports('resources'):raise MCPError('no_supported_features')
                    catalog = client.list_tools() if client.supports('tools') else []
                    manifest['servers'].append({'name': name, 'config_sha256': _hash(spec), 'protocol_version': client.protocol_version})
                    for definition in catalog:
                        if len(manifest['tools']) >= 64 or len(_json(definition['inputSchema']).encode()) > 16384:
                            raise ValueError('mcp_catalog_limit')
                        if 'description' in definition and not isinstance(definition['description'], str):
                            raise ValueError('invalid_mcp_tool_description')
                        wire = 'mcp_' + _hash({'server': name, 'tool': definition['name']})[:40]
                        if wire in names:
                            raise ValueError('mcp_tool_name_collision')
                        names.add(wire)
                        description = definition.get('description', '').encode('utf-8')[:2048].decode('utf-8', errors='ignore')
                        authority='HTTP service' if spec.get('transport')=='streamable_http' else 'process'
                        schemas.append({'type': 'function', 'function': {'name': wire,
                            'description': 'MCP %s/%s; external trusted %s; approval required. %s' % (name, definition['name'], authority, description),
                            'parameters': copy.deepcopy(definition['inputSchema'])}})
                        entry = {'name': wire, 'server': name, 'tool': definition['name'], 'definition_sha256': _hash(definition)}
                        manifest['tools'].append(entry)
                        self._bindings[wire] = {'spec': spec, 'entry': entry}
                if self._resource_servers:
                    resource_schemas=_resource_schemas()
                    if any(item['function']['name'] in names for item in resource_schemas):raise ValueError('mcp_tool_name_collision')
                    schemas.extend(resource_schemas)
                    manifest['resource_servers']=[{'name':name,'config_sha256':_hash(spec)} for name,spec in sorted(self._resource_servers.items())]
                    manifest['resource_schema_sha256']=_hash(resource_schemas)
                if len(_json(schemas).encode()) > MAX_CATALOG_BYTES:
                    raise ValueError('mcp_catalog_limit')
                self._schemas, self.manifest = schemas, manifest
                self.identity = _hash(manifest)
                return self
            except BaseException as error:
                self.close()
                if isinstance(error, MCPError):
                    raise ValueError('mcp_startup_failed') from None
                raise

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            failed = False
            for client in self._clients.values():
                try:
                    client.close()
                except Exception:
                    failed = True
            if failed:
                raise ValueError('mcp_shutdown_failed')

    def schemas(self):
        if self._schemas is None:
            raise ValueError('mcp_registry_not_started')
        return copy.deepcopy(self._schemas)

    def _prepare_resource(self,name,args):
        if self._closed:raise ToolError('mcp_registry_closed')
        read=name=='read_mcp_resource'
        fields={'server','uri'} if read else {'server','cursor'}
        if (type(args) is not dict or set(args)-fields or 'server' not in args
                or (read and 'uri' not in args) or type(args['server']) is not str
                or args['server'] not in self._resource_servers):raise ToolError('invalid_mcp_resource_arguments')
        try:
            if read:_resource_string(args['uri'],uri=True)
            elif 'cursor' in args:_resource_string(args['cursor'])
            arguments=json.loads(_json(args))
        except (MCPError,ValueError,TypeError,UnicodeError):raise ToolError('invalid_mcp_resource_arguments') from None
        spec=self._resource_servers[args['server']];remote=spec.get('transport')=='streamable_http'
        action={'authority':'external_trusted_http' if remote else 'external_trusted_process','server':args['server'],
                'method':_RESOURCE_METHODS[name],'arguments':arguments,'manifest_identity':self.identity}
        if remote:action['url']=spec['url']
        else:action.update(command=copy.deepcopy(spec['command']),cwd=spec['cwd'])
        intent={'name':name,'args':arguments,'policy':'unsafe','requires_approval':True,
                'origin':{'type':'mcp_streamable_http' if remote else 'mcp_stdio','server':args['server'],'tool':name},
                'toolset_identity':self.identity,'approval_action':action}
        intent['action_digest']=_hash(intent)
        return intent

    def prepare(self, name, args, timeout=3):
        if name in _RESOURCE_METHODS and self._resource_servers:return self._prepare_resource(name,args)
        if name not in self._bindings:
            return self.base.prepare(name, args, timeout)
        if self._closed:
            raise ToolError('mcp_registry_closed')
        try:
            if not isinstance(args, dict):
                raise ValueError()
            raw = _json(args)
            if len(raw.encode('utf-8')) > MAX_RESULT_BYTES or json.loads(raw) != args:
                raise ValueError()
            arguments = json.loads(raw)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ToolError('invalid_mcp_arguments') from None
        binding = self._bindings[name]
        spec, entry = binding['spec'], binding['entry']
        remote=spec.get('transport')=='streamable_http'
        action = {'authority': 'external_trusted_http' if remote else 'external_trusted_process',
                  'server': entry['server'], 'tool': entry['tool'],'arguments': arguments,
                  'manifest_identity': self.identity, 'definition_sha256': entry['definition_sha256']}
        if remote:action['url']=spec['url']
        else:action.update({'command':copy.deepcopy(spec['command']),'cwd':spec['cwd']})
        intent = {'name': name, 'args': arguments, 'policy': 'unsafe', 'requires_approval': True,
                  'origin': {'type': 'mcp_streamable_http' if remote else 'mcp_stdio', 'server': entry['server'], 'tool': entry['tool']},
                  'toolset_identity': self.identity, 'approval_action': action}
        intent['action_digest'] = _hash(intent)
        return intent

    def execute(self, intent, timeout):
        resource=intent.get('name') in _RESOURCE_METHODS and bool(self._resource_servers)
        if intent.get('name') not in self._bindings and not resource:
            if intent.get('origin', {}).get('type') in _MCP_ORIGINS:
                raise ToolError('mcp_intent_mismatch')
            return self.base.execute(intent, timeout)
        with self._lock:
            if _json(self.prepare(intent['name'], intent.get('args'), timeout)) != _json(intent):
                raise ToolError('mcp_intent_mismatch')
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ToolError('tool_timeout')
            binding = None if resource else self._bindings[intent['name']]
            client = self._clients[intent['args']['server'] if resource else binding['entry']['server']]
            original_timeout = client.timeout
            client.timeout = min(original_timeout, timeout)
            try:
                # Engine continues to unwrap to base for native job/cancel
                # ownership. Consume its callback here without changing that
                # delegate chain or granting an external quiescence receipt.
                with client.cancellation_scope(self.base.cancellation_requested):
                    if resource:
                        args=intent['args']
                        if intent['name']=='read_mcp_resource':response=client.read_resource(args['uri'])
                        elif intent['name']=='list_mcp_resources':response=client.list_resources(args.get('cursor'))
                        else:response=client.list_resource_templates(args.get('cursor'))
                    else:response = client.call_tool(binding['entry']['tool'], intent['args'])
                if resource:
                    allowed=('contents',) if intent['name']=='read_mcp_resource' else (
                        'resources' if intent['name']=='list_mcp_resources' else 'resourceTemplates','nextCursor')
                    result={'ok':True,'server':intent['args']['server'],'origin':copy.deepcopy(intent['origin']),
                            **{key:response[key] for key in allowed if key in response}}
                    # Binary resources are not directly rendered or materialized.
                    for block in result.get('contents',[]):
                        if 'blob' in block:
                            raw=base64.b64decode(block.pop('blob'),validate=True)
                            block.update(blob_bytes=len(raw),blob_sha256=hashlib.sha256(raw).hexdigest(),omitted='binary_not_rendered')
                else:
                    result = {'ok': not response.get('isError', False), 'isError': response.get('isError', False),
                              'content': response['content'], 'origin': copy.deepcopy(intent['origin'])}
                    if 'structuredContent' in response:
                        result['structuredContent'] = response['structuredContent']
                    if not result['ok']:
                        result['error'] = 'mcp_tool_error'
                if len(_json(result).encode('utf-8')) > MAX_RESULT_BYTES:
                    raise MCPError('mcp_result_limit', uncertain=True)
                return result
            except BaseException as error:
                if resource and isinstance(error,MCPError) and error.code=='request_limit' and not error.uncertain:
                    # Only the exact local wire preflight takes this branch.
                    # Preserve the ready transport so corrected args can work;
                    # Engine's generic unsafe ToolError rule remains unchanged.
                    return {'ok':False,'error':'mcp_request_limit','origin':copy.deepcopy(intent['origin'])}
                client.close()
                if isinstance(error, MCPError):
                    if error.code=='request_cancelled':raise ToolCancelled(local_quiescent=False) from None
                    raise UncertainEffect('mcp_outcome_unknown') from None
                raise
            finally:
                client.timeout = original_timeout

    def recover(self, intent, timeout):
        if intent.get('name') in self._bindings or intent.get('origin', {}).get('type') in _MCP_ORIGINS:
            raise UncertainEffect('mcp_outcome_unknown')
        return self.base.recover(intent, timeout)
