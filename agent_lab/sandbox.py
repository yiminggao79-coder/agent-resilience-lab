"""Fail-closed macOS tests and bounded, fork-denying managed commands.

Seatbelt starts with deny all. The trusted supervisor retains the worker's
execution lease independently; project code inherits no control descriptors.
"""
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time

if __package__ in (None, ''):
    # Script entry point is absolute and -I; import only the trusted source tree.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_lab.tools import ToolError
else:
    from .tools import ToolError

from agent_lab.tool_control import NativeCancelled, owner_dead, owner_read_fd, OWNER_ENV, inherited_owner, cancelled_receipt
from agent_lab.native_job_runtime import current_tracker, supervisor_payload, supervisor_job, SUPERVISOR_ENV
from agent_lab.tool_output import emit_output, forwarded_output, inherited_output

BACKEND_PATH = '/usr/bin/sandbox-exec'
MAX_OUTPUT_BYTES = 65536
MAX_TEST_SECONDS = 60
MAX_PROTOCOL_BYTES = MAX_OUTPUT_BYTES * 12 + 4096
TEST_BOOTSTRAP = """import os,sys,traceback,unittest
sys.path.insert(0,os.getcwd())
code=2
try:
    start=sys.argv[1]
    if start not in ('.','tests'):
        raise ValueError('invalid_test_start_dir')
    loader=unittest.TestLoader()
    suite=loader.discover(start_dir=start,pattern='test*.py')
    if loader.errors:
        for error in loader.errors:
            print(error,file=sys.stderr)
    elif suite.countTestCases()==0:
        print('no_tests_discovered',file=sys.stderr)
        code=5
    else:
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        code=0 if result.testsRun>0 and result.wasSuccessful() else 1
except BaseException:
    traceback.print_exc()
raise SystemExit(code)
"""


def test_start_dir(root):
    """Select one conventional scope without importing project code.

    tests/ takes priority; root discovery is used when that directory is absent.
    Explicit scope pinning is available for evaluator baseline/final symmetry.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ToolError('permission_denied')
    if (root / 'tests').is_symlink():
        raise ToolError('permission_denied')
    return 'tests' if (root / 'tests').is_dir() else '.'


def _test_scope(root, start_dir):
    if start_dir is None:
        return test_start_dir(root)
    if not isinstance(start_dir, str) or start_dir not in ('.', 'tests'):
        raise ToolError('invalid_test_start_dir')
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or (start_dir == 'tests' and (root / 'tests').is_symlink()):
        raise ToolError('permission_denied')
    return start_dir



def _quote(value):
    return json.dumps(str(value), ensure_ascii=True)


def _validate_read_only(root, paths):
    from agent_lab.permissions import ReadOnlyPolicy
    try:
        protection = ReadOnlyPolicy(root, paths)
    except ValueError:
        raise ToolError('invalid_read_only_policy') from None
    try:
        protection.check_native()
    except ValueError as error:
        raise ToolError(str(error)) from None
    return protection


def _selected_environment(root,path,expected=None,*,base_executable=None):
    if path is None:
        if expected is not None:raise ToolError('command_runtime_changed')
        return None
    from agent_lab.python_environment import validate_environment
    return validate_environment(root,path,expected,base_executable=base_executable)


def _selected_backend(root, selection, expected=None):
    from agent_lab.confined_backend import backend_descriptor
    current=backend_descriptor(root,selection)
    if json.dumps(current,sort_keys=True)!=json.dumps(expected,sort_keys=True):
        raise ToolError('command_runtime_changed')
    return current


def _inner_collect(argv, root, env, timeout, backend=None):
    if backend is None:
        return _collect(argv,root,env,timeout,stream_output=True)
    from agent_lab.confined_collector import collect
    return collect(argv,root,env,timeout,cleanup_timeout=backend['cleanup_seconds'],stream_output=True)


def _profile(root, scratch, *, read_only_paths=(), python_environment_policy=None):
    protection = _validate_read_only(root, read_only_paths) if read_only_paths else None
    if protection is not None:
        try:
            protection.check_scratch(scratch)
        except ValueError as error:
            raise ToolError(str(error)) from None
    executable = Path(sys.executable).resolve()
    runtime = Path(sys.base_prefix).resolve()
    # Prefixes are interpreter installations, not arbitrary project settings.
    # Reject broad installations whose subtree would expose user data.
    if str(runtime) in ('/', '/Users', '/private', '/tmp', '/private/tmp'):
        raise ToolError('sandbox_unavailable')
    read_roots = {root, scratch, Path(sysconfig.get_path('stdlib')).resolve(), executable.parent,
                  runtime / 'Resources/Python.app/Contents/MacOS', Path('/System/Library'), Path('/usr/lib'),
                  Path('/System/Volumes/Preboot/Cryptexes/OS/usr/lib')}
    metadata = set()
    for path in read_roots | {executable, Path(sys.executable)}:
        metadata.update(path.parents)
    rules = ['(version 1)', '(deny default)',
             '(allow process-info* (target self))',
             '(allow signal (target children))',
             '(allow mach-lookup (global-name "com.apple.system.logger"))',
             '(allow process-exec (literal %s) (literal %s) (literal "/usr/bin/true"))' %
             (_quote(executable), _quote(sys.executable)),
             '(allow file-read* %s)' % ' '.join('(subpath %s)' % _quote(path) for path in sorted(read_roots)),
             '(allow file-read-metadata %s)' % ' '.join('(literal %s)' % _quote(path) for path in sorted(metadata)),
             # dyld reads the root directory itself; literal is not recursive.
             '(allow file-read-data (literal "/"))',
             '(allow file-read* (literal "/usr/bin/true") (literal %s) (literal %s))' % (_quote(sys.executable), _quote(runtime / 'Python')),
             '(allow process-exec (literal %s))' % _quote(runtime / 'Resources/Python.app/Contents/MacOS/Python'),
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
             '(allow file-write* (literal "/dev/null"))',
             '(allow file-write* (subpath %s) (subpath %s))' % (_quote(root), _quote(scratch)),
             '(deny network*)']
    # More specific denies override the workspace grant, including nested paths.
    control_pattern = '^' + re.escape(str(root)) + r'/(.*/)?\.agent-lab[^/]*(/.*)?$'
    cache_pattern = '^' + re.escape(str(root)) + r'/(.*/)?__pycache__(/.*)?$'
    git_pattern = '^' + re.escape(str(root)) + r'/(.*/)?\.git(/.*)?$'
    rules += ['(deny file-read* file-write* (regex %s))' % _quote(control_pattern),
              '(deny file-read* file-write* (regex %s))' % _quote(cache_pattern),
              '(deny file-write* (regex %s))' % _quote(git_pattern)]
    if read_only_paths:
        for scope in protection.paths:
            path = Path(root) / scope
            rules.append('(deny file-write* (literal %s) (subpath %s))' % (_quote(path), _quote(path)))
        for ancestor in protection.protected_ancestors():
            rules.append('(deny file-write-unlink (require-all (literal %s) (vnode-type DIRECTORY)))' % _quote(ancestor))
            # A missing ancestor must not become a symlink in the same process.
            # Exact creation denial also prevents replacing its authority anchor.
            rules.append('(deny file-write-create (literal %s))' % _quote(ancestor))
        rules.append('(deny file-link)')
        # These Darwin fcntls can mutate through a descriptor opened read-only.
        rules.append('(deny system-fcntl (fcntl-command 80 110))')
    if python_environment_policy is not None:
        from agent_lab.command_runtime import _inside
        selected=python_environment_policy;environment=Path(selected['path'])
        if _inside(Path(scratch).resolve(),environment) or _inside(environment,Path(scratch).resolve()):
            raise ToolError('command_runtime_not_allowed')
        packages=Path(selected['packages']['path'])
        exact={Path(link['path']) for link in selected['links']}|{environment/'pyvenv.cfg'}
        metadata={parent for path in exact|{packages} for parent in path.parents}
        hidden='^'+re.escape(str(packages))+r'/(.*/)?\.[^/]+(/.*)?$'
        rules.extend([
            '(allow process-exec (literal %s))'%_quote(selected['launcher']),
            '(allow file-read* %s)'%' '.join('(literal %s)'%_quote(p) for p in sorted(exact)),
            '(allow file-read* (subpath %s))'%_quote(packages),
            '(allow file-read-metadata %s)'%' '.join('(literal %s)'%_quote(p) for p in sorted(metadata)),
            '(deny file-write* (subpath %s))'%_quote(environment),
            '(deny system-fcntl (fcntl-command %s))'%' '.join(str(n) for n in selected['deny_fcntl']),
            '(deny file-read* (regex %s))'%_quote(hidden)])
    return '\n'.join(rules)


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _collect(argv, root, env, timeout, pass_fds=(), output_limit=MAX_OUTPUT_BYTES, monitor_owner=True, stream_output=False):
    """Drain both pipes continuously while retaining at most two bounded buffers."""
    if monitor_owner and owner_dead():
        raise NativeCancelled('tool_cancelled')
    tracker = current_tracker()
    if tracker is not None:
        if monitor_owner:
            tracker.native_begin()
        else:
            tracker.supervisor_begin()
    try:
        proc = subprocess.Popen(argv, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, close_fds=True, pass_fds=pass_fds)
    except OSError:
        if tracker is not None:
            (tracker.native_failed if monitor_owner else tracker.supervisor_failed)()
        raise ToolError('sandbox_unavailable')
    if tracker is not None and monitor_owner:
        tracker.native_started(proc.pid)
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = {'stdout': False, 'stderr': False}
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        for label, pipe in [('stdout', proc.stdout), ('stderr', proc.stderr)]:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, label)
        while selector.get_map() or proc.poll() is None:
            if monitor_owner and owner_dead():
                raise NativeCancelled('tool_cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError('tool_timeout')
            if not selector.get_map():
                time.sleep(min(remaining, .05))
            for key, _ in selector.select(min(remaining, .05)):
                data = os.read(key.fd, 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                if stream_output:
                    emit_output(label, data)
                capacity = output_limit - len(streams[label])
                streams[label].extend(data[:capacity])
                if len(data) > capacity:
                    truncated[label] = True
            if proc.poll() is not None:
                # A completed test parent must not leave a background child alive.
                _kill_group(proc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ToolError('tool_timeout')
        try:
            code = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ToolError('tool_timeout')
        for label in streams:
            if len(streams[label].decode('utf-8', errors='replace').encode('utf-8')) > output_limit:
                truncated[label] = True
        return {'ok': code == 0, 'exit_code': code,
                'stdout': _bounded_text(streams['stdout'], output_limit),
                'stderr': _bounded_text(streams['stderr'], output_limit),
                'stdout_truncated': truncated['stdout'], 'stderr_truncated': truncated['stderr']}
    finally:
        _kill_group(proc)
        proc.wait()
        if tracker is not None and monitor_owner:
            tracker.native_joined(proc.pid)
        selector.close()
        proc.stdout.close()
        proc.stderr.close()


def _bounded_text(data, limit):
    # Replacement characters can expand invalid input threefold in UTF-8.
    return data.decode('utf-8', errors='replace').encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def _run_tests(root, timeout, bootstrap=TEST_BOOTSTRAP, extra_args=(), *, read_only_paths=(), start_dir=None,
               python_environment=None,python_environment_policy=None,command_backend='nofork',backend_policy=None,
               tool_executables=None,node_executable=None,command_runtime_policy=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    """Run a fixed isolated unittest bootstrap with project imports enabled."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    if platform.system() != 'Darwin' or not Path(BACKEND_PATH).is_file():
        raise ToolError('sandbox_unavailable')
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ToolError('permission_denied')
    root = root.resolve()
    from agent_lab.commands import _check_workspace
    _check_workspace(root)
    backend=_selected_backend(root,command_backend,backend_policy)
    environment=_selected_environment(root,python_environment,python_environment_policy,
        base_executable=backend['entry']['path'] if backend else None)
    if tool_executables or allow_workspace_executables or tool_read_paths or tool_sysctl_read:
        _check_runtime_policy(root,node_executable,python_environment,command_backend,tool_executables,command_runtime_policy,allow_workspace_executables,tool_read_paths,tool_sysctl_read)
    selected = _test_scope(root, start_dir) if bootstrap == TEST_BOOTSTRAP else None
    if selected is not None:
        extra_args = (selected,)
    deadline = time.monotonic() + min(timeout, MAX_TEST_SECONDS)
    with tempfile.TemporaryDirectory(prefix='agent-lab-test-') as directory:
        scratch = Path(directory).resolve()
        profile = (_command_profile(root,scratch,read_only_paths=read_only_paths,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,node_executable=node_executable,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
                   if backend or tool_executables or allow_workspace_executables else '\n'.join([_profile(root,scratch,read_only_paths=read_only_paths,python_environment_policy=environment),*_tool_resource_rules(root,tool_read_paths,tool_sysctl_read)]))
        env = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'TMPDIR': str(scratch),
               'HOME': str(scratch), 'PYTHONDONTWRITEBYTECODE': '1'}
        if environment is not None:
            env.update({'VIRTUAL_ENV':environment['path'],'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'})
        # Probe policy application before permitting any project code to execute.
        probe = _collect([BACKEND_PATH, '-p', profile, '/usr/bin/true'], root, env,
                         min(1, deadline - time.monotonic()))
        if probe['exit_code'] != 0:
            raise ToolError('sandbox_unavailable')
        _validate_read_only(root, read_only_paths)
        _check_workspace(root)
        _selected_backend(root,command_backend,backend)
        if tool_executables or allow_workspace_executables or tool_read_paths or tool_sysctl_read:
            _check_runtime_policy(root,node_executable,python_environment,command_backend,tool_executables,command_runtime_policy,allow_workspace_executables,tool_read_paths,tool_sysctl_read)
        if environment is not None:_selected_environment(root,python_environment,environment,
            base_executable=backend['entry']['path'] if backend else None)
        # Explicit dependency environments retain their admitted launcher.
        # Otherwise use the canonical NoFork binary or the confined native app
        # entry; the framework wrapper itself requires denied spawn/SETEXEC.
        executable = environment['launcher'] if environment else (backend['entry']['path'] if backend else str(Path(sys.executable).resolve()))
        result = _inner_collect([BACKEND_PATH, '-p', profile, executable, '-I', '-c',
                           bootstrap, *extra_args], root, env,
                          deadline - time.monotonic(), backend)
        if selected is not None:
            result['test_start_dir'] = selected
        return result


OBSERVE_BOOTSTRAP = '''import importlib.util,json,os,sys
sys.path.insert(0,os.getcwd())
p=json.loads(sys.argv[1])
spec=importlib.util.spec_from_file_location("_candidate",p["module"]+".py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
function=getattr(module,p["function"])
values=[function(*args) for args in p["cases"]]
print(json.dumps({"observations":values},allow_nan=False,separators=(",",":")))
'''


def _json_observation(value, depth=0):
    if depth > 32:
        return False
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_json_observation(item, depth+1) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_observation(item, depth+1) for key,item in value.items())
    return False


def observe_function(root, module, function, cases, timeout, *, read_only_paths=()):
    """Private evaluator boundary: returns observations, never decides correctness.

    The trusted parent owns expected values. Early exit, missing observations,
    extra output and malformed output fail closed. This is a finite development
    benchmark, not a tamperproof judge for malicious Python introspection.
    """
    if not all(isinstance(n,str) and re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,79}',n) for n in (module,function)):
        raise ToolError('invalid_arguments')
    payload=json.dumps({'module':module,'function':function,'cases':cases},allow_nan=False)
    if len(payload)>65536:raise ToolError('input_too_large')
    options={'read_only_paths':read_only_paths} if read_only_paths else {}
    result=_run_tests(root,timeout,OBSERVE_BOOTSTRAP,(payload,),**options)
    observations=None
    try:
        body=json.loads(result['stdout'])
        observations=body['observations']
        complete=(result['exit_code']==0 and not result['stdout_truncated'] and isinstance(observations,list)
                  and len(observations)==len(cases) and all(_json_observation(v) for v in observations))
    except (ValueError,TypeError,KeyError,RecursionError):
        complete=False
    return {'complete':complete,'observations':observations if complete else None,'exit_code':result['exit_code']}


def _supervisor_owner(lease):
    environment, descriptors = {'PATH': os.defpath, 'LANG': 'C.UTF-8'}, [lease]
    owner = owner_read_fd()
    if owner is not None:
        descriptors.append(owner)
        environment[OWNER_ENV] = str(owner)
    output_env, output_fds = forwarded_output()
    environment.update(output_env)
    descriptors.extend(output_fds)
    job = supervisor_payload()
    if job is not None:
        environment[SUPERVISOR_ENV] = job
    return environment, tuple(descriptors)


def _supervisor_result(response):
    if response['exit_code'] or response['stdout_truncated']:
        raise ToolError('sandbox_unavailable')
    try:
        payload = json.loads(response['stdout'])
        if cancelled_receipt(payload):
            raise NativeCancelled('tool_cancelled')
        if isinstance(payload, dict) and ('cancelled' in payload or 'local_quiescent' in payload):
            raise ToolError('sandbox_unavailable')
        if 'error' in payload:
            raise ToolError(payload['error'])
        return payload['result']
    except (ValueError, KeyError, TypeError):
        raise ToolError('sandbox_unavailable') from None


def run_tests(root, timeout, *, read_only_paths=(), start_dir=None,python_environment=None,python_environment_policy=None,command_backend='nofork',backend_policy=None,
               tool_executables=None,node_executable=None,command_runtime_policy=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    """Keep a trusted execution-lease owner alive independently of the worker.

    The supervisor inherits only the lease; project processes inherit no lease
    descriptors. Killing the tool worker therefore does not release the fence
    while the bounded supervisor is still running project tests.
    """
    selected = _test_scope(root, start_dir)
    from agent_lab.confined_backend import backend_descriptor
    selected_backend=backend_descriptor(root,command_backend)
    backend=_selected_backend(root,command_backend,backend_policy if backend_policy is not None else selected_backend)
    environment=_selected_environment(root,python_environment,python_environment_policy,
        base_executable=backend['entry']['path'] if backend else None)
    options={'python_environment':environment['path'],'python_environment_policy':environment} if environment else {}
    if backend is not None:options.update(command_backend=command_backend,backend_policy=backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    if tool_executables or allow_workspace_executables or tool_read_paths or tool_sysctl_read:
        from agent_lab.command_runtime import runtime_policy
        full=runtime_policy(root,node_executable,python_environment,command_backend,tool_executables,allow_workspace_executables,tool_read_paths,tool_sysctl_read)
        if command_runtime_policy is not None:
            _check_runtime_policy(root,node_executable,python_environment,command_backend,tool_executables,command_runtime_policy,allow_workspace_executables,tool_read_paths,tool_sysctl_read)
        options.update(tool_executables=tool_executables,node_executable=node_executable,command_runtime_policy=full,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    try:
        from .process_guard import active_lease_fd
    except ImportError:
        return _run_tests(root, timeout, read_only_paths=read_only_paths, start_dir=selected,**options)
    lease = active_lease_fd()
    if lease is None:
        return _run_tests(root, timeout, read_only_paths=read_only_paths, start_dir=selected,**options)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    if tool_executables or allow_workspace_executables or tool_read_paths or tool_sysctl_read:
        return _extended_supervisor(lease,_runtime_request('run_tests',root,min(timeout,MAX_TEST_SECONDS),
            read_only_paths,node_executable,python_environment,command_backend,tool_executables,full,start_dir=selected,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read))
    supervisor_env, supervisor_fds = _supervisor_owner(lease)
    response = _collect([sys.executable, '-I', str(Path(__file__).resolve()),
                         '--supervisor', str(lease), str(root), str(min(timeout, MAX_TEST_SECONDS)),
                         json.dumps(list(read_only_paths), ensure_ascii=True), selected,
                         *([json.dumps(environment,ensure_ascii=True),json.dumps(backend,ensure_ascii=True)] if backend else
                           ([json.dumps(environment,ensure_ascii=True)] if environment else []))],
                        root, supervisor_env,
                        min(timeout, MAX_TEST_SECONDS) + 1 + (backend['cleanup_seconds'] if backend else 0), pass_fds=supervisor_fds,
                        output_limit=MAX_PROTOCOL_BYTES, monitor_owner=False)
    return _supervisor_result(response)


def _tool_resource_rules(root,tool_read_paths,tool_sysctl_read):
    rules=[]
    # Resolve scopes/selectors without rehashing large tools or SDK files here.
    # Full runtime descriptors are checked before and after the policy probe.
    from agent_lab.tool_resources import resource_paths,sysctl_reads,DENY_FCNTL
    resources=resource_paths(root,tool_read_paths)
    named_reads=sysctl_reads(tool_sysctl_read)
    if named_reads:
        rules.append('(allow sysctl-read %s)'%' '.join('(sysctl-name %s)'%_quote(name) for name in named_reads))
    for resource in resources:
        matcher='subpath' if resource.is_dir() else 'literal'
        rules += ['(allow file-read* (%s %s))'%(matcher,_quote(resource)),
                  '(deny file-write* (%s %s))'%(matcher,_quote(resource))]
    if resources:
        rules += ['(deny file-link)','(deny system-fcntl (fcntl-command %s))'%' '.join(str(n) for n in DENY_FCNTL)]
    metadata = {parent for resource in resources for parent in resource.parents}
    if metadata:rules.append('(allow file-read-metadata %s)' % ' '.join('(literal %s)' % _quote(p) for p in sorted(metadata)))
    return rules


def _command_profile(root, scratch, *, read_only_paths=(), node_executable=None,python_environment=None,command_backend='nofork',tool_executables=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    from agent_lab.commands import trusted_executables
    binaries = {Path(path).resolve() for path in trusted_executables(node_executable, command_backend=command_backend,tool_executables=tool_executables,workspace=root).values()}
    # Modern macOS /bin/sh dispatches to the system bash image. These literal
    # runtime resources do not expose a user-selected executable or config tree.
    binaries.add(Path('/bin/bash'))
    from agent_lab.confined_backend import backend_descriptor
    backend=backend_descriptor(root,command_backend)
    environment=_selected_environment(root,python_environment,
        base_executable=backend['entry']['path'] if backend else None)
    rules = [_profile(root, scratch, read_only_paths=read_only_paths,python_environment_policy=environment),
             '(allow process-exec %s)' % ' '.join('(literal %s)' % _quote(p) for p in sorted(binaries)),
             '(allow file-read* %s)' % ' '.join('(literal %s)' % _quote(p) for p in sorted(binaries))]
    if node_executable is not None:
        from agent_lab.command_runtime import NODE_SYSCTL_READ
        rules.append('(allow sysctl-read %s)' % ' '.join(
            '(sysctl-name %s)' % _quote(name) for name in NODE_SYSCTL_READ))
    rules.extend(_tool_resource_rules(root,tool_read_paths,tool_sysctl_read))
    metadata = {parent for binary in binaries for parent in binary.parents}
    rules.append('(allow file-read-metadata %s)' % ' '.join('(literal %s)' % _quote(p) for p in sorted(metadata)))
    rules.append('(allow file-read* (literal "/private/var/select/sh"))')
    hidden = '^' + re.escape(str(root)) + r'/(.*/)?\.[^/]+(/.*)?$'
    from agent_lab.workspace_exec import workspace_exec_policy
    scope=workspace_exec_policy(root,allow_workspace_executables)
    if scope is not None:
        visible='^'+re.escape(str(root))+r'/[^/.][^/]*(/[^/.][^/]*)*$'
        rules += ['(allow process-exec (regex %s))'%_quote(visible),
                  '(deny process-exec (regex %s))'%_quote(hidden)]
    if backend is not None:
        rules += ['(allow process-fork)', '(deny syscall-unix (syscall-number 66 82 147 244))']
    rules += ['(deny file-read* file-write* (regex %s))' % _quote(hidden),
              # Deny directory relocation rather than allow a protected child
              # to move past its pathname carveout. Scratch cleanup is separate.
              '(deny file-write-unlink (require-all (subpath %s) (vnode-type DIRECTORY)))' % _quote(root),
              '(deny file-write-unlink (require-all (literal %s) (vnode-type DIRECTORY)))' % _quote(root)]
    return '\n'.join(rules)


def _run_command(root, intent, timeout, *, read_only_paths=(), node_executable=None,python_environment=None,command_backend='nofork',backend_policy=None,tool_executables=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    from agent_lab.commands import validate_command
    intent = validate_command(root, intent, read_only_paths=read_only_paths, node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    backend=_selected_backend(root,command_backend,backend_policy)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    if platform.system() != 'Darwin' or not Path(BACKEND_PATH).is_file():
        raise ToolError('sandbox_unavailable')
    root = Path(intent['workspace'])
    deadline = time.monotonic() + min(timeout, MAX_TEST_SECONDS)
    with tempfile.TemporaryDirectory(prefix='agent-lab-command-') as directory:
        scratch = Path(directory).resolve()
        profile = _command_profile(root, scratch, read_only_paths=read_only_paths, node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
        env = dict(intent['environment_policy']['fixed'])
        env.update({'HOME': str(scratch), 'TMPDIR': str(scratch)})
        probe = _collect([BACKEND_PATH, '-p', profile, '/usr/bin/true'], root, env,
                         min(1, deadline - time.monotonic()))
        if probe['exit_code'] != 0:
            raise ToolError('sandbox_unavailable')
        # Revalidate after the policy probe and before executing project code.
        validate_command(root, intent, read_only_paths=read_only_paths, node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
        return _inner_collect([BACKEND_PATH, '-p', profile, *intent['argv']],
                        Path(intent['cwd']), env, deadline - time.monotonic(), backend)


def run_command(root, intent, timeout, *, read_only_paths=(), node_executable=None,python_environment=None,command_backend='nofork',backend_policy=None,tool_executables=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    """Execute validated authority; callers own approval and unknown recovery."""
    from .commands import validate_command
    from .process_guard import active_lease_fd
    intent = validate_command(root, intent, read_only_paths=read_only_paths, node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    backend=intent.get('command_runtime_policy',{}).get('backend')
    _selected_backend(root,command_backend,backend_policy if backend_policy is not None else backend)
    lease = active_lease_fd()
    if lease is None:
        return _run_command(root, intent, timeout, read_only_paths=read_only_paths, node_executable=node_executable,python_environment=python_environment,command_backend=command_backend,backend_policy=backend,tool_executables=tool_executables,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    timeout = min(timeout, MAX_TEST_SECONDS)
    if tool_executables or allow_workspace_executables or tool_read_paths or tool_sysctl_read:
        return _extended_supervisor(lease,_runtime_request('run_command',root,timeout,read_only_paths,
            node_executable,python_environment,command_backend,tool_executables,intent['command_runtime_policy'],intent=intent,allow_workspace_executables=allow_workspace_executables,tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read))
    supervisor_env, supervisor_fds = _supervisor_owner(lease)
    response = _collect([sys.executable, '-I', str(Path(__file__).resolve()),
                         '--command-supervisor', str(lease), str(root), str(timeout),
                         json.dumps(intent, ensure_ascii=True, allow_nan=False),
                         json.dumps(list(read_only_paths), ensure_ascii=True),
                         *([node_executable or '',str(python_environment) if python_environment else '',json.dumps(backend,ensure_ascii=True)] if backend else
                           ([node_executable or '',str(python_environment)] if python_environment is not None else
                            ([node_executable] if node_executable else [])))],
                        root, supervisor_env, timeout + 1 + (backend['cleanup_seconds'] if backend else 0),
                        pass_fds=supervisor_fds, output_limit=MAX_PROTOCOL_BYTES, monitor_owner=False)
    return _supervisor_result(response)


def _git_profile(root, scratch, executable):
    # Remove the broad workspace grants from the test profile. The replacement
    # permits visible paths plus Git metadata, never arbitrary hidden sources.
    profile = _profile(root, scratch).replace('(subpath %s)' % _quote(root), '')
    visible = '^' + re.escape(str(root)) + r'/[^./][^/]*(/[^./][^/]*)*$'
    # Explicit denies are required by Seatbelt in addition to positive path
    # filters. Spell out all top-level dot names except exactly .git using
    # ordinary regex alternatives (Seatbelt does not support lookarounds).
    top_hidden = '^' + re.escape(str(root)) + r'/(\.[^g/][^/]*|\.g([^i/][^/]*)?|\.gi([^t/][^/]*)?|\.git[^/]+)(/.*)?$'
    nested_hidden = '^' + re.escape(str(root)) + r'/[^./][^/]*(/[^./][^/]*)*/\.[^/]+(/.*)?$'
    rules = [profile,
             '(allow file-read* (literal %s) (regex %s) (subpath %s))' %
             (_quote(root), _quote(visible), _quote(root/'.git')),
             '(allow process-exec (literal %s))' % _quote(executable),
             '(allow file-read* (literal %s) (subpath "/Library/Developer/CommandLineTools/usr/lib"))' % _quote(executable),
             '(allow file-read-metadata %s)' % ' '.join('(literal %s)' % _quote(p) for p in Path(executable).parents),
             '(deny file-read* (regex %s) (regex %s))' % (_quote(top_hidden), _quote(nested_hidden)),
             '(deny file-write* (subpath %s) (subpath %s))' % (_quote(root), _quote(scratch))]
    return '\n'.join(rules)


def _run_git(root, intent, timeout):
    from agent_lab.git_tools import git_argv, git_environment, validate_git, trusted_git, MAX_GIT_OUTPUT
    validate_git(root, intent)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    if platform.system() != 'Darwin' or not Path(BACKEND_PATH).is_file():
        raise ToolError('sandbox_unavailable')
    root = Path(root).resolve()
    deadline = time.monotonic() + min(timeout, MAX_TEST_SECONDS)
    with tempfile.TemporaryDirectory(prefix='agent-lab-git-') as directory:
        scratch = Path(directory).resolve()
        profile = _git_profile(root, scratch, trusted_git())
        env = git_environment(scratch)
        probe = _collect([BACKEND_PATH, '-p', profile, '/usr/bin/true'], root, env,
                         min(1, deadline-time.monotonic()))
        if probe['exit_code'] != 0:
            raise ToolError('sandbox_unavailable')
        # Construct fixed argv only after a second repository/path validation.
        return _collect([BACKEND_PATH, '-p', profile, *git_argv(root, intent)], root, env,
                        deadline-time.monotonic(), output_limit=MAX_GIT_OUTPUT, stream_output=True)


def run_git(root, intent, timeout):
    from .git_tools import validate_git
    from .process_guard import active_lease_fd
    validate_git(root, intent)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolError('tool_timeout')
    lease = active_lease_fd()
    if lease is None:
        return _run_git(root, intent, timeout)
    timeout = min(timeout, MAX_TEST_SECONDS)
    supervisor_env, supervisor_fds = _supervisor_owner(lease)
    response = _collect([sys.executable, '-I', str(Path(__file__).resolve()),
                         '--git-supervisor', str(lease), str(root), str(timeout),
                         json.dumps(intent, ensure_ascii=True, allow_nan=False)],
                        root, supervisor_env, timeout+1,
                        pass_fds=supervisor_fds, output_limit=MAX_PROTOCOL_BYTES, monitor_owner=False)
    return _supervisor_result(response)


def _runtime_request(kind,root,timeout,read_only_paths,node_executable,python_environment,
                     command_backend,tool_executables,policy,*,intent=None,start_dir=None,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    request={'version':3 if tool_read_paths or tool_sysctl_read else (2 if allow_workspace_executables else 1),'kind':kind,'root':str(Path(root).resolve()),'timeout':timeout,
            'read_only_paths':list(read_only_paths),'node_executable':node_executable,
            'python_environment':str(python_environment) if python_environment is not None else None,
            'command_backend':command_backend,'tool_executables':tool_executables,
            'command_runtime_policy':policy,'intent':intent,'start_dir':start_dir}
    if request['version']>=2:request['allow_workspace_executables']=allow_workspace_executables
    if request['version']==3:request.update(tool_read_paths=tool_read_paths,tool_sysctl_read=tool_sysctl_read)
    return request


def _check_runtime_policy(root,node,environment,backend,executables,expected,allow_workspace_executables=False,tool_read_paths=None,tool_sysctl_read=None):
    from agent_lab.command_runtime import runtime_policy
    current=runtime_policy(root,node,environment,backend,executables,allow_workspace_executables,tool_read_paths,tool_sysctl_read)
    if json.dumps(current,sort_keys=True,allow_nan=False)!=json.dumps(expected,sort_keys=True,allow_nan=False):
        raise ToolError('command_runtime_changed')
    return current


def _runtime_supervisor(request):
    """Versioned Host envelope; never a model-selected tool or raw sandbox text."""
    keys={'version','kind','root','timeout','read_only_paths','node_executable','python_environment',
          'command_backend','tool_executables','command_runtime_policy','intent','start_dir'}
    if type(request) is not dict or type(request.get('version')) is not int or request['version'] not in (1,2,3):
        raise ToolError('invalid_arguments')
    if request['version']>=2:keys.add('allow_workspace_executables')
    if request['version']==3:keys.update(('tool_read_paths','tool_sysctl_read'))
    resources=request.get('tool_read_paths');named_reads=request.get('tool_sysctl_read')
    scope=request.get('allow_workspace_executables',False)
    if (set(request)!=keys or type(scope) is not bool or request['kind'] not in ('run_command','run_tests')
            or type(request['root']) is not str or len(request['root'])>4096
            or not Path(request['root']).is_absolute() or '\x00' in request['root']
            or type(request['timeout']) not in (int,float) or not 0<request['timeout']<=MAX_TEST_SECONDS
            or not math.isfinite(request['timeout']) or type(request['read_only_paths']) is not list
            or (request['tool_executables'] is not None and type(request['tool_executables']) is not dict)
            or not (request['tool_executables'] or scope or resources or named_reads)):
        raise ToolError('invalid_arguments')
    root=Path(request['root'])
    current=_check_runtime_policy(root,request['node_executable'],request['python_environment'],
                                  request['command_backend'],request['tool_executables'],request['command_runtime_policy'],scope,resources,named_reads)
    options={'read_only_paths':request['read_only_paths'],'node_executable':request['node_executable'],
             'python_environment':request['python_environment'],'command_backend':request['command_backend'],
             'tool_executables':request['tool_executables'],'backend_policy':current.get('backend'),
             'allow_workspace_executables':scope,'tool_read_paths':resources,'tool_sysctl_read':named_reads}
    if request['kind']=='run_command':
        if request['start_dir'] is not None or type(request['intent']) is not dict:
            raise ToolError('invalid_arguments')
        return _run_command(root,request['intent'],request['timeout'],**options)
    if request['intent'] is not None or request['start_dir'] not in ('.','tests'):
        raise ToolError('invalid_arguments')
    environment=current.get('python_environment')
    return _run_tests(root,request['timeout'],start_dir=request['start_dir'],command_runtime_policy=current,
                      python_environment_policy=environment,**options)


def _extended_supervisor(lease,request):
    encoded=json.dumps(request,ensure_ascii=True,allow_nan=False,separators=(',',':'))
    if len(encoded.encode('ascii'))>131072:raise ToolError('invalid_arguments')
    supervisor_env,supervisor_fds=_supervisor_owner(lease)
    backend=request['command_runtime_policy'].get('backend')
    grace=backend['cleanup_seconds'] if backend else 0
    response=_collect([sys.executable,'-I',str(Path(__file__).resolve()),'--runtime-supervisor',str(lease),encoded],
                      request['root'],supervisor_env,request['timeout']+grace+1,
                      pass_fds=supervisor_fds,output_limit=MAX_PROTOCOL_BYTES,monitor_owner=False)
    return _supervisor_result(response)


if __name__ == '__main__':
    # No model-controlled bootstrap, environment, sandbox text or descriptors.
    try:
        with inherited_owner(), inherited_output(producer=True):
            if not ((len(sys.argv) in (6, 7, 8, 9) and sys.argv[1] == '--supervisor') or
                    (len(sys.argv) in (7, 8, 9, 10) and sys.argv[1] == '--command-supervisor') or
                    (len(sys.argv)==4 and sys.argv[1]=='--runtime-supervisor') or
                    (len(sys.argv) == 6 and sys.argv[1] == '--git-supervisor')):
                raise ToolError('invalid_arguments')
            lease = int(sys.argv[2])
            os.fstat(lease)
            # close_fds in _collect prevents the project from inheriting this FD.
            extended=None
            if sys.argv[1]=='--runtime-supervisor':
                if len(sys.argv[3].encode('utf-8'))>131072:raise ToolError('invalid_arguments')
                extended=json.loads(sys.argv[3]);root=Path(extended['root'])
            else:root=Path(sys.argv[3])
            with supervisor_job(root):
                if extended is not None:
                    result=_runtime_supervisor(extended)
                elif sys.argv[1] == '--supervisor':
                    environment=json.loads(sys.argv[7]) if len(sys.argv)>=8 else None
                    backend_options=({'command_backend':'confined-fork','backend_policy':json.loads(sys.argv[8])} if len(sys.argv)==9 else {})
                    result = _run_tests(Path(sys.argv[3]), float(sys.argv[4]), read_only_paths=json.loads(sys.argv[5]),
                                        start_dir=sys.argv[6] if len(sys.argv)>=7 else None,
                                        **({'python_environment':environment['path'],'python_environment_policy':environment} if environment else {}),**backend_options)
                else:
                    operation = _run_git if sys.argv[1] == '--git-supervisor' else _run_command
                    options = {'read_only_paths': json.loads(sys.argv[6])} if sys.argv[1] == '--command-supervisor' else {}
                    if sys.argv[1] == '--command-supervisor' and len(sys.argv)>=8 and sys.argv[7]:
                        options['node_executable'] = sys.argv[7]
                    if sys.argv[1] == '--command-supervisor' and len(sys.argv)>=9 and sys.argv[8]:
                        options['python_environment']=sys.argv[8]
                    if sys.argv[1] == '--command-supervisor' and len(sys.argv)==10:
                        options.update(command_backend='confined-fork',backend_policy=json.loads(sys.argv[9]))
                    result = operation(Path(sys.argv[3]), json.loads(sys.argv[5]), float(sys.argv[4]), **options)
        payload = {'result': result}
    except NativeCancelled:
        payload = {'cancelled': True, 'local_quiescent': True}
    except ToolError as error:
        payload = {'error': error.code}
    except Exception:
        payload = {'error': 'sandbox_unavailable'}
    try:
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except BrokenPipeError:
        # The worker may have died; tests are already quiescent before release.
        pass
