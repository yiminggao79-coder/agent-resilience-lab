"""Owned C fixture compiler; Host compilation is NOT Agent/model build evidence."""
import hashlib
import os
from pathlib import Path
import subprocess


def compile_c(root,name,source):
    root=Path(root);directory=root.parent/'compiler-scratch';directory.mkdir(exist_ok=True)
    code=root/(name+'.c');binary=root/name
    code.write_text(source)
    result=subprocess.run(['/usr/bin/clang',str(code),'-o',str(binary)],
        env={'PATH':os.defpath,'LANG':'C','TMPDIR':str(directory)},capture_output=True,text=True,timeout=10)
    if result.returncode:raise AssertionError('owned fixture compilation failed: '+result.stderr)
    return binary,{'agent_compilation':False,'compile_exit_code':result.returncode,
                   'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                   'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                   'compiler_stderr_bytes':len(result.stderr.encode())}
