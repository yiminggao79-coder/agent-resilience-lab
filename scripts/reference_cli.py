"""Isolation helpers for an installed Codex reference experiment, not inference."""
import json
import os
from pathlib import Path
import stat

FIXTURE_TOKEN='owned-synthetic-reference-token'


def _owned_base(base):
    base=Path(base)
    if (not base.is_absolute() or base!=base.resolve(strict=True) or base.parent!=Path('/private/tmp')
            or not base.name.startswith('agentlab-reference-') or not base.is_dir()):
        raise ValueError('owned_reference_directory_required')
    return base


def reference_environment(base):
    base=_owned_base(base)
    for name in ('home','codex-home','scratch'):
        path=base/name
        if path.is_symlink():raise ValueError('owned_reference_directory_required')
        path.mkdir(exist_ok=True,mode=0o700)
    # Child-only documented settings; the controller's environment is untouched.
    return {'PATH':os.defpath,'LANG':'C.UTF-8','HOME':str(base/'home'),'TMPDIR':str(base/'scratch'),
            'CODEX_HOME':str(base/'codex-home'),'AGENTLAB_REFERENCE_FIXTURE_KEY':FIXTURE_TOKEN}


def reference_profile(base,port,private):
    base=_owned_base(base);private=Path(private)
    if type(port) is not int or not 1<=port<=65535 or private!=base/'private':
        raise ValueError('invalid_reference_boundary')
    info=private.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('invalid_reference_boundary')
    quote=lambda value:json.dumps(str(value),ensure_ascii=True)
    return '\n'.join(['(version 1)','(allow default)',
        '(deny file-read* file-write* (subpath "/Users"))',
        '(deny file-read* file-write* (literal '+quote(private)+'))',
        '(deny mach-lookup)',
        '(allow mach-lookup (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon"))',
        '(deny network*)',
        '(allow network-outbound (remote ip '+quote('localhost:'+str(port))+'))'])


REFERENCE_BINARY=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')


def reference_command(base,port,prompt,*,mcp=False):
    base=_owned_base(base)
    if type(prompt) is not str or not prompt or len(prompt.encode())>65536:raise ValueError('invalid_reference_prompt')
    settings={
        'model_provider':'"reference_fixture"','model':'"reference-protocol-fixture"',
        'model_providers.reference_fixture.name':'"Owned local protocol fixture"',
        'model_providers.reference_fixture.base_url':json.dumps('http://127.0.0.1:%d/v1'%port),
        'model_providers.reference_fixture.env_key':'"AGENTLAB_REFERENCE_FIXTURE_KEY"',
        'model_providers.reference_fixture.requires_openai_auth':'false',
        'model_providers.reference_fixture.wire_api':'"responses"',
        'model_providers.reference_fixture.supports_websockets':'false',
        'model_providers.reference_fixture.request_max_retries':'0',
        'model_providers.reference_fixture.stream_max_retries':'0',
        'model_providers.reference_fixture.stream_idle_timeout_ms':'3000',
        'cli_auth_credentials_store':'"ephemeral"','analytics.enabled':'false',
        'otel.metrics_exporter':'"none"','otel.trace_exporter':'"none"',
        'feedback.enabled':'false','approval_policy':'"never"',
        'features.shell_snapshot':'false','shell_environment_policy.inherit':'"none"'}
    if mcp:
        from agent_lab.confined_backend import native_python_entry
        settings.update({
            'mcp_servers.owned_fixture.command':json.dumps(str(native_python_entry())),
            'mcp_servers.owned_fixture.args':json.dumps(['-I','-S',str(base/'server.py'),str(base/'work/receipt.txt'),str(base/'mcp-methods.txt')]),
            'mcp_servers.owned_fixture.cwd':json.dumps(str(base)),
            'mcp_servers.owned_fixture.startup_timeout_sec':'5',
            'mcp_servers.owned_fixture.tool_timeout_sec':'5',
            'mcp_servers.owned_fixture.required':'true'})
    command=['/usr/bin/sandbox-exec','-p',reference_profile(base,port,base/'private'),str(REFERENCE_BINARY),
        'exec','--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
        '--strict-config','--json','--color','never','--sandbox','workspace-write','--cd',str(base/'work')]
    for key,value in settings.items():command.extend(['-c',key+'='+value])
    return command+[prompt]


def managed_preferences_status(base,port):
    """Query only forced-key presence; never call CopyAppValue or auth APIs."""
    import subprocess
    import sys
    body='''import ctypes,json
cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
create=cf.CFStringCreateWithCString;create.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint32];create.restype=ctypes.c_void_p
sync=cf.CFPreferencesAppSynchronize;sync.argtypes=[ctypes.c_void_p];sync.restype=ctypes.c_ubyte
forced=cf.CFPreferencesAppValueIsForced;forced.argtypes=[ctypes.c_void_p,ctypes.c_void_p];forced.restype=ctypes.c_ubyte
release=cf.CFRelease;release.argtypes=[ctypes.c_void_p]
application=create(None,b'com.openai.codex',0x08000100)
result={'synchronized':bool(sync(application))}
for label,name in [('forced_config',b'config_toml_base64'),('forced_requirements',b'requirements_toml_base64')]:
 key=create(None,name,0x08000100);result[label]=bool(forced(key,application));release(key)
release(application);print(json.dumps(result))
'''
    result=subprocess.run(['/usr/bin/sandbox-exec','-p',reference_profile(base,port,Path(base)/'private'),
        sys.executable,'-I','-S','-c',body],env=reference_environment(base),capture_output=True,text=True,timeout=5)
    if result.returncode!=0:raise ValueError('reference_managed_preferences_unavailable')
    value=json.loads(result.stdout)
    if (type(value) is not dict or set(value)!={'synchronized','forced_config','forced_requirements'}
            or any(type(item) is not bool for item in value.values()) or not value['synchronized']):
        raise ValueError('reference_managed_preferences_unavailable')
    return value
