"""Project skill text snapshots; helper execution reuses approved commands."""
import errno
import copy
import hashlib
import os
from pathlib import Path
import stat

from .instructions import (InstructionError,GUIDANCE_BOUNDARY,MAX_FILE_BYTES,_root,_name,
                           _directory,_visible_parts,_frontmatter,_bounded_result)


def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _text_at(directory,name):
    descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    try:
        before=os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:raise InstructionError('invalid_skill_resource_file')
        if before.st_size>MAX_FILE_BYTES:raise InstructionError('skill_resource_limit')
        chunks=[];total=0
        while True:
            chunk=os.read(descriptor,min(4096,MAX_FILE_BYTES-total+1))
            if not chunk:break
            chunks.append(chunk);total+=len(chunk)
            if total>MAX_FILE_BYTES:raise InstructionError('skill_resource_limit')
        after=os.fstat(descriptor);current=os.stat(name,dir_fd=directory,follow_symlinks=False)
        if _stamp(before)!=_stamp(after) or _stamp(after)!=_stamp(current) or total!=before.st_size:
            raise InstructionError('skill_resource_changed')
        raw=b''.join(chunks)
        try:text=raw.decode('utf-8')
        except UnicodeError:raise InstructionError('invalid_skill_resource_utf8') from None
        return text,hashlib.sha256(raw).hexdigest(),total
    finally:os.close(descriptor)


def _parts(resource):
    parts=_visible_parts(resource)
    if any(ord(c)<32 or ord(c)==127 for c in resource):raise InstructionError('invalid_instruction_scope')
    return parts


def read_skill_resource(root,name,resource,*,explicit=False):
    """Read one full bounded text resource from the same pinned skill directory.

    Source metadata and resource bytes are separate reads, not a frozen package.
    Neither this read nor skill text grants native hidden-directory access.
    """
    if type(explicit) is not bool:raise InstructionError('invalid_skill_selection')
    name=_name(name);parts=_parts(resource);root=_root(root)
    try:
        with _directory(root,['.agents','skills',name]) as skill:
            body,skill_sha,_=_text_at(skill,'SKILL.md')
            metadata=_frontmatter(body,name,explicit=explicit)
            if metadata['disable_model_invocation'] and not explicit:raise InstructionError('skill_model_invocation_disabled')
            directory=os.dup(skill)
            try:
                for part in parts[:-1]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    os.close(directory);directory=child
                text,source_sha,size=_text_at(directory,parts[-1])
            finally:os.close(directory)
        return _bounded_result({'ok':True,'kind':'skill_resource','guidance':GUIDANCE_BOUNDARY,'skill':name,
            'skill_path':'.agents/skills/'+name+'/SKILL.md','skill_sha256':skill_sha,
            'resource':resource,'path':'.agents/skills/'+name+'/'+resource,'sha256':source_sha,'bytes':size,'content':text})
    except OSError as error:
        if error.errno==errno.ENOENT:code='skill_resource_not_found'
        elif error.errno in (errno.ELOOP,errno.ENOTDIR):code='skill_resource_symlink'
        else:code='skill_resource_io_error'
        raise InstructionError(code) from None


def prepare_skill_script(tools,name,resource,args,cwd,timeout,*,explicit=False):
    parts=_parts(resource)
    if len(parts)<2 or parts[0]!='scripts' or not parts[-1].endswith('.py'):
        raise InstructionError('skill_script_not_supported')
    if (type(args) is not list or len(args)>124 or any(type(arg) is not str or not arg or '\x00' in arg for arg in args)
            or type(cwd) is not str):raise InstructionError('invalid_skill_script_arguments')
    loaded=read_skill_resource(tools.root,name,resource,explicit=explicit)
    origin=str(Path(tools.root)/loaded['path'])
    # compile gives the original body its own future-import/module syntax and
    # filename without reading that hidden path at execution time. The captured
    # source, hash and args are in canonical argv and therefore approval-bound.
    program=('# Agent Resilience Lab skill snapshot sha256='+loaded['sha256']+'\n'
        +'import sys\nsys.argv[0]='+repr(origin)+'\n__file__='+repr(origin)+'\n'
        +'exec(compile('+repr(loaded['content'])+',__file__,"exec"))\n')
    intent=tools.prepare('run_command',{'argv':['python','-I','-c',program,*args],'cwd':cwd},timeout)
    snapshot={'purpose':'prepared_command_source','path':loaded['path'],'sha256':loaded['sha256'],
        'skill_sha256':loaded['skill_sha256'],'content':loaded['content'],'command_intent':copy.deepcopy(intent)}
    return intent,snapshot
