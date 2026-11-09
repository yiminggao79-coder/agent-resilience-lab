#!/usr/bin/env python3
"""Opt-in real multi-turn context experiments; no API flag means no side effects.

Only candidate project/task text reaches the model. Frozen oracle answers stay
in the parent. Offline injected models establish contracts, never quality rates.
"""
import argparse
import ast
import hashlib
import difflib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from agent_lab import compaction, sandbox
from agent_lab.budget import BudgetLedger, BudgetedModel, load_first_line_key, RATES
from agent_lab.coding_tools import CodingTools
from agent_lab.context import build_context, ContextLimit
from agent_lab.engine import Engine, Limits, public_status
from agent_lab.model import OpenAIModel, ModelError
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import ToolError
from scripts.deepseek_live import RecordingModel
from scripts.evaluate_acceptance import copy_project, digest_file, implementation_snapshot, json_equal, safe_path
from scripts.evaluate_acceptance_v3 import _safe_to_observe, _probe_backend



EXPERIMENT_PROFILES = {
    'legacy48k': {'thinking':'disabled','reasoning_effort':None,'max_output_tokens':4096,
        'model_timeout':90,'working_bytes':48000,'summary_input_bytes':None,
        'protocol_file':'docs/context-mission-runtime-protocol.md'},
    'thinking128k': {'thinking':'enabled','reasoning_effort':'low','max_output_tokens':16384,
        'model_timeout':180,'working_bytes':131072,'summary_input_bytes':32000,
        'protocol_file':'docs/context-mission-runtime-protocol-r7.md'},
}


def experiment_settings(name):
    if type(name) is not str or name not in EXPERIMENT_PROFILES:
        raise ValueError('invalid_experiment_profile')
    return dict(EXPERIMENT_PROFILES[name])



def validate_frozen_runtime(frozen, selected_profile, model_name, *, development=False):
    if frozen is not None and (development or frozen != {'experiment_profile':selected_profile,'model':model_name}):
        raise ValueError('frozen_runtime_profile_mismatch')


def child_environment():
    return {'PATH':os.defpath,'LANG':'C.UTF-8'}


def atomic_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    with temporary.open('x') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def verify_suite(suite,*,repository=REPO):
    suite,repository=Path(suite).resolve(),Path(repository).resolve()
    if not suite.is_relative_to(repository):raise ValueError('suite_outside_repository')
    frozen=json.loads((suite/'FREEZE.json').read_text());rows=frozen['files']
    canonical=json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if hashlib.sha256(canonical).hexdigest()!=frozen['inventory_sha256']:raise ValueError('freeze_inventory_mismatch')
    seen=set()
    for row in rows:
        name=row['path']
        if name in seen:raise ValueError('duplicate_frozen_path')
        seen.add(name);path=safe_path(repository,name)
        if path.stat().st_size!=row['bytes'] or digest_file(path)!=row['sha256']:
            raise ValueError('freeze_file_mismatch')
    actual={str(p.relative_to(repository)) for p in suite.rglob('*') if p.is_file() and p.name!='FREEZE.json'}
    expected={name for name in seen if (repository/name).is_relative_to(suite)}
    if actual!=expected:raise ValueError('freeze_paths_mismatch')
    return digest_file(suite/'FREEZE.json')


def load_suite(suite):
    suite=Path(suite).resolve();freeze=verify_suite(suite)
    manifest=json.loads((suite/'manifest.json').read_text())
    if manifest.get('version')!=1 or not isinstance(manifest.get('cases'),list) or not manifest['cases']:
        raise ValueError('invalid_mission_manifest')
    seen=set()
    for case in manifest['cases']:
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}',case['id']) or case['id'] in seen:
            raise ValueError('invalid_case_identity')
        seen.add(case['id']);root=safe_path(suite,case['workspace_dir'])
        if not root.is_dir() or not case['immutable_files']:raise ValueError('invalid_workspace')
        for name in case['immutable_files']:digest_file(safe_path(root,name))
        if not 3<=len(case['turns'])<=6:raise ValueError('invalid_turn_count')
        for turn in case['turns']:
            task=safe_path(suite,turn['task']).read_text()
            if not 1<=len(task.encode())<=65536:raise ValueError('invalid_task')
            oracle=turn['oracle']
            if not all(type(oracle.get(k)) is str and re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,79}',oracle[k]) for k in ('module','function')):
                raise ValueError('invalid_oracle')
            if (not isinstance(oracle.get('inputs'),list) or not 1<=len(oracle['inputs'])<=512
                    or not all(isinstance(args,list) for args in oracle['inputs'])
                    or not isinstance(oracle.get('expected'),list) or len(oracle['expected'])!=len(oracle['inputs'])):
                raise ValueError('invalid_oracle')
    return freeze,manifest['cases']


def grade_turn(row):
    if row.get('immutable_files_unchanged') is False:return False
    for key in ('original_tests_exit_code','all_tests_exit_code'):
        if type(row.get(key)) is int and row[key]!=0:return False
    if row.get('oracle_matches') is False:return False
    if (row.get('immutable_files_unchanged') is True and type(row.get('original_tests_exit_code')) is int
            and row['original_tests_exit_code']==0 and type(row.get('all_tests_exit_code')) is int
            and row['all_tests_exit_code']==0 and row.get('oracles_complete') is True
            and row.get('oracle_matches') is True):return True
    return None


OBSERVE_STDIN='''import importlib.util,json,os,sys
p=json.load(sys.stdin)
sys.path.insert(0,os.getcwd())
seen=set()
prefix=os.getcwd()+os.sep
def record_call(frame,event,arg):
 filename=frame.f_code.co_filename
 if event=="call" and frame.f_code.co_name!="<module>" and filename.endswith(".py") and len(seen)<200:
  filename=os.path.abspath(filename)
  if filename.startswith(prefix):seen.add(os.path.relpath(filename,os.getcwd()))
 return record_call
sys.settrace(record_call)
spec=importlib.util.spec_from_file_location("_candidate",p["module"]+".py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
f=getattr(m,p["function"])
values=[f(*args) for args in p["cases"]]
coverage_complete=sys.gettrace() is record_call and len(seen)<200
sys.settrace(None)
print(json.dumps({"observations":values,"executed_python_paths":sorted(seen),"coverage_complete":coverage_complete},allow_nan=False,separators=(",",":")))
'''


def observe_stdin(root,module,function,cases,timeout,*,read_only_paths=()):
    """Only finite inputs enter fd0; expected answers never leave the parent."""
    if not all(type(n) is str and re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,79}',n) for n in (module,function)):
        raise ValueError('invalid_observation_name')
    raw=json.dumps({'module':module,'function':function,'cases':cases},allow_nan=False).encode()
    if len(raw)>65536:raise ValueError('observation_input_limit')
    root=Path(root).resolve()
    from agent_lab.commands import _check_workspace
    _check_workspace(root);sandbox._validate_read_only(root,read_only_paths)
    with tempfile.TemporaryDirectory(prefix='mission-observe-') as scratch:
        profile=sandbox._profile(root,Path(scratch),read_only_paths=read_only_paths)
        feed=Path(scratch)/'input.json';feed.write_bytes(raw)
        fd=os.open(str(feed),os.O_RDONLY|os.O_NOFOLLOW);feed.unlink()
        try:
            bootstrap='import os\nos.dup2('+str(fd)+',0)\nos.close('+str(fd)+')\n'+OBSERVE_STDIN
            result=sandbox._collect([sandbox.BACKEND_PATH,'-p',profile,str(Path(sys.executable).resolve()),
                '-I','-B','-S','-c',bootstrap],root,child_environment(),timeout,pass_fds=(fd,))
        finally:os.close(fd)
    observations=None
    try:
        data=json.loads(result['stdout']);observations=data['observations']
        complete=(result['exit_code']==0 and not result['stdout_truncated'] and isinstance(observations,list)
                  and len(observations)==len(cases) and all(sandbox._json_observation(v) for v in observations))
    except (ValueError,TypeError,KeyError,RecursionError):complete=False
    paths=data.get('executed_python_paths',[]) if complete else []
    coverage=bool(complete and data.get('coverage_complete') is True and isinstance(paths,list)
                  and len(paths)<=200 and all(isinstance(p,str) and len(p)<=512 and p.endswith('.py')
                  and not Path(p).is_absolute() and all(x and not x.startswith('.') for x in p.split('/')) for p in paths))
    return {'complete':complete,'observations':observations if complete else None,'exit_code':result['exit_code'],
            'executed_python_paths':paths if coverage else [],'coverage_complete':coverage}


def run_owned(argv,stdout_path,stderr_path,*,marker=None,timeout=950):
    if marker is not None and Path(marker).exists():raise ValueError('fault_marker_already_exists')
    with Path(stdout_path).open('xb') as stdout,Path(stderr_path).open('xb') as stderr:
        proc=subprocess.Popen(argv,stdout=stdout,stderr=stderr,env=child_environment(),start_new_session=True)
        injected=False;timed_out=False;deadline=time.monotonic()+timeout
        try:
            while proc.poll() is None:
                if marker is not None and Path(marker).is_file():
                    try:body=json.loads(Path(marker).read_text())
                    except ValueError:time.sleep(.02);continue
                    if type(body.get('pid')) is not int or body['pid']!=proc.pid:
                        raise ValueError('fault_marker_owner_mismatch')
                    os.killpg(proc.pid,signal.SIGKILL);injected=True;break
                if time.monotonic()>=deadline:
                    os.killpg(proc.pid,signal.SIGKILL);timed_out=True;break
                time.sleep(.02)
            proc.wait(timeout=15)
            return {'pid':proc.pid,'returncode':proc.returncode,'injected':injected,'timed_out':timed_out}
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=15)


def _summary_identity(state):
    record=state.get('semantic_context')
    if not record:return None
    return {k:record[k] for k in ('generation','covered_until','source_sha256','summary_sha256')}


def archive_hash(state):
    return hashlib.sha256(json.dumps(state['messages'],sort_keys=True,ensure_ascii=True).encode()).hexdigest()


def packing_proof(messages,previous,budget):
    _,size,end=compaction.prepare(messages,previous,budget)
    _,full_size,eligible=compaction.prepare(messages,previous,2**40)
    return {'request_bytes':size,'full_request_bytes':full_size,'budget_bytes':budget,
            'covered_until':end,'eligible_until':eligible,'proper_subprefix':end<eligible and full_size>budget}


def is_implementation_path(name,protected=()):
    parts=Path(name).parts
    return (bool(parts) and name.endswith('.py') and not Path(name).is_absolute()
            and all(not p.startswith('.') and p not in ('tests','__pycache__') for p in parts)
            and not parts[-1].startswith('test_') and not parts[-1].endswith('_test.py')
            and not any(p=='.' or name==p or name.startswith(p.rstrip('/')+'/') for p in protected))


def implementation_versions(root,protected):
    from agent_lab.file_context import _stamp
    versions={};total=0;tools=CodingTools(root)
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('tests','__pycache__')]
        for name in files:
            path=Path(directory)/name;relative=str(path.relative_to(root))
            if not is_implementation_path(relative,protected):continue
            with tools._parent_directory(relative) as (parent_fd,leaf):
                raw,info=tools._read_at(parent_fd,leaf,2*1024*1024)
                if _stamp(info)!=_stamp(os.stat(leaf,dir_fd=parent_fd,follow_symlinks=False)):
                    raise ValueError('implementation_snapshot_unstable')
            digest=hashlib.sha256(raw).hexdigest();total+=len(raw)
            if len(versions)>=200 or total>8*1024*1024:raise ValueError('implementation_snapshot_limit')
            syntax=None
            try:
                tree=ast.parse(raw.decode('utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and node.body:
                        first=node.body[0]
                        if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str):
                            node.body=node.body[1:]
                syntax=hashlib.sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()
            except (SyntaxError,UnicodeError,ValueError,RecursionError):pass
            versions[relative]={'sha256':digest,'ast_sha256':syntax}
    return versions


def read_mutation_evidence(directory):
    return [json.loads(p.read_text()) for p in sorted(Path(directory).rglob('mutation-*-after.json'))]


def execute_turn(config,*,model=None):
    settings=experiment_settings(config.get('experiment_profile','legacy48k'))
    root=Path(config['workspace']).resolve();db=Path(config['db'])
    receipts=Path(config['receipt_dir']);receipts.mkdir(parents=True,mode=0o700)
    live=model is None;previous_key=os.environ.get('DEEPSEEK_API_KEY');loaded=False
    if live:
        if not config.get('allow_api') or not config.get('prepaid_account'):
            raise ValueError('live_api_not_authorized')
        delegate=OpenAIModel(config.get('model','deepseek-flash'),'https://api.deepseek.com','DEEPSEEK_API_KEY',True,
                    protocol='chat',stream=False,token_parameter='max_tokens',parallel_parameter=False,
                    thinking=settings['thinking'],reasoning_effort=settings['reasoning_effort'],
                    ca_bundle='/etc/ssl/cert.pem')
        model=RecordingModel(BudgetedModel(delegate,BudgetLedger.open_existing(REPO/'.lab/paid-budget.sqlite'),
                                         prepaid_account=True),receipts)
    tools=CodingTools(root,read_only_paths=tuple(config.get('read_only_paths',[])))
    try:
        with Store(db) as store:
            def observer(event):
                if event['kind']=='tool_started' and event.get('tool') in CODING_WRITES:
                    atomic_json(receipts/('mutation-'+event['operation_id']+'-before.json'),
                                implementation_versions(root,config.get('read_only_paths',[])))
                if event['kind']=='tool_finished' and event.get('tool') in CODING_WRITES:
                    before_path=receipts/('mutation-'+event['operation_id']+'-before.json')
                    if before_path.is_file():
                        before=json.loads(before_path.read_text())
                        after=implementation_versions(root,config.get('read_only_paths',[]));changes=[]
                        for name in sorted(set(before)|set(after)):
                            left,right=before.get(name,{}),after.get(name,{})
                            if left.get('ast_sha256')!=right.get('ast_sha256') and left.get('sha256')!=right.get('sha256'):
                                changes.append({'path':name,'before_sha256':left.get('sha256'),
                                  'after_sha256':right.get('sha256'),'before_ast_sha256':left.get('ast_sha256'),
                                  'after_ast_sha256':right.get('ast_sha256')})
                        atomic_json(receipts/('mutation-'+event['operation_id']+'-after.json'),
                                    {'operation_id':event['operation_id'],'changes':changes})
                if event['kind'] in ('model_finished','model_error','compaction_installed','compaction_failed'):
                    atomic_json(receipts/'receipt-inventory.json',getattr(model,'receipts',[]))
                if event['kind']=='compaction_started':
                    saved=store.load(event['task_id'])
                    atomic_json(receipts/('packing-'+event['operation_id']+'.json'),{
                        'operation_id':event['operation_id'],**packing_proof(saved['messages'],
                            saved.get('semantic_context'),saved['limits'].get('max_summary_input_bytes')
                            or saved['limits']['max_context_bytes'])})
                if event['kind']=='compaction_installed' and config.get('fault_marker'):
                    saved=store.load(event['task_id'])
                    if saved['phase']!='ready' or saved.get('pending') or saved.get('tool_queue'):
                        return
                    atomic_json(config['fault_marker'],{'pid':os.getpid(),'task_id':saved['id'],
                        'turn_id':saved['turn_id'],'revision':saved['revision'],'summary':_summary_identity(saved),
                        'archive_sha256':archive_hash(saved),
                        'event_seq':store.events(saved['id'])[-1]['seq']})
                    while True:signal.pause()
            engine=Engine(store,tools,model,observer=observer);limits=Limits(**config.get('limits',{}))
            initial=None;initial_archive=None
            if config['action']=='start':task=engine.create(config['task'],limits,track_file_context=True)
            else:
                task=config['task_id'];old=store.load(task);initial=_summary_identity(old);initial_archive=archive_hash(old)
                if config['action']=='continue':
                    if config.get('rollover'):
                        try:
                            _,stats=build_context(old['messages'],engine._schemas(),limits.max_context_bytes,
                                                 semantic=old.get('semantic_context'))
                            # Trigger once at the prior view size; halving the
                            # whole request can fall below immutable schema/user
                            # overhead and cause continuous summary thrashing.
                            watermark=max(1,min(limits.max_context_bytes,stats['retained_bytes']))
                        except ContextLimit:watermark=limits.max_context_bytes
                        limits=Limits(**{**config.get('limits',{}),'auto_compact_bytes':watermark})
                    engine.continue_turn(task,config['task'],limits)
                elif config['action']!='resume':raise ValueError('invalid_child_action')
            before_run=store.load(task)
            atomic_json(receipts/'segment.json',{'pid':os.getpid(),'task_id':task,
                'turn_id':before_run['turn_id'],'base_model_calls':before_run['metrics']['model_calls']})
            if live:load_first_line_key();loaded=True
            state=engine.run(task);approvals=0
            while state['phase']=='awaiting_approval':
                intent=state['pending']['intent']
                if intent.get('name')!='run_command':break
                from agent_lab.commands import validate_command
                validate_command(root,intent,read_only_paths=tuple(config.get('read_only_paths',[])))
                request=engine.approval_request(task)
                engine.decide_approval(task,request['request_id'],request['digest'],True)
                approvals+=1;state=engine.run(task)
            result={**public_status(state),'pid':os.getpid(),'resume_initial_summary':initial,
                    'resume_initial_archive_sha256':initial_archive,
                    'summary':_summary_identity(state),'limits':state['limits'],
                    'sandbox_commands_preapproved':approvals,
                    'safe_to_observe':_safe_to_observe(state),
                    'trace':[e for e in store.events(task) if e.get('turn_id')==state['turn_id']],
                    'model_receipts':getattr(model,'receipts',[])}
            return result
    finally:
        if loaded:
            if previous_key is None:os.environ.pop('DEEPSEEK_API_KEY',None)
            else:os.environ['DEEPSEEK_API_KEY']=previous_key


ORIGINAL_TEST_BOOTSTRAP='''import os,sys,unittest
sys.path.insert(0,os.getcwd())
s=unittest.defaultTestLoader.discover("tests",pattern="test_original.py")
r=unittest.TextTestRunner(verbosity=0).run(s)
sys.exit(0 if s.countTestCases()>0 and r.wasSuccessful() else 1)
'''


def judge_turn(case,stage,root,trial,original_hashes,safe):
    row={'immutable_files_unchanged':None,'original_tests_exit_code':None,'all_tests_exit_code':None,
         'oracles_complete':None,'oracle_matches':None,'observations':[],'executed_python_paths':[]}
    if not safe:return {**row,'quality_passed':None,'observation_status':'blocked_unresolved'}
    row['immutable_files_unchanged']=all(digest_file(safe_path(root,n))==h for n,h in original_hashes.items())
    if not row['immutable_files_unchanged']:return {**row,'quality_passed':False,'observation_status':'constraint_violation'}
    paths=tuple(original_hashes)
    try:
        tests=trial/'all-tests';copy_project(root,tests)
        row['all_tests_exit_code']=sandbox.run_tests(tests,12,read_only_paths=paths)['exit_code']
        original=trial/'original-tests';copy_project(root,original)
        row['original_tests_exit_code']=sandbox._run_tests(original,12,ORIGINAL_TEST_BOOTSTRAP,
                                                         read_only_paths=paths)['exit_code']
        complete=True;matches=True
        for index,turn in enumerate(case['turns'][:stage+1]):
            oracle=turn['oracle'];observed_root=trial/('oracle-'+str(index));copy_project(root,observed_root)
            observed=observe_stdin(observed_root,oracle['module'],oracle['function'],oracle['inputs'],12,
                                   read_only_paths=paths)
            row['observations'].append({'turn':index+1,**observed})
            row['executed_python_paths']=sorted(set(row['executed_python_paths'])|set(observed['executed_python_paths']))
            complete=complete and observed['complete']
            if observed['complete']:matches=matches and json_equal(observed['observations'],oracle['expected'])
            for n,h in original_hashes.items():
                row['immutable_files_unchanged']=row['immutable_files_unchanged'] and digest_file(observed_root/n)==h
        for observed_root in (tests,original):
            row['immutable_files_unchanged']=row['immutable_files_unchanged'] and all(
                digest_file(observed_root/n)==h for n,h in original_hashes.items())
        row.update(oracles_complete=complete,oracle_matches=matches if complete else None,
                   observation_status='observed' if complete else 'observations_incomplete')
    except (OSError,ValueError,ToolError):row['observation_status']='observation_unavailable'
    row['quality_passed']=grade_turn(row);return row


def request_summary_evidence(directory):
    result=[]
    for path in sorted(Path(directory).rglob('model-*-request.json')):
        request=json.loads(path.read_text())
        if not request.get('tools'):continue
        segment_path=path.parent/'segment.json'
        segment=json.loads(segment_path.read_text()) if segment_path.is_file() else {}
        call_number=(segment['base_model_calls']+int(path.name.split('-')[1])
                     if 'base_model_calls' in segment else None)
        for message in request['messages']:
            text=message.get('content')
            if isinstance(text,str) and text.startswith(compaction.SUMMARY_BOUNDARY+'\n'):
                body=json.loads(text.split('\n',1)[1])
                result.append({'source_sha256':body['source_sha256'],'covered_until':body['covered_until'],
                               'summary_sha256':hashlib.sha256(body['summary'].encode()).hexdigest(),
                               'request_model_call_number':call_number,'pid':segment.get('pid')})
    return result


CODING_WRITES=('write_file','edit_file','apply_patch','apply_patch_text','delete_file','run_command','run_skill_script')


def continuity_proof(turns,events):
    """Bind an automatic Session note to the exact final Coding request."""
    failed={'passed':False,'coding_model_call':None,'coding_event_seq':None,'installed_seq':None,
            'reason':'no_verified_implementation_change'}
    if not turns:return failed
    final=turns[-1]
    writes=[e for e in final.get('trace',[]) if e['kind']=='tool_finished'
            and e.get('ok') is True and e.get('tool') in CODING_WRITES]
    reached=set(final.get('executed_python_paths',[]))
    mutations={m['operation_id']:m for m in final.get('mutation_evidence',[])}
    writes=[e for e in writes if any(is_implementation_path(c['path']) and c['path'] in reached
            and c.get('before_sha256')!=c.get('after_sha256') and c.get('after_ast_sha256') is not None
            and c.get('before_ast_sha256')!=c.get('after_ast_sha256')
            for c in mutations.get(e.get('operation_id'),{}).get('changes',[]))]
    if not writes:return failed
    write=max(writes,key=lambda e:e['seq'])
    starts=[e for e in final.get('trace',[]) if e['kind']=='model_started' and e['seq']<write['seq']]
    if not starts:return failed
    start=max(starts,key=lambda e:e['seq'])
    notes=[n for n in final.get('model_visible_summaries',[]) if n.get('request_model_call_number')==start['model_calls']]
    for note in notes:
        for event in events:
            if (event['kind']=='compaction_installed' and event.get('compaction_automatic') is True
                    and event['seq']<start['seq'] and event.get('source_sha256')==note['source_sha256']
                    and event.get('summary_sha256')==note['summary_sha256']
                    and event.get('covered_messages')==note['covered_until']):
                return {'passed':True,'coding_model_call':start['model_calls'],'coding_event_seq':write['seq'],
                        'installed_seq':event['seq'],'installed_turn_id':event.get('turn_id'),'note':note,
                        'operation_id':write['operation_id'],
                        'changed_called_paths':sorted({c['path'] for c in mutations[write['operation_id']]['changes']
                            if c['path'] in reached and is_implementation_path(c['path'])})}
    return {**failed,'coding_model_call':start['model_calls'],'coding_event_seq':write['seq'],
            'reason':'coding_request_note_not_bound'}


def receipt_inventory(directory):
    """Recover every request segment, including an owned Runner killed at commit."""
    records=[]
    for request in sorted(Path(directory).rglob('model-*-request.json')):
        prefix=request.name.removesuffix('request.json');parent=request.parent
        index=int(prefix.split('-')[1]);usage=None;outcome='unknown';details={}
        response=parent/(prefix+'response.json');error=parent/(prefix+'error.json')
        sidecar=parent/'receipt-inventory.json';timing={}
        if sidecar.is_file():
            timing=next((r for r in json.loads(sidecar.read_text()) if r['index']==index),{})
        if response.is_file():
            result=json.loads(response.read_text());usage=result.get('usage');outcome='complete'
            details={'response_sha256':digest_file(response),'finish_reason':result.get('finish_reason'),
                     'tools':[c['function']['name'] for c in result['message'].get('tool_calls',[])]}
        elif error.is_file():
            result=json.loads(error.read_text());outcome='error'
            details={'error_sha256':digest_file(error),'error_code':result.get('error_code'),
                     'http_status':result.get('http_status'),'diagnostic':result.get('diagnostic')}
        records.append({'segment':str(parent.relative_to(directory)),'index':index,'outcome':outcome,
            'usage':usage,'elapsed_seconds':timing.get('elapsed_seconds'),
            'request_sha256':digest_file(request),**details})
    return records


def usage_totals(records,*,model_name='deepseek-flash'):
    known=[r['usage'] for r in records if isinstance(r.get('usage'),dict)
           and all(type(r['usage'].get(k)) is int for k in ('input_tokens','output_tokens'))]
    inputs=sum(u['input_tokens'] for u in known);outputs=sum(u['output_tokens'] for u in known)
    a,b=RATES[model_name]
    return {'requests':len(records),'known_input_tokens':inputs,'known_output_tokens':outputs,
            'unknown_usage_requests':len(records)-len(known),
            'known_peak_cache_miss_estimate_cny':(inputs*a+outputs*b)/1e6,
            'pricing_cny_per_million':{'input_peak_cache_miss':a,'output_peak':b},
            'estimate_is_invoice':False}


def _run_case(suite,case,trial,*,development=False,kill_after_summary=False,model_name='deepseek-flash',
              experiment_profile='legacy48k'):
    settings=experiment_settings(experiment_profile)
    trial.mkdir(parents=True,mode=0o700);root=trial/'project';copy_project(safe_path(suite,case['workspace_dir']),root)
    original={n:digest_file(safe_path(root,n)) for n in case['immutable_files']}
    turns=[];task=None;kill_proof=None
    for stage,turn in enumerate(case['turns']):
        phase=trial/('turn-'+str(stage+1));phase.mkdir(mode=0o700)
        final_stage=stage==len(case['turns'])-1
        config={'workspace':str(root.resolve()),'db':str((trial/'tasks.sqlite').resolve()),
                'read_only_paths':list(original),'task':safe_path(suite,turn['task']).read_text(),'model':model_name,
                'experiment_profile':experiment_profile,
                'action':'start' if stage==0 else 'continue','task_id':task,
                'limits':{'max_steps':50,'max_seconds':900,'model_timeout':settings['model_timeout'],'tool_timeout':12,
                          'max_output_tokens':settings['max_output_tokens'],
                          'max_context_bytes':(64000 if final_stage else 131072) if development else settings['working_bytes'],
                          'max_auto_compactions':6},
                'receipt_dir':str((phase/'first-receipts').resolve()),'allow_api':True,'prepaid_account':True}
        summary_bytes=32000 if development else settings['summary_input_bytes']
        if summary_bytes is not None:config['limits']['max_summary_input_bytes']=summary_bytes
        # Every Turn can overflow, including before the final task. Keep the
        # threshold above fixed schema overhead and summarize before losing groups.
        config['limits']['auto_compact_bytes']=int(config['limits']['max_context_bytes']*.8)
        marker=phase/'summary-checkpoint.json'
        if kill_after_summary and kill_proof is None:config['fault_marker']=str(marker.resolve())
        config_path=phase/'config.json';atomic_json(config_path,config)
        command=[sys.executable,str(Path(__file__).resolve()),'--child-config',str(config_path.resolve())]
        first=run_owned(command,phase/'first-stdout.txt',phase/'first-stderr.txt',
                        marker=marker if config.get('fault_marker') else None)
        processes=[first];result_path=phase/'result.json'
        if first['injected']:
            saved=json.loads(marker.read_text());task=saved['task_id']
            config.update(action='resume',task_id=task,receipt_dir=str((phase/'resume-receipts').resolve()))
            config.pop('fault_marker',None);atomic_json(phase/'resume-config.json',config)
            resumed=run_owned([sys.executable,str(Path(__file__).resolve()),'--child-config',str((phase/'resume-config.json').resolve())],
                              phase/'resume-stdout.txt',phase/'resume-stderr.txt')
            processes.append(resumed)
            kill_proof={'marker':saved,'killed_returncode':first['returncode'],'new_pid':resumed['pid'],
                        'different_pid':resumed['pid']!=first['pid']}
        if not result_path.is_file():
            records=receipt_inventory(phase)
            row={'turn':stage+1,'quality_passed':None,'processes':processes,'reason':'missing_child_result',
                 'model_receipts':records,'usage_totals':usage_totals(records,model_name=model_name)}
            turns.append(row);atomic_json(phase/'stage-evidence.json',row);break
        child=json.loads(result_path.read_text());task=child.get('id',task)
        if kill_proof and child.get('turn_id')==kill_proof['marker']['turn_id']:
            kill_proof['saved_summary_restored']=child.get('resume_initial_summary')==kill_proof['marker']['summary']
            kill_proof['saved_archive_restored']=child.get('resume_initial_archive_sha256')==kill_proof['marker']['archive_sha256']
            old_ops=set()
            with Store(trial/'tasks.sqlite') as store:
                events=store.events(task)
                old_ops={e.get('operation_id') for e in events if e['seq']<=kill_proof['marker']['event_seq'] and e['kind']=='tool_finished'}
                new_ops={e.get('operation_id') for e in events if e['seq']>kill_proof['marker']['event_seq'] and e['kind']=='tool_started'}
            kill_proof['no_prior_operation_id_reexecuted']=not (old_ops & new_ops)
        judge=judge_turn(case,stage,root,phase,original,child.get('safe_to_observe') is True)
        row={'turn':stage+1,**child,**judge,'processes':processes,
             'model_visible_summaries':request_summary_evidence(phase)}
        row['model_receipts']=receipt_inventory(phase)
        row['mutation_evidence']=read_mutation_evidence(phase)
        row['usage_totals']=usage_totals(row['model_receipts'],model_name=model_name)
        installed={e['operation_id'] for e in child.get('trace',[]) if e['kind']=='compaction_installed'}
        row['packing']=[json.loads(p.read_text()) for p in sorted(phase.rglob('packing-*.json'))
                        if json.loads(p.read_text())['operation_id'] in installed]
        turns.append(row)
        atomic_json(phase/'stage-evidence.json',row)
        if child.get('phase')!='completed' or judge['quality_passed'] is not True:break
    try:
        with Store.open_readonly(trial/'tasks.sqlite') as store:events=store.events(task)
    except (ValueError,StorageUnavailable):events=[]
    proof=continuity_proof(turns,events)
    summaries=[e for e in events if e['kind']=='compaction_installed' and e.get('compaction_automatic') is True]
    runtime_passed=bool(len(turns)==len(case['turns']) and proof['passed']
                        and all(row.get('phase')=='completed' for row in turns)
                        and len({row.get('pid') for row in turns})==len(turns))
    if kill_after_summary:
        if kill_proof:
            boundary=kill_proof['marker']['event_seq']
            old_ops={e['operation_id'] for e in events if e['seq']<=boundary and e['kind']=='tool_finished' and e.get('operation_id')}
            new_ops={e['operation_id'] for e in events if e['seq']>boundary and e['kind']=='tool_started' and e.get('operation_id')}
            kill_proof['no_prior_operation_id_reexecuted']=not (old_ops & new_ops)
            kill_proof['post_checkpoint_coding']=bool(proof['passed'] and proof['coding_event_seq']>boundary)
        runtime_passed=runtime_passed and bool(kill_proof and kill_proof['killed_returncode']==-9
            and kill_proof['different_pid'] and kill_proof['saved_summary_restored']
            and kill_proof['saved_archive_restored']
            and kill_proof['no_prior_operation_id_reexecuted'] and kill_proof['post_checkpoint_coding'])
    if development:
        generations=[e['compaction_generation'] for e in summaries]
        cursors=[e['covered_messages'] for e in summaries]
        runtime_passed=runtime_passed and bool(len(generations)>=2 and generations==sorted(set(generations))
            and cursors==sorted(set(cursors)) and any(p['proper_subprefix'] for t in turns for p in t.get('packing',[])))
    quality=(False if any(t.get('quality_passed') is False for t in turns) else
             True if len(turns)==len(case['turns']) and all(t.get('quality_passed') is True for t in turns) else None)
    return {'case':case['id'],'turns':turns,'quality_passed':quality,'runtime_passed':runtime_passed,
            'kill_proof':kill_proof,'continuity_proof':proof,'installed_summaries':summaries,
            'complete':quality is True and runtime_passed}


def export_candidate(source,trial,destination):
    root=trial/'project'
    if not root.is_dir():return
    copy_project(root,destination/'candidate')
    before={str(p.relative_to(source)):p for p in source.rglob('*') if p.is_file()}
    after={str(p.relative_to(destination/'candidate')):p for p in (destination/'candidate').rglob('*') if p.is_file()}
    patches=[]
    for name in sorted(set(before)|set(after)):
        left=before[name].read_text() if name in before else ''
        right=after[name].read_text() if name in after else ''
        patches.extend(difflib.unified_diff(left.splitlines(True),right.splitlines(True),
                      fromfile='before/'+name,tofile='after/'+name))
    (destination/'candidate.diff').write_text(''.join(patches))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=REPO/'evaluation/context_mission_v1')
    parser.add_argument('--model',choices=sorted(RATES),default='deepseek-flash')
    parser.add_argument('--experiment-profile',choices=sorted(EXPERIMENT_PROFILES),default='legacy48k')
    parser.add_argument('--allow-api',action='store_true');parser.add_argument('--prepaid-account',action='store_true')
    parser.add_argument('--development',action='store_true');parser.add_argument('--kill-after-summary',action='store_true')
    parser.add_argument('--child-config',type=Path)
    args=parser.parse_args(argv)
    args.suite=args.suite.resolve()
    if args.child_config:
        config=json.loads(args.child_config.read_text());target=args.child_config.parent/'result.json'
        try:
            result=execute_turn(config);atomic_json(target,result)
            print(json.dumps({k:result[k] for k in ('id','phase','reason','pid')}))
            return 0 if result['phase']=='completed' else 1
        except Exception:
            atomic_json(target,{'phase':'failed','reason':'child_internal_error','safe_to_observe':False,'pid':os.getpid()})
            print(json.dumps({'phase':'failed','reason':'child_internal_error'}));return 2
    if not args.allow_api:
        print(json.dumps({'mode':'preflight_only','api_requests_sent':0,'suite_checked':False}));return 0
    if not args.prepaid_account:raise ValueError('explicit_prepaid_authorization_required')
    snapshot=implementation_snapshot()
    if snapshot['runtime_dirty']:raise ValueError('commit_runtime_before_experiment')
    ledger=BudgetLedger.open_existing(REPO/'.lab/paid-budget.sqlite')
    if ledger.report()['halted']:raise ValueError('paid_requests_halted')
    freeze,cases=load_suite(args.suite)
    validate_frozen_runtime(json.loads((args.suite/'manifest.json').read_text()).get('runtime_profile'),
                            args.experiment_profile,args.model,development=args.development)
    settings=experiment_settings(args.experiment_profile)
    protocol_file=REPO/settings['protocol_file'];protocol_sha=digest_file(protocol_file)
    run_id=uuid.uuid4().hex[:16];private=REPO/'.lab/context-mission'/run_id;private.mkdir(parents=True,mode=0o700)
    if not _probe_backend(private/'backend-probe'):raise ValueError('native_backend_unavailable')
    report={'kind':'real_context_mission_development' if args.development else 'real_context_mission_acceptance',
            'run_id':run_id,'implementation':snapshot,'suite':str(args.suite.relative_to(REPO)),
            'freeze_sha256':freeze,'model':args.model,'protocol':'chat','stream':False,
            'experiment_profile':args.experiment_profile,'thinking':settings['thinking'],
            'reasoning_effort':settings['reasoning_effort'],
            'runtime_protocol_sha256':protocol_sha,
            'cases':[],'limitations':['Byte budgets are not provider token counts.',
            'Summaries and identity hashes do not prove semantic fidelity; external finite task observations do.',
            'Same AI environment authorship, not external statistical independence.',
            'Finite sandboxed Python judge, not tamperproof against malicious introspection.',
            'No external exactly-once guarantee; no old operation ID is mechanically replayed after the tested checkpoint.']}
    output=REPO/'evidence/context-mission/runs'/run_id/'report.json';output.parent.mkdir(parents=True)
    try:
        for case in cases:
            if not args.development:
                gate=REPO/'.lab/context-mission-exposures'/freeze/case['id'];gate.parent.mkdir(parents=True,exist_ok=True)
                with gate.open('x') as stream:json.dump({'run_id':run_id,'possible_exposure':True},stream)
            result=_run_case(args.suite,case,private/case['id'],development=args.development,
                             kill_after_summary=args.kill_after_summary,model_name=args.model,
                             experiment_profile=args.experiment_profile)
            export_candidate(safe_path(args.suite,case['workspace_dir']),private/case['id'],output.parent/case['id'])
            report['cases'].append(result);atomic_json(output,report)
            if ledger.report()['halted']:break
    except KeyboardInterrupt:report['error_code']='interrupted'
    except (ValueError,OSError,ToolError,StorageUnavailable):report['error_code']='experiment_unavailable'
    finally:
        try:report['freeze_unchanged']=verify_suite(args.suite)==freeze
        except (ValueError,OSError,KeyError):report['freeze_unchanged']=False
        report['runtime_unchanged']=implementation_snapshot()['source_sha256']==snapshot['source_sha256']
        try:report['runtime_protocol_unchanged']=digest_file(protocol_file)==protocol_sha
        except (ValueError,OSError):report['runtime_protocol_unchanged']=False
        report['usage_totals']=usage_totals([r for c in report['cases'] for t in c['turns'] for r in t.get('model_receipts',[])],model_name=args.model)
        report['complete']=bool(len(report['cases'])==len(cases) and all(c['complete'] for c in report['cases'])
                                and report['freeze_unchanged'] and report['runtime_unchanged']
                                and report['runtime_protocol_unchanged'] and not report.get('error_code'))
        atomic_json(output,report)
    print(json.dumps({'report':str(output.relative_to(REPO)),'complete':report['complete'],
                     'cases_measured':len(report['cases'])}))
    return 0 if report['complete'] else 1


if __name__=='__main__':sys.exit(main())
