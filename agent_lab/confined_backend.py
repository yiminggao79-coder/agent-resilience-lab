"""Host-selected experimental Darwin backend authority, never model input.

Only the Host ABI/runtime family exercised by the native failure matrix is
admitted. Default NoFork does not need libproc or this optional capability.
"""
from pathlib import Path
import platform
import sys

from .tools import ToolError

CLEANUP_SECONDS = 1.
MAX_GROUP_CAPACITY = 4096
DENY_SYSCALLS = (66, 82, 147, 244)


def native_python_entry():
    return Path(sys.base_prefix)/'Resources/Python.app/Contents/MacOS/Python'


def backend_descriptor(root, selection='nofork'):
    if type(selection) is not str or selection not in ('nofork', 'confined-fork'):
        raise ToolError('command_runtime_not_allowed')
    if selection == 'nofork':
        return None
    from .command_runtime import _inside, executable_descriptor
    from .process_group import DarwinProcesses, GroupObservationError
    try:
        if (platform.system() != 'Darwin' or platform.machine() != 'x86_64'
                or platform.release().split('.')[0] != '23'
                or sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 9)):
            raise ValueError()
        # A nondefault SIGCHLD policy cannot own this protocol; reject before
        # native launch rather than manufacture a cleanup attempt without anchor.
        if not DarwinProcesses().wait_policy_ok():
            raise ValueError()
        workspace = Path(root).resolve(strict=True)
        entry = native_python_entry()
        canonical = entry.resolve(strict=True)
        if (_inside(entry, workspace) or _inside(canonical, workspace)
                or any(_inside(parent.resolve(), workspace) for parent in entry.parents)):
            raise ValueError()
        return {'kind':'darwin_confined_fork_v1', 'entry':executable_descriptor(canonical),
                'system':platform.system(), 'release':platform.release(), 'machine':platform.machine(),
                'python_version':list(sys.version_info[:3]), 'deny_syscalls':list(DENY_SYSCALLS),
                'cleanup_seconds':CLEANUP_SECONDS, 'group_max_capacity':MAX_GROUP_CAPACITY}
    except (OSError, ValueError, TypeError, RuntimeError, GroupObservationError, ToolError):
        raise ToolError('command_runtime_not_allowed') from None
