"""Host-selected same-base venv authority, bounded and separate from workspace."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from .tools import ToolError

MAX_PACKAGE_BYTES=512*1024*1024
MAX_PACKAGE_ENTRIES=20000
MAX_PACKAGE_DEPTH=32
CHUNK_BYTES=1024*1024
DENY_FCNTL=(80,110)


def _stamp(info):
    return [info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def _file(path,limit,*,keep=False):
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit:
        raise ValueError()
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        opened=os.fstat(fd)
        if _stamp(before)!=_stamp(opened):raise ValueError()
        digest=hashlib.sha256();total=0;body=bytearray()
        while True:
            chunk=os.read(fd,min(CHUNK_BYTES,limit-total+1))
            if not chunk:break
            total+=len(chunk)
            if total>limit:raise ValueError()
            digest.update(chunk)
            if keep:body.extend(chunk)
        if total!=opened.st_size or _stamp(opened)!=_stamp(os.fstat(fd)) or _stamp(opened)!=_stamp(path.lstat()):
            raise ValueError()
        return {'stamp':_stamp(opened),'sha256':digest.hexdigest()},bytes(body)
    finally:os.close(fd)


def _tree(root):
    digest=hashlib.sha256();entries=files=total=0
    def record(relative,kind,detail):
        digest.update(json.dumps([relative,kind,detail],sort_keys=True,ensure_ascii=True,
                                 separators=(',',':')).encode('ascii')+b'\n')
    def visit(directory,depth):
        nonlocal entries,files,total
        if depth>MAX_PACKAGE_DEPTH:raise ValueError()
        before=directory.lstat()
        if not stat.S_ISDIR(before.st_mode):raise ValueError()
        children=[]
        with os.scandir(directory) as stream:
            for entry in stream:
                entries+=1
                if entries>MAX_PACKAGE_ENTRIES or entry.name.startswith('.') or len(str(entry.path))>4096:
                    raise ValueError()
                children.append(entry.name)
        record(directory.relative_to(root).as_posix(),'directory',_stamp(before))
        for name in sorted(children):
            path=directory/name
            if stat.S_ISDIR(path.lstat().st_mode):visit(path,depth+1)
            else:
                detail,_=_file(path,MAX_PACKAGE_BYTES-total)
                total+=detail['stamp'][4];files+=1
                record(path.relative_to(root).as_posix(),'file',detail)
        if _stamp(before)!=_stamp(directory.lstat()):raise ValueError()
    visit(root,0)
    return {'path':str(root),'device':root.stat().st_dev,'inode':root.stat().st_ino,
            'sha256':digest.hexdigest(),'files':files,'entries':entries,'bytes':total}


def environment_descriptor(workspace,selected,*,base_executable=None):
    """No code execution, environment discovery, source body or secrets in errors."""
    from .command_runtime import _inside,executable_descriptor
    try:
        if not isinstance(selected,(str,Path)) or not Path(selected).is_absolute():raise ValueError()
        selected=Path(selected);root=Path(workspace).resolve(strict=True)
        env=selected.resolve(strict=True)
        if (selected.is_symlink() or not env.is_dir() or not str(env).isascii()
                or any(ord(c)<32 or ord(c)==127 for c in str(env))
                or _inside(env,root) or _inside(root,env)
                or any(_inside(p.resolve(),root) for p in selected.parents)):
            raise ValueError()
        minor='python%d.%d'%sys.version_info[:2]
        packages=env/'lib'/minor/'site-packages'
        for directory in (env/'bin',env/'lib',env/'lib'/minor,packages):
            if directory.is_symlink() or not directory.is_dir():raise ValueError()
        cfg,raw=_file(env/'pyvenv.cfg',4096,keep=True)
        values={}
        for line in raw.decode('utf-8').splitlines():
            if not line.strip():continue
            key,separator,value=line.partition('=');key=key.strip().lower();value=value.strip()
            if not separator or not key or key in values:raise ValueError()
            values[key]=value
        if (values.get('include-system-site-packages','').lower()!='false'
                or values.get('version','').split('.')[:2]!=[str(n) for n in sys.version_info[:2]]):
            raise ValueError()
        home=Path(values.get('home',''))
        base=Path(base_executable if base_executable is not None else sys.executable).resolve(strict=True)
        allowed_homes=({base.parent} if base_executable is not None else
                       {base.parent,Path(getattr(sys,'_base_executable',sys.executable)).absolute().parent.resolve()})
        if not home.is_absolute() or home.resolve(strict=True) not in allowed_homes:raise ValueError()
        launcher=env/'bin'/'python';current=launcher;links=[];seen=set()
        for _ in range(16):
            if (current in seen or _inside(current,root)
                    or any(_inside(parent.resolve(),root) for parent in current.parents)):
                raise ValueError()
            seen.add(current);info=current.lstat()
            if not stat.S_ISLNK(info.st_mode):break
            target=os.readlink(current)
            if _stamp(info)!=_stamp(current.lstat()):raise ValueError()
            links.append({'path':str(current),'target':target,'stamp':_stamp(info)})
            current=Path(os.path.normpath(target if os.path.isabs(target) else str(current.parent/target)))
        else:raise ValueError()
        if current.resolve(strict=True)!=base or not links:raise ValueError()
        environment_info=env.stat()
        result={'path':str(env),'device':environment_info.st_dev,'inode':environment_info.st_ino,
                'launcher':str(launcher),'links':links,'config':cfg,
                'deny_fcntl':list(DENY_FCNTL),
                'base_executable':executable_descriptor(base),'packages':_tree(packages)}
        if cfg!=_file(env/'pyvenv.cfg',4096)[0]:raise ValueError()
        return result
    except (OSError,ValueError,TypeError,RuntimeError,UnicodeError,ToolError):
        raise ToolError('command_runtime_not_allowed') from None


def validate_environment(workspace,selected,expected=None,*,base_executable=None):
    current=environment_descriptor(workspace,selected,base_executable=base_executable)
    if expected is not None and json.dumps(current,sort_keys=True)!=json.dumps(expected,sort_keys=True):
        raise ToolError('command_runtime_changed')
    return current
