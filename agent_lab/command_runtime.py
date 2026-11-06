"""Host-selected runtime identity; no environment discovery or directory grants."""
import hashlib
import os
from pathlib import Path
import stat
import unicodedata

from .tools import ToolError

MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
NODE_SYSCTL_READ = ('hw.machine', 'hw.pagesize_compat', 'kern.hostname',
                    'kern.osrelease', 'kern.ostype', 'kern.version')


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def executable_descriptor(path, *, require_single_link=False):
    """Hash a regular executable from one stable FD, with bounded reads.

    The returned shape is the legacy command executable descriptor. Additional
    mode/ctime checks establish stability without changing approval identity.
    """
    descriptor = None
    try:
        binary = Path(path).resolve(strict=True)
        before = binary.stat()
        if (not stat.S_ISREG(before.st_mode) or not os.access(binary, os.X_OK)
                or (require_single_link and before.st_nlink != 1)):
            raise ToolError('command_not_allowed')
        if before.st_size > MAX_EXECUTABLE_BYTES:
            raise ToolError('invalid_command')
        descriptor = os.open(str(binary), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if _stamp(opened) != _stamp(before):
            raise ToolError('invalid_command')
        digest, total = hashlib.sha256(), 0
        while True:
            data = os.read(descriptor, min(HASH_CHUNK_BYTES, MAX_EXECUTABLE_BYTES - total + 1))
            if not data:
                break
            total += len(data)
            if total > MAX_EXECUTABLE_BYTES:
                raise ToolError('invalid_command')
            digest.update(data)
        after = os.fstat(descriptor)
        current = os.stat(binary, follow_symlinks=False)
        if _stamp(opened) != _stamp(after) or _stamp(after) != _stamp(current) or total != opened.st_size:
            raise ToolError('invalid_command')
        return {'path': str(binary), 'device': opened.st_dev, 'inode': opened.st_ino,
                'size': opened.st_size, 'mtime_ns': opened.st_mtime_ns, 'sha256': digest.hexdigest()}
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ToolError('invalid_command') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _node_input(value):
    if (not isinstance(value, (str, Path)) or not str(value) or '\x00' in str(value)
            or not Path(value).is_absolute()):
        raise ToolError('command_runtime_not_allowed')
    return Path(value)


def canonical_node_path(node_executable):
    """Map an explicit host selection to a canonical alias, never PATH lookup."""
    try:
        return str(_node_input(node_executable).resolve(strict=True))
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ToolError('command_runtime_not_allowed') from None


def _inside(candidate, root):
    # Conservative aliases also cover case/normalization differences on macOS.
    key = lambda path: tuple(unicodedata.normalize('NFD', part).casefold() for part in path.parts)
    candidate, root = key(candidate), key(root)
    return candidate[:len(root)] == root


def runtime_policy(root, node_executable=None, python_environment=None, command_backend='nofork', tool_executables=None, allow_workspace_executables=False, tool_read_paths=None, tool_sysctl_read=None):
    from .confined_backend import backend_descriptor
    backend = backend_descriptor(root, command_backend)
    from .executables import executable_descriptors
    executables=executable_descriptors(root,tool_executables)
    from .workspace_exec import workspace_exec_policy
    workspace_exec=workspace_exec_policy(root,allow_workspace_executables)
    from .tool_resources import resource_policy,sysctl_reads
    resources=resource_policy(root,tool_read_paths);named_reads=sysctl_reads(tool_sysctl_read)
    if node_executable is None and python_environment is None and backend is None and not executables and workspace_exec is None and resources is None and not named_reads:
        return None
    try:
        result={'version':1}
        if resources is not None:result['read_resources']=resources
        if named_reads:result['tool_sysctl_read']=named_reads
        if workspace_exec is not None:result['workspace_exec']=workspace_exec
        if executables:result['executables']=executables
        if backend is not None:result['backend']=backend
        if python_environment is not None:
            from .python_environment import environment_descriptor
            result['python_environment']=environment_descriptor(root,python_environment,
                **({'base_executable':backend['entry']['path']} if backend else {}))
        if node_executable is None:return result
        selected = _node_input(node_executable)
        lexical_root = Path(root).absolute()
        canonical_root = lexical_root.resolve(strict=True)
        if not canonical_root.is_dir() or _inside(selected, lexical_root) or _inside(selected, canonical_root):
            raise ToolError('command_runtime_not_allowed')
        canonical = Path(canonical_node_path(selected))
        if _inside(canonical, lexical_root) or _inside(canonical, canonical_root):
            raise ToolError('command_runtime_not_allowed')
        result.update({'node':executable_descriptor(canonical,require_single_link=True),
                       'sysctl_read':list(NODE_SYSCTL_READ)})
        return result
    except (ToolError, OSError, ValueError, TypeError, RuntimeError):
        raise ToolError('command_runtime_not_allowed') from None
