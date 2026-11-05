import argparse
import json
import os
import sys
import sqlite3
import uuid
from pathlib import Path
from contextlib import ExitStack
from .engine import Engine, Limits, public_status
from .model import OpenAIModel, ModelError
from .store import Busy, Store, StorageUnavailable
from .tools import Tools, ToolError
from .native_jobs import JobBlocked, JobStateError
from .runtime_config import RUNTIME_COMMANDS,add_runtime_arguments,resolve_cli_runtime,RuntimeConfigurationError


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description='Agent Resilience Lab: private checkpoints, metadata-only trace')
    parser.add_argument('--db', default='.lab/tasks.sqlite')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('run', 'resume', 'continue', 'compact', 'turns', 'status', 'trace', 'answer', 'plan', 'policy', 'abort', 'approval', 'approve', 'deny', 'chat', 'steer', 'inputs', 'input-withdraw', 'pending', 'stop', 'stop-status', 'fork', 'events', 'runtime-check'):
        item = sub.add_parser(command)
        if command not in ('run', 'chat', 'runtime-check'):
            item.add_argument('task_id')
        if command == 'chat':
            item.add_argument('--task-id', default=None)
        if command == 'fork':
            item.add_argument('task', help='new user task for a separate history branch; files remain shared; no inference runs')
        if command == 'steer':
            item.add_argument('--turn-id', required=True, help='expected active Turn; stale input is rejected')
            item.add_argument('--request-id', default=None, help='reuse a 32-hex ID for idempotent retries; feedback text is read from stdin')
        if command == 'inputs':
            item.add_argument('--show-text', action='store_true', help='print private user input text explicitly')
        if command == 'pending':
            item.add_argument('--show-intent', action='store_true', help='explicitly print the private prepared operation, including arguments; does not execute or confirm it')
        if command == 'events':
            item.add_argument('--after', type=int, default=0, help='read committed global event sequence greater than this value')
            item.add_argument('--limit', type=int, default=100, help='page size 1..100')
            item.add_argument('--expected-database', help='optional database_identity from the previous page; rejects a changed source')
            item.add_argument('--follow-seconds', type=float, default=None,
                              help='explicitly emit flushed JSONL observations for up to 30 seconds; empty pages do not mean task completion')
            item.add_argument('--follow-max-pages', type=int, default=None,
                              help='maximum emitted pages 1..100 (default 20); requires --follow-seconds')
        if command in ('stop', 'stop-status'):
            item.add_argument('--turn-id', required=True, help='exact Turn targeted by the durable cooperative stop')
        if command == 'stop':
            item.add_argument('--request-id', default=None, help='canonical ID for retries; a coalesced request returns the existing ID')
        if command == 'input-withdraw':
            item.add_argument('request_id')
        if command in ('run', 'resume', 'continue', 'compact', 'abort', 'approval', 'approve', 'deny', 'chat', 'stop', 'fork', 'runtime-check'):
            item.add_argument('--workspace', required=True)
            item.add_argument('--read-only-path', action='append', default=argparse.SUPPRESS if command in RUNTIME_COMMANDS else [],
                              help="host read-only file/subtree scope; exact '.' protects whole workspace; repeat saved scopes on resume")
        if command in RUNTIME_COMMANDS:
            add_runtime_arguments(item)
        if command in ('approve', 'deny'):
            item.add_argument('request_id')
            item.add_argument('--digest', required=True)
        if command in ('run', 'resume', 'continue', 'compact', 'chat'):
            item.add_argument('--mcp-config', default=None,
                              help='explicit trusted stdio/Streamable HTTP MCP configuration outside workspace; external processes/services are not sandboxed')
            item.add_argument('--model-config', default=None, help='secret-free trusted model JSON outside workspace; routing flags must not conflict')
            item.add_argument('--model', default=os.environ.get('AGENT_LAB_MODEL'))
            item.add_argument('--base-url', default=None)
            item.add_argument('--api-key-env', default=None, help='defaults to protocol-specific key environment variable')
            item.add_argument('--allow-api', action='store_true', help='explicitly enable requests which may incur costs')
            item.add_argument('--load-deepseek-key', action='store_true',
                              help='load only the authorized ~/Downloads/API.txt first line for this process; requires --allow-api and official DeepSeek routing')
            item.add_argument('--prepaid-account',action='store_true',
                              help='explicit Host authorization to use DeepSeek prepaid balance without a project total cap; keep usage records and stop on402')
            item.add_argument('--budget-ledger', default=None,
                              help='existing original shared DeepSeek budget SQLite outside workspace; never initializes a missing ledger; repeat this path for relocated runtimes')
            item.add_argument('--profile', choices=('local','coding'), default='coding')
            item.add_argument('--protocol', choices=('chat','responses','anthropic'), default='chat')
            item.add_argument('--stream', action='store_true')
            item.add_argument('--stream-text', action='store_true',
                              help='stream private assistant text to stderr; implies --stream; metadata events stay on stdout')
            item.add_argument('--stream-tools', action='store_true',
                              help='show bounded private native stdout/stderr on stderr; display is not tool completion')
            item.add_argument('--thinking', choices=('enabled','disabled','adaptive'))
            item.add_argument('--reasoning-effort', default=None)
            item.add_argument('--ca-bundle', default=None, help='explicit trusted CA PEM; TLS verification remains enabled')
            item.add_argument('--token-parameter', choices=('max_completion_tokens','max_tokens'), default='max_completion_tokens')
            item.add_argument('--no-parallel-parameter', action='store_true')
            item.add_argument('--skill', action='append', default=[])
            item.add_argument('--events', action='store_true', help='emit committed metadata events as JSON lines')
        if command in ('run', 'continue', 'chat'):
            if command in ('run', 'chat'):
                item.add_argument('--track-file-context', action='store_true',
                                  help='opt in for this new Coding task: bounded known-read file-version hints before sampling; saved for resume/fork, not a whole-project watcher')
            if command != 'chat':
                item.add_argument('task')
            item.add_argument('--no-auto-compact', action='store_true', help='disable saved automatic summaries for this new turn; original archive is retained')
            for name, default, datatype in [('max-steps', 12, int), ('max-seconds', 120, float), ('model-timeout', 30, float),
                                            ('tool-timeout', 3, float), ('max-errors', 3, int), ('max-model-retries', 2, int),
                                            ('max-output-tokens', 1024, int), ('max-context-bytes', 262144, int),
                                            ('max-summary-input-bytes', None, int),
                                            ('auto-compact-bytes', None, int), ('max-auto-compactions', 2, int), ('max-cost-usd', None, float),
                                            ('input-price', None, float), ('output-price', None, float)]:
                item.add_argument('--' + name, default=default if command == 'run' else None, type=datatype)
    sub.add_parser('providers', help='list documentation presets and live verification status; no API request')
    job_status = sub.add_parser('job-status', help='inspect fixed native-job metadata without creating or modifying state')
    job_status.add_argument('--workspace', required=True)
    sessions = sub.add_parser('sessions', help='list saved session IDs as readonly metadata; never executes or resumes')
    sessions.add_argument('--workspace', help='exact canonical workspace filter; does not check files or grant access')
    sessions.add_argument('--phase', help='exact saved phase filter')
    sessions.add_argument('--limit', type=int, default=20, help='page size 1..100 (default 20)')
    sessions.add_argument('--cursor', help='opaque next_cursor from the same database and filters')
    args = parser.parse_args(raw_argv)
    key_loaded = False
    previous_key = None
    try:
        extra_executables=resolve_cli_runtime(args)
        if args.command=='runtime-check':
            tools=Tools(args.workspace,read_only_paths=args.read_only_path,node_executable=args.node_executable,
                python_environment=args.python_environment,command_backend=args.command_backend,tool_executables=extra_executables,
                allow_workspace_executables=args.allow_workspace_executables,tool_read_paths=args.tool_read_path,tool_sysctl_read=args.tool_sysctl_read)
            print(json.dumps({'command_runtime_policy':tools.command_runtime_policy,
                'read_only_scopes':list(tools.read_only_policy.paths),'tool_execution_performed':False,'model_called':False},ensure_ascii=True,indent=2))
            return 0
        if args.command == 'job-status':
            from .native_jobs import inspect_workspace
            print(json.dumps(inspect_workspace(args.workspace, args.db), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'providers':
            from .providers import provider_catalog
            print(json.dumps(provider_catalog(),ensure_ascii=False,indent=2))
            return 0
        store_factory = Store.open_readonly if args.command in ('pending', 'stop-status', 'sessions', 'events') else Store
        with ExitStack() as resources, store_factory(args.db) as store:
            if args.command == 'events':
                if args.follow_seconds is None and args.follow_max_pages is not None:
                    raise ValueError('invalid_event_follow')
                if args.follow_seconds is not None:
                    from .event_reader import follow_events
                    try:
                        for frame in follow_events(store, args.task_id, after=args.after, limit=args.limit,
                                expected_database=args.expected_database, seconds=args.follow_seconds,
                                max_pages=args.follow_max_pages if args.follow_max_pages is not None else 20):
                            print(json.dumps(frame, ensure_ascii=False, allow_nan=False), flush=True)
                    except KeyboardInterrupt:
                        return 130
                    return 0
                from .event_reader import read_events
                result = read_events(store, args.task_id, after=args.after, limit=args.limit,
                                     expected_database=args.expected_database)
            elif args.command == 'sessions':
                from .session_listing import list_sessions
                result = list_sessions(store, limit=args.limit, cursor=args.cursor,
                                       workspace=args.workspace, phase=args.phase)
            elif args.command == 'fork':
                source = store.load(args.task_id)
                if source.get('profile', 'local') == 'coding':
                    from .coding_tools import CodingTools
                    factory = CodingTools
                elif source.get('profile', 'local') == 'local':
                    factory = Tools
                else:
                    raise ValueError('fork_profile_unsupported')
                tools = factory(args.workspace, read_only_paths=args.read_only_path,
                                node_executable=args.node_executable,python_environment=args.python_environment,command_backend=args.command_backend,tool_executables=extra_executables,allow_workspace_executables=args.allow_workspace_executables,tool_read_paths=args.tool_read_path,tool_sysctl_read=args.tool_sysctl_read)
                child_id = Engine(store, tools, None).fork_session(args.task_id, args.task)
                child = store.load(child_id)
                result = {'id': child_id, 'session_id': child['session_id'], 'turn_id': child['turn_id'],
                          'phase': child['phase'], 'source_task_id': args.task_id,
                          'source_revision': child['fork_origin']['source_revision'],
                          'copied_messages': child['fork_origin']['message_count'],
                          'workspace_shared': True, 'model_called': False}
            elif args.command == 'stop-status':
                result = store.stop_request(args.task_id, args.turn_id)
            elif args.command == 'stop':
                tools = Tools(args.workspace, **({'read_only_paths':args.read_only_path} if args.read_only_path else {}))
                receipt = store.request_stop(args.task_id, args.turn_id, args.request_id or uuid.uuid4().hex, str(tools.root))
                observation = 'already_observed'
                if receipt['status'] == 'requested':
                    try:
                        Engine(store, tools, None).observe_stop(args.task_id, args.turn_id)
                    except Busy:
                        observation = 'deferred_until_checkpoint'
                    else:
                        receipt = store.stop_request(args.task_id, args.turn_id)
                        observation = 'observed_checkpoint' if receipt['status'] == 'observed' else 'turn_already_ended'
                result = {'request': receipt, 'observation': observation, 'current_call_preempted': False,
                          'process_liveness': 'not_checked'}
            elif args.command == 'pending':
                from .diagnostics import describe_pending
                result = describe_pending(*store.diagnostic_view(args.task_id), show_intent=args.show_intent)
            elif args.command == 'steer':
                result = store.queue_input(args.task_id, args.turn_id, sys.stdin.read(65537),
                                           args.request_id or uuid.uuid4().hex)
                result = {**result, 'applies_at': 'next_sampling_boundary', 'interrupts_running_work': False}
            elif args.command == 'inputs':
                result = store.inputs(args.task_id, include_text=args.show_text)
            elif args.command == 'input-withdraw':
                result = store.withdraw_input(args.task_id, args.request_id)
            elif args.command in ('status', 'trace', 'answer', 'turns', 'plan', 'policy'):
                state = store.load(args.task_id)
                if args.command == 'turns':
                    result = store.turns(args.task_id)
                elif args.command == 'plan':
                    result = {'plan': state.get('current_plan')}
                elif args.command == 'policy':
                    result = {'read_only_policy': state.get('read_only_policy', {'version':1,'read_only_paths':[]}),
                              'identity': state.get('read_only_policy_identity')}
                elif args.command == 'answer':
                    if state['phase'] != 'completed':
                        raise ValueError('answer_not_available')
                    result = {'answer': state['messages'][-1]['content']}
                else:
                    result = store.events(args.task_id) if args.command == 'trace' else public_status(state)
                    if args.command == 'status':
                        from .diagnostics import describe_fork_origin
                        origin = describe_fork_origin(state)
                        if origin is not None:
                            result['fork_origin'] = origin
            elif args.command in ('approval', 'approve', 'deny'):
                engine = Engine(store, Tools(args.workspace, node_executable=args.node_executable,
                                            python_environment=args.python_environment,command_backend=args.command_backend,tool_executables=extra_executables,allow_workspace_executables=args.allow_workspace_executables,tool_read_paths=args.tool_read_path,tool_sysctl_read=args.tool_sysctl_read,
                                            **({'read_only_paths':args.read_only_path} if args.read_only_path else {})), None)
                result = engine.approval_request(args.task_id) if args.command == 'approval' else public_status(
                    engine.decide_approval(args.task_id, args.request_id, args.digest, args.command == 'approve'))
            else:
                if getattr(args, 'profile', 'local') == 'coding':
                    from .coding_tools import CodingTools
                    tools = CodingTools(args.workspace, **({'node_executable':args.node_executable} if getattr(args, 'node_executable', None) else {}),
                                        **({'python_environment':args.python_environment} if getattr(args,'python_environment',None) else {}),
                                        **({'command_backend':args.command_backend} if getattr(args,'command_backend','nofork')!='nofork' else {}),
                                        **({'tool_executables':extra_executables} if extra_executables else {}),
                                        **({'allow_workspace_executables':True} if getattr(args,'allow_workspace_executables',False) else {}),
                                        **({'tool_read_paths':args.tool_read_path} if getattr(args,'tool_read_path',None) else {}),
                                        **({'tool_sysctl_read':args.tool_sysctl_read} if getattr(args,'tool_sysctl_read',None) else {}),
                                        **({'read_only_paths':args.read_only_path} if args.read_only_path else {}))
                    tools.selected_skills = args.skill
                else:
                    tools = Tools(args.workspace, **({'node_executable':args.node_executable} if getattr(args, 'node_executable', None) else {}),
                                  **({'python_environment':args.python_environment} if getattr(args,'python_environment',None) else {}),
                                        **({'command_backend':args.command_backend} if getattr(args,'command_backend','nofork')!='nofork' else {}),
                                        **({'tool_executables':extra_executables} if extra_executables else {}),
                                        **({'allow_workspace_executables':True} if getattr(args,'allow_workspace_executables',False) else {}),
                                        **({'tool_read_paths':args.tool_read_path} if getattr(args,'tool_read_path',None) else {}),
                                        **({'tool_sysctl_read':args.tool_sysctl_read} if getattr(args,'tool_sysctl_read',None) else {}),
                                  **({'read_only_paths':args.read_only_path} if args.read_only_path else {}))
                if getattr(args, 'stream_tools', False):
                    from .tool_display import ToolOutputDisplay
                    tools.on_output = ToolOutputDisplay()
                model = None
                if args.command != 'abort':
                    on_text = (lambda text: print(text,end='',file=sys.stderr,flush=True)) if args.stream_text else None
                    if args.model_config:
                        from .providers import load_model_config
                        routing_flags={'--model','--base-url','--api-key-env','--protocol','--token-parameter',
                                       '--no-parallel-parameter','--thinking','--reasoning-effort','--ca-bundle'}
                        if any(value.split('=',1)[0] in routing_flags for value in raw_argv):
                            raise ValueError('model_config_conflicting_flags')
                        configured=load_model_config(args.model_config,args.workspace)
                        configured['stream']=configured.get('stream',False) or args.stream or args.stream_text
                        model=OpenAIModel(allow_api=args.allow_api,on_text=on_text,**configured)
                    else:
                        model=OpenAIModel(args.model,args.base_url,args.api_key_env,args.allow_api,
                                protocol=args.protocol,stream=args.stream or args.stream_text,token_parameter=args.token_parameter,
                                parallel_parameter=not args.no_parallel_parameter,thinking=args.thinking,
                                reasoning_effort=args.reasoning_effort,ca_bundle=args.ca_bundle,on_text=on_text)
                    paid_model = None
                    paid_route = args.allow_api and model.api_key_env == 'DEEPSEEK_API_KEY'
                    if args.prepaid_account and not paid_route:raise ValueError('prepaid_account_requires_deepseek_api')
                    if args.budget_ledger is not None and not paid_route:
                        raise ValueError('budget_ledger_requires_deepseek_api')
                    if paid_route:
                        from .budget import BudgetedModel, BudgetLedger
                        budget_path = (Path(args.budget_ledger) if args.budget_ledger is not None
                                       else Path(__file__).resolve().parents[1]/'.lab'/'paid-budget.sqlite')
                        try:
                            ledger = BudgetLedger.open_existing(budget_path, workspace=args.workspace)
                        except ValueError:
                            raise ModelError('budget_ledger_unavailable') from None
                        # Routing and ledger admission precede credential loading.
                        paid_model = (BudgetedModel(model,ledger,prepaid_account=True) if args.prepaid_account
                                      else BudgetedModel(model,ledger))
                    if args.load_deepseek_key:
                        if (not args.allow_api or model.api_key_env != 'DEEPSEEK_API_KEY'
                                or model.base_url not in ('https://api.deepseek.com','https://api.deepseek.com/anthropic')):
                            raise ValueError('credential_scope_mismatch')
                        from .budget import load_first_line_key
                        previous_key = os.environ.get('DEEPSEEK_API_KEY')
                        load_first_line_key()
                        key_loaded = True
                    if paid_model is not None:
                        model = paid_model
                if getattr(args, 'mcp_config', None):
                    from .registry import MCPToolRegistry
                    tools = resources.enter_context(MCPToolRegistry(tools, args.mcp_config))
                observer = (lambda event: print(json.dumps({'event': event}), flush=True)) if getattr(args,'events',False) else None
                engine = Engine(store, tools, model, observer=observer)
                if args.command == 'compact':
                    if not args.allow_api:
                        raise ValueError('api_not_enabled')
                    if not os.environ.get(model.api_key_env):
                        raise ValueError('api_key_missing')
                    state = engine.compact(args.task_id)
                elif args.command in ('run', 'continue', 'chat'):
                    if not args.allow_api:
                        raise ValueError('api_not_enabled')
                    if not os.environ.get(model.api_key_env):
                        raise ValueError('api_key_missing')
                    options = {k: getattr(args, k) for k in Limits.__dataclass_fields__ if hasattr(args, k) and getattr(args, k) is not None}
                    if args.no_auto_compact:
                        if args.auto_compact_bytes is not None:
                            raise ValueError('conflicting_compaction_options')
                        options['auto_compact_bytes'] = None
                    if args.command == 'chat':
                        if args.track_file_context and args.task_id is not None:
                            raise ValueError('file_context_requires_new_task')
                        from .interactive import chat_loop
                        saved_limits = store.load(args.task_id)['limits'] if args.task_id else {}
                        limits = Limits(**{**saved_limits, **options}) if options else None
                        tid = chat_loop(engine, task_id=args.task_id, limits=limits, stream_text=args.stream_text,
                                        **({'track_file_context': True} if args.track_file_context else {}))
                        print(json.dumps({'session_closed': True, 'task_id': tid}))
                        return 0
                    if args.command == 'continue':
                        tid = args.task_id
                        engine.continue_turn(tid, args.task, Limits(**{**store.load(tid)['limits'], **options}) if options else None)
                    else:
                        tid = engine.create(args.task, Limits(**options),
                                            **({'track_file_context': True} if args.track_file_context else {}))
                    print(json.dumps({'task_id': tid}), flush=True)
                    state = engine.run(tid)
                else:
                    state = engine.abort(args.task_id) if args.command == 'abort' else engine.run(args.task_id)
                result = public_status(state)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            if args.command == 'compact':
                return 0 if state.get('last_compaction', {}).get('status') == 'installed' else 3 if state['phase'] == 'needs_confirmation' else 2
            if args.command in ('run', 'resume', 'continue'):
                return 0 if state['phase'] == 'completed' else 3 if state['phase'] in ('needs_confirmation','awaiting_approval') else 2
            return 0
    except RuntimeConfigurationError as error:
        code=error.code if type(error.code) is str and error.code in ('invalid_runtime_config','runtime_config_flag_conflict') else 'invalid_runtime_config'
        print(json.dumps({'error':code,'hint':'Use one explicit --runtime-config outside workspace or individual runtime flags. No keys/model/budget fields or automatic overrides are allowed.'}),file=sys.stderr)
        return 2
    except (sqlite3.Error, StorageUnavailable):
        print(json.dumps({'error': 'storage_unavailable'}), file=sys.stderr)
        return 2
    except ModelError as exc:
        print(json.dumps({'error': exc.code}), file=sys.stderr)
        return 2
    except ToolError as error:
        candidate = error.code
        if (type(candidate) is str and len(candidate) <= 64
                and candidate in ('command_runtime_not_allowed', 'command_runtime_changed')):
            result = {'error': candidate, 'hint': 'Check --command-backend, --node-executable, --python-environment and --tool-executable/--allow-workspace-executables/--tool-read-path/--tool-sysctl-read or --runtime-config; use trusted external runtimes and repeat the saved configuration on resume or approval.'}
        elif type(candidate) is str and candidate in ('native_group_unconfirmed','tool_output_incomplete'):
            result = {'error':candidate,'hint':'Inspect saved status and job-status; incomplete execution is not permission to replay effects.'}
        else:
            result = {'error': 'tool_error', 'hint': 'Inspect saved status before retrying; this error does not confirm effects or recovery.'}
        print(json.dumps(result), file=sys.stderr)
        return 2
    except JobBlocked:
        print(json.dumps({'error': 'workspace_quarantined', 'inspect_with': 'job-status'}), file=sys.stderr)
        return 2
    except JobStateError as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        return 2
    except (ValueError, KeyError, Busy, OSError):
        # Never print arbitrary exception text or task contents to an error stream.
        print(json.dumps({'error': 'configuration_or_storage_error'}), file=sys.stderr)
        return 2
    finally:
        if key_loaded:
            if previous_key is None:
                os.environ.pop('DEEPSEEK_API_KEY', None)
            else:
                os.environ['DEEPSEEK_API_KEY'] = previous_key


if __name__ == '__main__':
    sys.exit(main())
