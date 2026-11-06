"""Explicit Host workspace execution scope and primary image admission.

Primary hash/mode validation is a preparation check, not an immutable exec FD or
a claim that every secondary image in the granted scope is individually pinned.
"""
import os
from pathlib import Path
import stat

from .tools import ToolError


def workspace_exec_policy(root,enabled=False):
    if type(enabled) is not bool:raise ToolError('command_runtime_not_allowed')
    if not enabled:return None
    try:
        root=Path(root).resolve(strict=True);info=root.stat()
        if not stat.S_ISDIR(info.st_mode):raise ValueError()
        return {'version':1,'root':str(root),'device':info.st_dev,'inode':info.st_ino,
                'scope':'visible_workspace_images',
                'primary_validation':'stable_fd_sha256_and_ordinary_mode',
                'secondary_images':'scope_grant_not_individual_approval'}
    except (OSError,ValueError,TypeError,RuntimeError):
        raise ToolError('command_runtime_not_allowed') from None


def primary_program(root,cwd,requested):
    from .command_runtime import executable_descriptor,_stamp
    try:
        if (type(requested) is not str or len(requested)>4096 or '\\' in requested
                or any(ord(c)<32 or ord(c)==127 for c in requested)):
            raise ValueError()
        root=Path(root);path=Path(requested)
        if not path.is_absolute():
            if not requested.startswith('./'):raise ValueError()
            path=Path(cwd)/path
        parts=path.relative_to(root).parts
        if not parts or any(p in ('.','..') or p.startswith('.') for p in parts):raise ValueError()
        current=root
        for part in parts:
            current=current/part
            if current.is_symlink():raise ValueError()
            if current!=path and not current.is_dir():raise ValueError()
        before=path.lstat();mode=stat.S_IMODE(before.st_mode)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or mode&~0o777 or not os.access(path,os.X_OK):
            raise ValueError()
        descriptor=executable_descriptor(path,require_single_link=True)
        if _stamp(before)!=_stamp(path.lstat()):raise ValueError()
        return descriptor,{'path':'/'.join(parts),'mode':mode}
    except (OSError,ValueError,TypeError,RuntimeError,ToolError):
        raise ToolError('command_not_allowed') from None
