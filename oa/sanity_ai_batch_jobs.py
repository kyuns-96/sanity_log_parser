from oa import sanity_ai_store as _ai_store
"""Durable sequential batch execution, public progress projection and result loading."""
from datetime import timedelta
import hashlib
import time
from uuid import uuid4

from common.sanity_ai_retry import (
    MAX_RETRIES, receipt as failure_receipt, resumable, retry_at, valid_receipt, validation_failure,
)
from common.sanity_common import ReviewError, digest
from common.sanity_preserved_ai import available
from common.sanity_submission import check
from common.sanity_validation_diagnostics import sanitize_validation_failure
from oa.sanity_ai_ledger import (
    Ledger, MAX_BYTES, NAMESPACE, attempts, bind_preserved, build_batch_model,
    finish_round, record_plan,
)
from oa.sanity_ai_live import observer as live_observer
from oa.sanity_ai_parallel_jobs import public as parallel_public
from oa.sanity_ai_parallel_ledger import ParallelLedger
from oa.sanity_ai_progress import display_metrics, group_count_after
from oa.sanity_ai_scheduler import release, reserve
from oa.sanity_ai_store import error_code, parse_timestamp, public_event, worker_load
from oa.sanity_ai_unknown_recovery import recovery_info


def public(service, event):
    if event['data']['batch'].get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_public(service, event)
    value = public_event(event)
    try: step, step_ref = Ledger(service, event).last()
    except ReviewError:
        if event['status'] in ('FAILED', 'STALE', 'UNKNOWN', 'CANCELLED'):
            value['model_called'] = None
            return value
        raise
    state = step['state'] if step else {}
    value['progress'] = {'mode': 'BATCH', 'phase': state.get('phase', 'CLASSIFY' if state.get('round', 0) == 0 else 'MERGE'),
        'completed_records': state.get('records', 0), 'total_records': event['data']['batch'].get('total_records', 0),
        'completed_calls': state.get('calls', 0), 'round': state.get('round', 0),
        'waiting': bool(step and (step['status'] == 'RESERVED' or state.get('retry_after'))),
        'round_completed': state.get('index', 0), 'round_total': state.get('round_total', 0),
        'average_call_seconds': state.get('seconds', 0) / state['calls'] if state.get('calls') else None,
        'seconds_per_record': state.get('record_seconds', 0) / state['records'] if state.get('records') else None,
        'stage_eta_seconds': max(0, state.get('round_total', 0) - state.get('index', 0)) * state['round_seconds'] / state['round_samples']
            if state.get('round_samples', 0) >= 2 and state.get('round_total') else None,
        'retry_count': state.get('failed_calls', 0), 'child_retries': state.get('child_retries', 0),
        'retry_limit': MAX_RETRIES, 'retry_after': state.get('retry_after')}
    value['progress']['unknown_calls'] = state.get('unknown_calls', 0)
    recovery = recovery_info(event, step, step_ref)
    if recovery is not None: value['unknown_recovery'] = recovery
    value.update(display_metrics(event, state, phase=value['progress']['phase']))
    proof = (step.get('failure', {}).get('receipt', {}) if step else {})
    diagnostic = step.get('failure', {}).get('validation_failure') if step else None
    if diagnostic is not None and (event['status'] == 'RUNNING' or
                                  event['status'] == 'FAILED' and event['data'].get('error') == 'INVALID_RATIONALE'):
        check(proof.get('error') == 'INVALID_RATIONALE' and sanitize_validation_failure(diagnostic) == diagnostic, 'AI_BATCH_CORRUPT')
        value['validation_failure'] = diagnostic
    if state.get('retry_after') and valid_receipt(proof):
        value['progress']['retry_error'] = proof['error']
        if 'http_status' in proof: value['progress']['retry_http_status'] = proof['http_status']
    value['resumable'] = resumable(event, checkpoint_ready=bool(step and step['status'] == 'READY'))
    if state.get('calls', 0): value['model_called'] = True; value['model_attempted'] = True
    elif step and step['status'] == 'RESERVED':
        # A terminal pre-send failure is known unsent even though its immutable
        # reservation remains. An active/abandoned reservation is still unknown.
        known_unsent = event['status'] == 'FAILED' and value['model_called'] is False
        if value['model_called'] is not True and not known_unsent: value['model_called'] = None
        value['model_attempted'] = True
    return value


def _prepare(service, event, now):
    """Inside a short authorization transaction. Never performs HTTP."""
    _, context, cases, criteria = service._inputs(event)
    advisor = service._job_advisor(event)
    revision = None
    if service._personal_tokens(event):
        token, revision = service._user_token(event['data']['command']['user_id'], event['data']['command'].get('connection_id'))
        advisor = advisor.with_user_token(token)
    model = build_batch_model(advisor, event, context, cases, criteria)
    ledger = Ledger(service, event)
    if event['status'] == 'PENDING':
        # Catch a fundamentally oversized record/criterion before paying for
        # text analysis. The final record plan also includes its actual summary.
        if getattr(model, 'segments', []): model.leaf_plan()
        text_plan = model.text_plan() if hasattr(model, 'text_plan') else []
        # Every text fragment is preflighted before the first HTTP call. Record
        # leaves are preflighted together after the actual text analysis fits.
        ranges = text_plan or record_plan(model)
        plan = ledger.plans(0, ranges, len(text_plan) or len(model.records))
        started = now or _ai_store.db_now(service.db)
        batch = {**event['data']['batch'], 'total_records': len(model.records), 'leaf_count': len(ranges),
               'plan': plan, 'credential_revision': revision}
        event = service._append(event, 'RUNNING', {**event['data'], 'batch': batch, 'call_token': str(uuid4()),
            'model_attempted': False, 'model_called': False, 'started_at': started.isoformat(),
            'deadline': (started + timedelta(seconds=model.settings.deadline_seconds)).isoformat()}, now=started)
        ledger = Ledger(service, event)
        state = {'round': 0, 'index': 0, 'records': 0, 'calls': 0, 'nodes': 0, 'stable': 0, 'actual_model': None,
               'round_total': len(ranges), 'round_seconds': 0, 'round_samples': 0, 'seconds': 0, 'record_seconds': 0}
        state['current_groups'] = 1 if getattr(model, 'review', False) else None if text_plan else 0
        if event['data']['batch']['version'] == 2:
            state.update(phase='TEXT' if text_plan else 'REVIEW' if model.review else 'CLASSIFY', stage_round=0)
        ledger.step(None, None, 'READY', state)
    bind_preserved(model, ledger, ledger.last()[0]['state'])
    return event, ledger, model, revision


def load_result(service, event, context, cases, criteria=None):
    if event['data']['batch'].get('execution') in ('parallel-v1', 'parallel-v2'):
        ledger = ParallelLedger(service, event)
    else:
        ledger = Ledger(service, event)
    ref = event['data']['result_ref']
    result = ledger.get(ref['key'], ref)[0]
    check(set(result) == {'nodes', 'metadata'} and result['metadata'].get('input_hash_kind') == 'BATCH_MANIFEST'
          and result['metadata']['batch_receipt']['root'] == ledger.audit()
          and result['metadata']['input_sha256'] == digest([event['data']['batch']['plan'], ledger.last()[1]['sha256']]), 'AI_BATCH_CORRUPT')
    model = build_batch_model(service._job_advisor(event), event, context, cases, criteria or [])
    bind_preserved(model, ledger, ledger.last()[0]['state'])
    advice, citations = model.advice(ledger.nodes(result['nodes']))
    check(digest(advice) == result['metadata']['output_sha256'], 'AI_BATCH_CORRUPT')
    if available(context): check(citations == result['metadata']['preserved_citations'], 'AI_BATCH_CORRUPT')
    return {'advice': advice, 'metadata': result['metadata']}


def run_step(open_service, job_id, *, now=None):
    dispatch = None
    with open_service() as service:
        service._lock(job_id); event = worker_load(service, job_id)
        if event is None: return None
        if event['status'] == 'RESULT_READY': return public(service, service._apply(event))
        if event['status'] not in ('PENDING', 'RUNNING'): return public(service, event)
        try:
            # A persisted reservation may already have reached the model.
            # Input or credential changes cannot prove that it did not happen.
            if event['status'] == 'RUNNING':
                reserved, _ = Ledger(service, event).last()
                if reserved and reserved['status'] == 'RESERVED':
                    current = now or _ai_store.db_now(service.db)
                    if parse_timestamp(reserved['deadline']) <= current or parse_timestamp(event['data']['deadline']) <= current:
                        error = ReviewError('AI_OUTCOME_UNKNOWN', 'Child call completion is unknown'); error.unknown_reason = 'RESERVED_EXPIRED'
                        return public(service, service._failed(event, error, status='UNKNOWN', called=None))
                    return public(service, event)
            event, ledger, model, revision = _prepare(service, event, now)
            step, ref = ledger.last(); state = step['state']; current = now or _ai_store.db_now(service.db)
            if step['status'] == 'RESERVED':
                if parse_timestamp(step['deadline']) <= current or parse_timestamp(event['data']['deadline']) <= current:
                    return public(service, service._failed(event, ReviewError('AI_OUTCOME_UNKNOWN', 'Child call completion is unknown'), status='UNKNOWN', called=None))
                return public(service, event)
            if parse_timestamp(event['data']['deadline']) <= current:
                return public(service, service._failed(event, ReviewError('AI_BATCH_DEADLINE', 'Overall batch deadline exceeded'), status='FAILED'))
            if state.get('retry_after') and parse_timestamp(state['retry_after']) > current: return public(service, event)
            manifest = ledger.round(state['round'])
            if state['index'] == manifest['count']:
                return public(service, finish_round(service, event, ledger, model, step, ref))
            check(state['index'] < manifest['count'], 'AI_BATCH_CORRUPT')
            descriptor = ledger.descriptor(state)
            nodes = ledger.nodes(descriptor['node_refs']) if 'node_refs' in descriptor else None
            if nodes is not None and len(nodes) == 1:
                result = ledger.put(f"result.{state['round']:03d}.{state['index']:06d}", {'nodes': nodes, 'metadata': None})
                ledger.step(step, ref, 'READY', {**state, 'index': state['index'] + 1, 'nodes': state['nodes'] + 1}, result=result)
                return public(service, event)
            check(attempts(state) < model.settings.max_calls, 'AI_BATCH_LIMIT')
            prepared_call = model.prepare(descriptor, nodes)
            request_bytes = prepared_call[1][0]
            state = {**state, 'retry_after': None}
            token = str(uuid4()); deadline = (current + timedelta(seconds=model.advisor.settings.timeout + 60)).isoformat()
            with service.db.transaction():
                if not reserve(service, event['job_id'], token, deadline): return public(service, event)
                reserved, reserved_ref = ledger.step(step, ref, 'RESERVED', state, descriptor_sha256=digest(descriptor),
                    input_sha256=hashlib.sha256(request_bytes).hexdigest(), input_bytes=len(request_bytes),
                    prompt_version=prepared_call[0][2], call_token=token, deadline=deadline)
            model.advisor = service._bind_advisor(model.advisor, event, reserved['call_token'])
            model.advisor = model.advisor.with_observer(live_observer(open_service, event, reserved['call_token']))
            dispatch = (event, reserved, reserved_ref, model, descriptor, nodes, revision, prepared_call)
        except ReviewError as error:
            stale = error.code in ('AI_INPUT_STALE', 'AI_CASES_STALE', 'AI_CRITERIA_STALE', 'REVISION_CONFLICT', 'AI_CONFIG_CHANGED', 'AI_CONNECTION_UNAVAILABLE', 'AI_CREDENTIAL_CHANGED', 'UNAUTHORIZED', 'FORBIDDEN')
            return public(service, service._failed(event, error, status='STALE' if stale else 'FAILED'))
    # No database connection or authorization lock survives model dispatch.
    event, reserved, reserved_ref, model, descriptor, nodes, revision, prepared_call = dispatch
    call_started = time.monotonic()
    try:
        result = model.execute(descriptor, nodes=nodes, prepared_call=prepared_call); failure = None
    except Exception as error:
        result = None; failure = error
    seconds = time.monotonic() - call_started
    with open_service() as service:
        service._lock(job_id)
        release(service, job_id, reserved['call_token'])
        current = worker_load(service, job_id)
        if current is None: return None
        ledger = Ledger(service, current)
        if current['status'] != 'RUNNING': return public(service, current)
        if current['sequence'] != event['sequence']: return public(service, current)
        step, ref = ledger.last()
        check(ref == reserved_ref and step['status'] == 'RESERVED', 'AI_BATCH_CORRUPT')
        if parse_timestamp(step['deadline']) <= _ai_store.db_now(service.db):
            error = ReviewError('AI_OUTCOME_UNKNOWN', 'Child deadline exceeded'); error.unknown_reason = 'RESPONSE_DEADLINE'
            return public(service, service._failed(current, error, status='UNKNOWN', called=True))
        if failure is not None:
            state = step['state']
            proof = failure_receipt(failure) if current['data'].get('fail_safe') else None
            if proof is not None:
                failed = state.get('failed_calls', 0) + 1
                can_retry = state.get('child_retries', 0) < MAX_RETRIES and attempts(state) + 1 < model.settings.max_calls
                retries = state.get('child_retries', 0) + (1 if can_retry else 0)
                diagnostic = validation_failure(failure)
                ledger.step(step, ref, 'READY', {**state, 'child_retries': retries, 'failed_calls': failed,
                    'retry_after': retry_at(_ai_store.db_now(service.db), retries, failure) if can_retry else None},
                    failure={'call_token': step['call_token'], 'input_sha256': step['input_sha256'], 'receipt': proof,
                             **({'validation_failure': diagnostic} if diagnostic is not None else {})})
                if can_retry: return public(service, current)
                current = {**current, 'data': {**current['data'], 'retry_failure': proof}}
                if attempts(state) + 1 >= model.settings.max_calls:
                    return public(service, service._failed(current, ReviewError('AI_BATCH_LIMIT', 'Call attempt budget exhausted'),
                                  status='FAILED', called=proof['model_called']))
            unknown = not isinstance(failure, ReviewError) or error_code(failure) == 'AI_OUTCOME_UNKNOWN'
            stale = error_code(failure) in ('AI_CONFIG_CHANGED', 'AI_CONNECTION_UNAVAILABLE', 'AI_CREDENTIAL_CHANGED', 'FORBIDDEN', 'UNAUTHORIZED')
            return public(service, service._failed(current, failure, status='UNKNOWN' if unknown else 'STALE' if stale else 'FAILED', called=getattr(failure, 'model_called', None)))
        try:
            service._inputs(current)
            if service._personal_tokens(current):
                check(service._user_token(current['data']['command']['user_id'], current['data']['command'].get('connection_id'))[1] == revision, 'AI_CREDENTIAL_CHANGED')
            state = step['state']; actual = result['metadata']['model']
            check(result['metadata']['input_sha256'] == step['input_sha256'], 'AI_BATCH_CORRUPT')
            check(state['actual_model'] is None or state['actual_model'] == actual, 'AI_MODEL_CHANGED')
            result_ref = ledger.put(f"result.{state['round']:03d}.{state['index']:06d}", result)
            records = state['records'] + (descriptor['stop'] - descriptor['start'] if descriptor['kind'] in ('LEAF', 'REVIEW_LEAF') else 0)
            ledger.step(step, ref, 'READY', {**state, 'index': state['index'] + 1, 'calls': state['calls'] + 1,
                        'current_groups': group_count_after(current, state, descriptor, len(result['nodes'])),
                        'records': records, 'nodes': state['nodes'] + len(result['nodes']), 'actual_model': actual,
                        'seconds': state.get('seconds', 0) + seconds, 'round_seconds': state.get('round_seconds', 0) + seconds,
                        'round_samples': state.get('round_samples', 0) + 1,
                        'record_seconds': state.get('record_seconds', 0) + (seconds if records > state['records'] else 0),
                        'child_retries': 0, 'retry_after': None}, result=result_ref)
            return public(service, current)
        except ReviewError as error:
            return public(service, service._failed(current, error, status='STALE', called=True))
