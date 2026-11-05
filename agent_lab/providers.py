"""Explicit vendor routes and secret-free trusted JSON configuration.

Documentation presets are not provider certifications. Only DeepSeek Chat has
real task evidence in this project; all other routes require separate live tests.
"""
import copy
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .model import OpenAIModel

PROVIDERS={
    'openai_chat':{'base_url':'https://api.openai.com/v1','protocol':'chat','api_key_env':'OPENAI_API_KEY',
                  'token_parameter':'max_completion_tokens','parallel_parameter':True,
                  'documentation':'https://developers.openai.com/api/docs/quickstart'},
    'openai_responses':{'base_url':'https://api.openai.com/v1','protocol':'responses','api_key_env':'OPENAI_API_KEY',
                  'token_parameter':'max_completion_tokens','parallel_parameter':True,
                  'documentation':'https://developers.openai.com/api/reference/resources/responses/methods/create'},
    'anthropic':{'base_url':'https://api.anthropic.com/v1','protocol':'anthropic','api_key_env':'ANTHROPIC_API_KEY',
                  'token_parameter':'max_tokens','parallel_parameter':True,
                  'documentation':'https://platform.claude.com/docs/en/api/messages'},
    'deepseek_chat':{'base_url':'https://api.deepseek.com','protocol':'chat','api_key_env':'DEEPSEEK_API_KEY',
                  'token_parameter':'max_tokens','parallel_parameter':False,
                  'documentation':'https://api-docs.deepseek.com/api/create-chat-completion/'},
    'deepseek_anthropic':{'base_url':'https://api.deepseek.com/anthropic','protocol':'anthropic','api_key_env':'DEEPSEEK_API_KEY',
                  'token_parameter':'max_tokens','parallel_parameter':False,
                  'documentation':'https://api-docs.deepseek.com/guides/anthropic_api/'},
    'siliconflow_chat':{'base_url':'https://api.siliconflow.cn/v1','protocol':'chat','api_key_env':'SILICONFLOW_API_KEY',
                  'token_parameter':'max_tokens','parallel_parameter':False,
                  'documentation':'https://docs.siliconflow.cn/docs/api/chat-completions-post'},
    'dashscope_chat':{'base_url':None,'protocol':'chat','api_key_env':'DASHSCOPE_API_KEY',
                  'token_parameter':'max_tokens','parallel_parameter':False,
                  'documentation':'https://help.aliyun.com/zh/model-studio/base-url'},
}
ALLOWED={'provider','model','base_url','api_key_env','protocol','stream','thinking','reasoning_effort',
         'token_parameter','parallel_parameter','ca_bundle'}
ROUTING={'base_url','api_key_env','protocol','token_parameter','parallel_parameter'}


def provider_catalog():
    return [{'provider':name,**copy.deepcopy(values),'documentation_checked':'2026-11-07',
             'real_provider_tested':name=='deepseek_chat',
             'status':'real_chat_task_and_sse_tested' if name=='deepseek_chat' else 'documentation_preset_live_unverified'}
            for name,values in PROVIDERS.items()]


def resolve_model_config(config):
    if not isinstance(config,dict) or set(config)-ALLOWED:
        raise ValueError('invalid_model_config_fields')
    name=config.get('provider')
    if not isinstance(name,str):raise ValueError('unknown_provider')
    if name!='custom' and name not in PROVIDERS:raise ValueError('unknown_provider')
    if not isinstance(config.get('model'),str) or not config['model'].strip():raise ValueError('model_required')
    if name=='custom':
        if not {'base_url','api_key_env','protocol'}<=set(config):raise ValueError('custom_routing_required')
        options={'token_parameter':'max_tokens','parallel_parameter':False}
    else:
        options={key:value for key,value in PROVIDERS[name].items() if key!='documentation'}
        for key in ROUTING & set(config):
            if name=='dashscope_chat' and key=='base_url':continue
            want=options[key]
            got=config[key].rstrip('/') if key=='base_url' and isinstance(config[key],str) else config[key]
            if type(got)!=type(want) or got!=want:raise ValueError('named_provider_route_override')
    options.update({key:copy.deepcopy(value) for key,value in config.items() if key!='provider'})
    options.setdefault('stream',False)
    if name=='dashscope_chat':
        value=options.get('base_url')
        if not isinstance(value,str):raise ValueError('workspace_endpoint_required')
        parsed=urlsplit(value.rstrip('/'))
        if (not re.fullmatch(r'[a-z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|us-east-1|cn-hongkong)\.maas\.aliyuncs\.com',parsed.hostname or '')
                or parsed.path!='/compatible-mode/v1' or parsed.port not in (None,443)):
            raise ValueError('invalid_workspace_endpoint')
    if name in ('openai_chat','openai_responses') and config.get('thinking') is not None:
        raise ValueError('provider_thinking_dialect_not_implemented')
    if name=='siliconflow_chat' and (config.get('thinking') is not None or config.get('reasoning_effort') is not None):
        raise ValueError('provider_thinking_dialect_not_implemented')
    if name=='anthropic' and config.get('thinking')=='enabled':
        raise ValueError('native_thinking_budget_not_implemented')
    if not isinstance(options.get('api_key_env'),str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,127}',options['api_key_env']):
        raise ValueError('invalid_api_key_environment_name')
    if not isinstance(options.get('base_url'),str) or not options['base_url']:
        raise ValueError('base_url_required')
    if options['api_key_env']=='DEEPSEEK_API_KEY' and options.get('base_url','').rstrip('/') not in ('https://api.deepseek.com','https://api.deepseek.com/anthropic'):
        raise ValueError('credential_scope_mismatch')
    # Validate without enabling API access or reading credentials. The adapter
    # owns exact capability validation; this layer owns selection and routing.
    try:OpenAIModel(allow_api=False,**options)
    except (TypeError,AttributeError):raise ValueError('invalid_model_config_option_types') from None
    return options


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_model_config_key')
        result[key]=value
    return result


def _outside_workspace(path,root):
    path=Path(path).absolute()
    try:
        resolved=path.resolve()
        if (resolved==root or root in resolved.parents or any(
                parent.resolve()==root or root in parent.resolve().parents for parent in path.parents)):
            raise ValueError('model_configuration_inside_workspace')
    except (OSError,RuntimeError):
        raise ValueError('invalid_model_configuration_path') from None
    return path


def load_model_config(path,workspace):
    path=Path(path)
    if path.is_symlink():raise ValueError('model_config_symlink')
    root=Path(workspace).resolve()
    path=_outside_workspace(path,root)
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>16384:
                raise ValueError('invalid_model_config_file')
            raw=stream.read(16385);after=os.fstat(stream.fileno())
            if (len(raw)>16384 or (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns,before.st_size)
                    !=(after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns,after.st_size)):
                raise ValueError('model_config_changed')
        config=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
                          parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (OSError,UnicodeError,RecursionError):
        raise ValueError('invalid_model_config_file') from None
    options=resolve_model_config(config)
    ca=options.get('ca_bundle')
    if ca is not None:_outside_workspace(ca,root)
    return options
