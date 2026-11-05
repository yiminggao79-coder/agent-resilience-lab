"""Metadata projection of one checkpoint view, never an effect verifier."""
import copy
import re


def _label(value):
    return value if isinstance(value,str) and re.fullmatch('[a-z][a-z0-9_]{0,79}',value) else None


def _id(value):
    return value if isinstance(value,str) and re.fullmatch('[0-9a-f]{32}',value) else None


def _hash(value):
    return value if isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) else None


def _number(value):
    return value if type(value) is int and value>=0 else None


def describe_fork_origin(state):
    """A lineage view contains identifiers and counts, never copied history."""
    origin=state.get('fork_origin')
    if not isinstance(origin,dict):
        return None
    return {**{key:_id(origin.get(key)) for key in ('source_task_id','source_session_id','source_turn_id')},
            **{key:_number(origin.get(key)) for key in ('source_revision','message_count')},
            'history_sha256':_hash(origin.get('history_sha256')),
            **{key:origin.get(key) if type(origin.get(key)) is bool else None
               for key in ('workspace_shared','source_usage_unknown')}}


def describe_pending(state,pending_inputs,*,show_intent=False):
    if (type(show_intent) is not bool or not isinstance(state,dict)
            or not isinstance(pending_inputs,list) or any(not isinstance(row,dict) for row in pending_inputs)):
        raise ValueError('invalid_diagnostic_view')
    phase=_label(state.get('phase'))
    pending=state.get('pending') if isinstance(state.get('pending'),dict) else {}
    intent=pending.get('intent') if isinstance(pending.get('intent'),dict) else {}
    policy=_label(intent.get('policy'))
    metrics=state.get('metrics') if isinstance(state.get('metrics'),dict) else {}
    result={'task_id':_id(state.get('id')),'session_id':_id(state.get('session_id')),
            'turn_id':_id(state.get('turn_id')),'observed_revision':_number(state.get('revision')),
            'phase':phase,'reason':_label(state.get('reason')),
            'outcome_unresolved':bool(state.get('outcome_unresolved')),
            'usage_unknown':bool(state.get('usage_unknown')) or metrics.get('input_tokens') is None or metrics.get('output_tokens') is None,
            'model_request':'pending_unconfirmed' if phase=='model_pending' else 'not_pending_in_snapshot',
            'model_purpose':_label(state.get('model_purpose')),
            'runner_liveness':'not_checked','filesystem_verification':'not_performed',
            'queued_tools':len(state.get('tool_queue',[])) if isinstance(state.get('tool_queue',[]),list) else None,
            'operation':None,'pending_inputs':[],'guidance':[]}
    for row in pending_inputs:
        result['pending_inputs'].append({'request_id':_id(row.get('request_id')),
            'turn_id':_id(row.get('turn_id')),'status':_label(row.get('status')),
            'message_index':_number(row.get('message_index')),'applied_revision':_number(row.get('applied_revision'))})
    if pending:
        policies={'read':'may_repeat_read','verify':'verify_target_before_replay',
                  'unsafe':'never_replay_unknown_effect','verify_patch':'verify_each_file_then_resume_prefix',
                  'verify_patch_step':'verify_each_file_then_resume_prefix',
                  'verify_delete':'verify_delete_target','idempotent_directory':'reconcile_directory_state'}
        op={'operation_id':_id(pending.get('operation_id')),'status':_label(pending.get('status')),
            'tool':_label(intent.get('requested_tool',intent.get('name'))),'policy':policy,
            'recovery_policy':policies.get(policy,'unknown_policy_requires_review')}
        for key in ('before','desired'):
            if key in intent:op[key]=_hash(intent[key])
        for key in ('before_mode','desired_mode'):
            if key in intent:op[key]=intent[key] if type(intent[key]) is int and 0<=intent[key]<=0o777 else None
        if isinstance(intent.get('steps'),list):
            op['patch']={'cursor':_number(pending.get('patch_cursor',0)),
                'total_steps':len(intent['steps']),'inflight':pending.get('patch_inflight') is True,
                'confirmed_receipts':len(pending.get('patch_receipts',[])) if isinstance(pending.get('patch_receipts',[]),list) else None}
        approval=pending.get('approval')
        if isinstance(approval,dict):
            op['approval']={'request_id':_id(approval.get('request_id')),'digest':_hash(approval.get('digest')),
                            'status':_label(approval.get('status'))}
        result['operation']=op
    if result['outcome_unresolved'] or phase=='needs_confirmation':
        result['guidance'].append('unknown_outcome_blocks_automatic_continuation')
    if result['usage_unknown']:
        result['guidance'].append('missing_usage_is_not_zero_cost')
    if phase=='model_pending':
        result['guidance'].append('request_may_be_running_or_unconfirmed')
    if phase=='awaiting_approval':
        result['guidance'].append('review_exact_approval')
    if pending and policy=='unsafe':
        result['guidance'].append('unknown_external_effect_is_not_replayed')
    if result['pending_inputs']:
        result['guidance'].append('withdraw_unapplied_feedback_before_new_turn' if phase in ('failed','completed')
                                  else 'unapplied_feedback_remains_pending' if phase=='needs_confirmation'
                                  else 'pending_feedback_waits_for_sampling_boundary')
    if show_intent:
        result['intent']=copy.deepcopy(intent) if intent else None
    return result
