"""Explicit bounded Host executable grants, separate from model tool arguments."""
import re
import os
import stat
from pathlib import Path

from .tools import ToolError

MAX_EXECUTABLES=16
RESERVED_ALIASES=frozenset(('python','sh','echo','cat','true','false','node'))


def _alias(value):
    return (type(value) is str and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',value) is not None
            and value not in RESERVED_ALIASES)


def parse_executable_options(values):
    """CLI syntax only; filesystem admission happens in Tools before model I/O."""
    if not values:return None
    if len(values)>MAX_EXECUTABLES:raise ValueError('invalid_tool_executables')
    selected={}
    for item in values:
        alias,separator,path=item.partition('=')
        if not separator or not _alias(alias) or not path or alias in selected:
            raise ValueError('invalid_tool_executables')
        selected[alias]=path
    return selected


def _outside_route(root,path):
    """Resolve bounded file AND directory links, rejecting every workspace hop.

    Checking only resolve()'s final target misses a writable intermediate link.
    This is admission validation, not an immutable snapshot against Host races.
    """
    from .command_runtime import _inside
    current=Path(path.anchor).resolve(strict=True);pending=list(path.parts[1:]);links=0
    while pending:
        if len(pending)>512:raise ValueError()
        component=pending.pop(0)
        if component=='..':
            current=current.parent
            continue
        current=current/component
        if _inside(current,root):raise ValueError()
        info=current.lstat()
        if stat.S_ISLNK(info.st_mode):
            links+=1
            if links>40:raise ValueError()
            target=Path(os.readlink(current))
            if not target.is_absolute():target=current.parent/target
            if len(str(target))+sum(len(p)+1 for p in pending)>4096:raise ValueError()
            pending=list(target.parts[1:])+pending;current=Path(target.anchor).resolve(strict=True)
        elif pending and not stat.S_ISDIR(info.st_mode):
            raise ValueError()
    if _inside(current,root):raise ValueError()
    return current


def executable_paths(workspace,selected):
    """Canonical literal paths only, no directory/loader/resource grants or hash.

    A symlink can be a Host configuration convenience; every lexical ancestor
    and canonical target must stay outside the writable target workspace.
    """
    if selected is None:return {}
    from .command_runtime import _inside
    try:
        if type(selected) is not dict or len(selected)>MAX_EXECUTABLES or any(not _alias(k) for k in selected):
            raise ValueError()
        root=Path(workspace).resolve(strict=True);paths={}
        for alias,value in sorted(selected.items()):
            if (not isinstance(value,(str,Path)) or not str(value) or len(str(value))>4096
                    or not str(value).isascii() or any(ord(c)<32 or ord(c)==127 for c in str(value))):
                raise ValueError()
            lexical=Path(value)
            if not lexical.is_absolute():raise ValueError()
            canonical=_outside_route(root,lexical)
            if (_inside(lexical,root) or _inside(canonical,root)
                    or any(_inside(parent.resolve(),root) for parent in lexical.parents)):
                raise ValueError()
            paths[alias]=str(canonical)
        return paths
    except (OSError,ValueError,TypeError,RuntimeError):
        raise ToolError('command_runtime_not_allowed') from None


def executable_descriptors(workspace,selected):
    from .command_runtime import executable_descriptor, MAX_EXECUTABLE_BYTES
    paths=executable_paths(workspace,selected)
    try:
        # One existing bounded stable-FD hash primitive, not a language runner.
        if sum(Path(p).stat().st_size for p in paths.values())>MAX_EXECUTABLE_BYTES:
            raise ValueError()
        result={alias:executable_descriptor(path,require_single_link=True) for alias,path in paths.items()}
        if sum(d['size'] for d in result.values())>MAX_EXECUTABLE_BYTES:raise ValueError()
        return result
    except (OSError,ValueError,TypeError,RuntimeError,ToolError):
        raise ToolError('command_runtime_not_allowed') from None
