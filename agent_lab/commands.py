"""Immutable, bounded command authority; never discovers ambient executables."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import sysconfig

from .tools import ToolError
from .command_runtime import canonical_node_path, executable_descriptor, runtime_policy

MAX_ARGUMENT_BYTES = 32768
MAX_ARGUMENTS = 128
MAX_WORKSPACE_ENTRIES = 10000
MAX_WORKSPACE_DEPTH = 32
SANDBOX_POLICY = 'managed_macos_v1_no_fork_hidden'


def _identity(path):
    info = path.stat()
    return {'device': info.st_dev, 'inode': info.st_ino}


def trusted_executables(node_executable=None, python_environment=None, command_backend='nofork', tool_executables=None, workspace=None):
    """Fixed aliases only; caller environments never affect resolution."""
    aliases = {'python': str(Path(sys.executable).resolve()), 'sh': '/bin/sh',
            'echo': '/bin/echo', 'cat': '/bin/cat',
            'true': '/usr/bin/true', 'false': '/usr/bin/false'}
    if command_backend == 'confined-fork':
        from .confined_backend import native_python_entry
        aliases['python'] = str(native_python_entry().resolve(strict=True))
    if node_executable is not None:
        aliases['node'] = canonical_node_path(node_executable)
    if python_environment is not None:
        aliases['python']=str(Path(python_environment)/'bin'/'python')
    if tool_executables:
        from .executables import executable_paths
        aliases.update(executable_paths(workspace,tool_executables))
    return aliases


def environment_policy(python_environment=None):
    result = {'version': 'clean_scratch_v1',
            'fixed': {'PATH': os.defpath, 'LANG': 'C.UTF-8',
                      'PYTHONDONTWRITEBYTECODE': '1',
                      'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
            'scratch_variables': ['HOME', 'TMPDIR'],
            'runtime': {'prefix': str(Path(sys.base_prefix).resolve()),
                        'stdlib': str(Path(sysconfig.get_path('stdlib')).resolve()),
                        'python_version': sys.version}}
    if python_environment is not None:
        result['fixed']['VIRTUAL_ENV']=str(python_environment)
    return result


def _check_workspace(root):
    """Reject visible hard-link aliases and special entries before authority."""
    count = 0
    def visit(directory, depth):
        nonlocal count
        if depth > MAX_WORKSPACE_DEPTH:
            raise ToolError('command_workspace_too_large')
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > MAX_WORKSPACE_ENTRIES:
                    raise ToolError('command_workspace_too_large')
                if entry.name.startswith('.') or entry.is_symlink():
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(entry.path, depth + 1)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ToolError('permission_denied')
    visit(root, 0)


def prepare_command(root, argv, cwd='.', *, read_only_paths=(), node_executable=None, python_environment=None, command_backend='nofork', tool_executables=None, allow_workspace_executables=False, tool_read_paths=None, tool_sysctl_read=None):
    """Prepare exact invocation and scope; this does not grant approval."""
    if (not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGUMENTS
            or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in argv)):
        raise ToolError('invalid_arguments')
    try:
        if len(json.dumps(argv, ensure_ascii=False).encode('utf-8')) > MAX_ARGUMENT_BYTES:
            raise ToolError('input_too_large')
        if (not isinstance(cwd, str) or not cwd or len(cwd.encode('utf-8')) > 512
                or '\x00' in cwd or '\\' in cwd):
            raise ToolError('invalid_path')
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise ToolError('permission_denied')
        root = root.resolve()
        from .permissions import ReadOnlyPolicy
        protection = ReadOnlyPolicy(root, read_only_paths)
        try:
            protection.check_native()
        except ValueError as error:
            raise ToolError(str(error)) from None
        current = root
        if cwd != '.':
            if Path(cwd).is_absolute() or any(not p or p in ('.', '..') or p.startswith('.') for p in cwd.split('/')):
                raise ToolError('permission_denied')
            for part in cwd.split('/'):
                current = current / part
                if current.is_symlink() or not current.is_dir():
                    raise ToolError('permission_denied')
        _check_workspace(root)
        runtime = runtime_policy(root, node_executable, python_environment, command_backend, tool_executables, allow_workspace_executables, tool_read_paths, tool_sysctl_read)
        environment=runtime.get('python_environment') if runtime else None
        environment_path=environment['path'] if environment else None
        aliases = trusted_executables(node_executable=node_executable,python_environment=environment_path,command_backend=command_backend,
            tool_executables={k:d['path'] for k,d in runtime.get('executables',{}).items()} if runtime else None,workspace=root)
        descriptor=workspace_program=None
        executable = aliases.get(argv[0])
        if executable is None:
            candidates = {str(Path(value).resolve()) for value in aliases.values()}
            if environment is not None:candidates.add(environment['launcher'])
            # Accept exact configured absolute paths, never PATH search or a
            # model-selected relative alias into a trusted executable.
            if Path(argv[0]).is_absolute() and argv[0] in candidates:
                executable=argv[0]
            elif allow_workspace_executables:
                from .workspace_exec import primary_program
                descriptor,workspace_program=primary_program(root,current,argv[0])
                executable=descriptor['path']
            else:raise ToolError('command_not_allowed')
        binary = Path(executable).resolve()
        invocation=environment['launcher'] if environment and executable==environment['launcher'] else str(binary)
        known=([runtime['node']] if runtime and 'node' in runtime else [])+list(runtime.get('executables',{}).values() if runtime else [])
        if descriptor is None:descriptor=next((item for item in known if item['path']==str(binary)),None)
        if descriptor is None:descriptor=executable_descriptor(binary)
        authority = {'name': 'run_command', 'policy': 'unsafe',
                     'args': {'argv': list(argv), 'cwd': cwd},
                     'requested_argv': list(argv), 'argv': [invocation, *argv[1:]],
                     'workspace': str(root), 'workspace_identity': _identity(root),
                     'cwd': str(current), 'cwd_identity': _identity(current),
                     'executable': descriptor,
                     'environment_policy': environment_policy(environment_path),
                     'sandbox_policy': ('managed_macos_v2_confined_fork_hidden' if command_backend=='confined-fork' else SANDBOX_POLICY),
                     'limits': {'max_seconds': 60, 'output_bytes_per_stream': 65536}}
        if workspace_program is not None:authority['workspace_executable']=workspace_program
        if runtime is not None:
            authority['command_runtime_policy'] = runtime
        if protection.paths:
            authority['read_only_policy'] = protection.manifest
            authority['read_only_policy_identity'] = protection.identity
        authority['command_digest'] = hashlib.sha256(json.dumps(
            authority, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
            allow_nan=False).encode('ascii')).hexdigest()
        return authority
    except (OSError, UnicodeError, ValueError, TypeError):
        raise ToolError('invalid_command') from None


def validate_command(root, intent, *, read_only_paths=(), node_executable=None, python_environment=None, command_backend='nofork', tool_executables=None, allow_workspace_executables=False, tool_read_paths=None, tool_sysctl_read=None):
    """Recompute every authority field immediately before trusted execution."""
    try:
        if not isinstance(intent, dict) or not isinstance(intent.get('args'), dict):
            raise ToolError('invalid_command')
        args = intent['args']
        if set(args) != {'argv', 'cwd'}:
            raise ToolError('invalid_command')
        expected = prepare_command(root, args['argv'], args['cwd'], read_only_paths=read_only_paths,
                                   node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
        # Serialized comparison distinguishes booleans from integers as well as
        # rejecting injected fields; JSON is the only supported intent format.
        if json.dumps(intent, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            raise ToolError('command_changed')
        return expected
    except (TypeError, ValueError, KeyError, UnicodeError):
        raise ToolError('invalid_command') from None
