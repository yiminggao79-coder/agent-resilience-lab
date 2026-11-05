"""Explicit Host configuration; no discovery, environment/key or permission engine."""
import argparse
import copy
import json
import os
from pathlib import Path
import stat

MAX_CONFIG_BYTES=32768
RUNTIME_COMMANDS=frozenset(('run','resume','continue','compact','chat','fork','approval','approve','deny','runtime-check'))
CLI_DEFAULTS={'read_only_path':[],'node_executable':None,'python_environment':None,'command_backend':'nofork',
              'tool_executable':[],'allow_workspace_executables':False,'tool_read_path':[],'tool_sysctl_read':[]}
CONFIG_KEYS=frozenset(('node_executable','python_environment','command_backend','tool_executables',
                       'allow_workspace_executables','tool_read_paths','tool_sysctl_read','read_only_paths'))


class RuntimeConfigurationError(ValueError):
    def __init__(self,code='invalid_runtime_config'):
        self.code=code
        super().__init__(code)


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError()
        result[key]=value
    return result


def _string(value,*,absolute=False):
    if (type(value) is not str or not value or len(value)>4096
            or any(ord(c)<32 or ord(c)==127 for c in value)
            or (absolute and not Path(value).is_absolute())):raise ValueError()


def _validate(config):
    if type(config) is not dict or set(config)-CONFIG_KEYS-{'version'} or type(config.get('version')) is not int or config['version']!=1:
        raise ValueError()
    options={key:value for key,value in config.items() if key!='version'}
    for key in ('node_executable','python_environment'):
        if key in options:_string(options[key],absolute=True)
    if 'command_backend' in options and (type(options['command_backend']) is not str or options['command_backend'] not in ('nofork','confined-fork')):raise ValueError()
    if 'allow_workspace_executables' in options and type(options['allow_workspace_executables']) is not bool:raise ValueError()
    for key,limit in (('tool_read_paths',8),('tool_sysctl_read',6),('read_only_paths',32)):
        if key not in options:continue
        if type(options[key]) is not list or len(options[key])>limit:raise ValueError()
        for value in options[key]:_string(value,absolute=key=='tool_read_paths')
    if 'tool_sysctl_read' in options:
        from .tool_resources import sysctl_reads
        sysctl_reads(options['tool_sysctl_read'])
    if 'tool_executables' in options:
        from .executables import _alias,MAX_EXECUTABLES
        values=options['tool_executables']
        if type(values) is not dict or len(values)>MAX_EXECUTABLES or any(not _alias(key) for key in values):raise ValueError()
        for value in values.values():_string(value,absolute=True)
    return options


def load_runtime_config(selected,workspace):
    """Stable bounded regular-file read; return existing Tools kwargs only.

    The file is a convenience input. Actual runtime descriptors and read-only
    policy, not this file's bytes/path, bind saved state and approvals.
    """
    from .executables import _outside_route
    from .command_runtime import _stamp
    from .tools import ToolError
    try:
        _string(str(selected),absolute=True)
        selected=Path(selected);root=Path(workspace).resolve(strict=True)
        if selected.is_symlink():raise ValueError()
        path=_outside_route(root,selected)
        before=path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>MAX_CONFIG_BYTES:raise ValueError()
        descriptor=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as source:
            opened=os.fstat(source.fileno())
            if _stamp(before)!=_stamp(opened):raise ValueError()
            raw=source.read(MAX_CONFIG_BYTES+1)
            after=os.fstat(source.fileno())
            if len(raw)>MAX_CONFIG_BYTES or _stamp(opened)!=_stamp(after) or _stamp(after)!=_stamp(path.lstat()):raise ValueError()
        config=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        return _validate(config)
    except (ValueError,OSError,UnicodeError,RuntimeError,RecursionError,ToolError,TypeError):
        raise RuntimeConfigurationError() from None


def add_runtime_arguments(item):
    # SUPPRESS retains whether a flag was supplied, even if it names the default
    # value or argparse accepted a unique abbreviation. No raw argv guessing.
    default=argparse.SUPPRESS
    item.add_argument('--runtime-config',default=None,metavar='ABS_JSON',
        help='explicit secret-free Host runtime JSON outside workspace; conflicts with runtime/read-only flags; repeat on resume/approval')
    item.add_argument('--allow-workspace-executables',action='store_true',default=default,
        help='explicitly permit visible workspace programs; primary image/mode approval-bound, secondary images share the scope; repeat on resume/approval')
    item.add_argument('--tool-read-path',action='append',default=default,metavar='ABS_PATH',
        help='explicit readonly trusted tool file/directory outside workspace and home; directory is a scope, not a snapshot; repeat saved grants on resume/approval')
    item.add_argument('--tool-sysctl-read',action='append',default=default,metavar='NAME',
        help='explicit safe named OS metadata read; no wildcard/write; repeat saved grants on resume/approval')
    item.add_argument('--tool-executable',action='append',default=default,metavar='ALIAS=ABS_PATH',
        help='explicit trusted executable outside workspace; repeat each saved grant on resume/approval; no parent directory or network grant')
    item.add_argument('--node-executable',default=default,
        help='explicit trusted absolute Node executable outside workspace; admits OS/hostname metadata reads; repeat saved runtime on resume/approval; no npm, fork or network')
    item.add_argument('--command-backend',choices=('nofork','confined-fork'),default=default,
        help='explicit experimental confined-fork on verified Darwin/CPython only; repeat on resume/approval; default forbids fork')
    item.add_argument('--python-environment',default=default,
        help='explicit trusted same-base venv outside workspace; bounded read-only dependencies; repeat on resume/approval; no automatic installation or network/fork grants')


def resolve_cli_runtime(args):
    if args.command not in RUNTIME_COMMANDS:return None
    explicit={key for key in CLI_DEFAULTS if hasattr(args,key)}
    selected=args.runtime_config
    if selected is not None and explicit:raise RuntimeConfigurationError('runtime_config_flag_conflict')
    options=load_runtime_config(selected,args.workspace) if selected is not None else {}
    mapping={'read_only_path':'read_only_paths','tool_read_path':'tool_read_paths'}
    for dest,default in CLI_DEFAULTS.items():
        if dest not in explicit:setattr(args,dest,copy.deepcopy(options.get(mapping.get(dest,dest),default)))
    from .executables import parse_executable_options
    return options.get('tool_executables') if selected is not None else parse_executable_options(args.tool_executable)
