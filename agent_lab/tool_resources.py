"""Host trusted readonly input scopes, deliberately not directory snapshots."""
from pathlib import Path
import stat

from .tools import ToolError

MAX_READ_PATHS=8
MAX_READ_FILE_BYTES=256*1024*1024
DENY_FCNTL=(80,110)
COARSE_ROOTS=frozenset(('/', '/Users', '/Library', '/System', '/private', '/private/tmp',
                        '/tmp', '/usr', '/opt', '/Applications', '/usr/local', '/opt/homebrew'))


def resource_paths(workspace,selected):
    if selected is None:return []
    from .executables import _outside_route
    from .command_runtime import _inside
    try:
        if type(selected) not in (list,tuple) or len(selected)>MAX_READ_PATHS:raise ValueError()
        root=Path(workspace).resolve(strict=True);home=Path.home().resolve();result=[]
        for value in selected:
            if (not isinstance(value,(str,Path)) or not str(value) or len(str(value))>4096
                    or not str(value).isascii() or any(ord(c)<32 or ord(c)==127 for c in str(value))):raise ValueError()
            lexical=Path(value)
            if not lexical.is_absolute():raise ValueError()
            # Home/Users are never discovered or granted for tool resources.
            if any(_inside(lexical,p) or _inside(p,lexical) for p in (home,Path('/Users'))):raise ValueError()
            path=_outside_route(root,lexical)
            if (any(_inside(path,Path(p)) and _inside(Path(p),path) for p in COARSE_ROOTS) or _inside(path,root) or _inside(root,path)
                    or any(_inside(path,p) or _inside(p,path) for p in (home,Path('/Users')))):raise ValueError()
            info=path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise ValueError()
            if stat.S_ISREG(info.st_mode) and (info.st_nlink!=1 or info.st_size>MAX_READ_FILE_BYTES):raise ValueError()
            if any(_inside(path,p) or _inside(p,path) for p in result):raise ValueError()
            result.append(path)
        return sorted(result)
    except (OSError,ValueError,TypeError,RuntimeError):
        raise ToolError('command_runtime_not_allowed') from None


def resource_policy(workspace,selected):
    from .command_runtime import _stamp
    from .python_environment import _file
    try:
        paths=resource_paths(workspace,selected)
        if not paths:return None
        items=[]
        for path in paths:
            before=path.lstat()
            item={'path':str(path),'device':before.st_dev,'inode':before.st_ino,'mode':stat.S_IMODE(before.st_mode),
                  'mtime_ns':before.st_mtime_ns,'ctime_ns':before.st_ctime_ns}
            if stat.S_ISDIR(before.st_mode):
                item.update(kind='directory',contents='trusted_host_read_scope_not_snapshot')
            else:
                fingerprint,_=_file(path,MAX_READ_FILE_BYTES)
                item.update(kind='file',file=fingerprint)
            if _stamp(before)!=_stamp(path.lstat()):raise ValueError()
            items.append(item)
        return {'version':1,'paths':items,'deny_fcntl':list(DENY_FCNTL),'deny_file_link':True}
    except (OSError,ValueError,TypeError,RuntimeError,ToolError):
        raise ToolError('command_runtime_not_allowed') from None


def sysctl_reads(selected):
    if selected is None:return []
    from .command_runtime import NODE_SYSCTL_READ
    if (type(selected) not in (list,tuple) or len(selected)>len(NODE_SYSCTL_READ)
            or any(type(name) is not str or name not in NODE_SYSCTL_READ for name in selected)
            or len(set(selected))!=len(selected)):
        raise ToolError('command_runtime_not_allowed')
    return sorted(selected)
