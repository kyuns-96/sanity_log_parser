"""Explicit user acceptance of duplicate execution for an uncertain batch call."""
import re

from common.sanity_ai_retry import MAX_RESUMES
from common.sanity_submission import check
from oa.sanity_ai_ledger import Ledger, attempts
from oa.sanity_ai_parallel_recovery import (
    retry_unknown as parallel_retry,
    unknown_availability as parallel_availability,
    validate_recovery as parallel_validate,
)
from oa.sanity_ai_recovery import (
    append_recovered_job_event,
    begin_job_recovery,
    evaluate_unknown_availability,
    validate_common_recovery_fields,
    verify_submission_snapshot,
)
from oa.sanity_ai_store import UNKNOWN_NAMESPACE as NAMESPACE, UNKNOWN_PREFIX as PREFIX


def recovery_info(event, step, ref):
    data = event['data']
    if (event['status'] != 'UNKNOWN' or not data.get('fail_safe') or 'batch' not in data
            or data.get('resume_count', 0) >= MAX_RESUMES or not isinstance(step, dict)
            or step.get('status') != 'RESERVED' or not isinstance(ref, dict)):
        return None
    state = step.get('state')
    if not isinstance(state, dict): return None
    if not (isinstance(step.get('call_token'), str) and re.fullmatch(r'[0-9a-f-]{36}', step['call_token'])
            and type(step.get('input_bytes')) is int and 0 < step['input_bytes'] <= 67108864
            and all(type(state.get(k)) is int and state[k] >= 0 for k in ('round', 'index', 'round_total'))
            and state['index'] < state['round_total']): return None
    phase = state.get('phase', 'CLASSIFY' if state['round'] == 0 else 'MERGE')
    if phase not in ('TEXT', 'TEXT_MERGE', 'CLASSIFY', 'MERGE', 'REVIEW', 'REVIEW_MERGE'): return None
    return {'event_sha256': event['sha256'], 'step_sha256': ref['sha256'], 'call_token': step['call_token'],
            'phase': phase, 'round': state['round'], 'index': state['index'], 'total': state['round_total'],
            'input_bytes': step['input_bytes']}


def unknown_availability(service, event, user_id, meta, job):
    """Display-only lightweight preflight; dispatch still revalidates inputs."""
    if event['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_availability(service, event, user_id, meta, job)

    def _verify_budget(_current):
        step, ref = Ledger(service, event).last()
        info = recovery_info(event, step, ref)
        check(info is not None and info == job.get('unknown_recovery'), 'AI_RESUME_UNAVAILABLE')
        advisor = service._job_advisor(event)
        check(advisor.describe()['config_sha256'] == event['data']['pins']['config_sha256'], 'AI_CONFIG_CHANGED')
        check(attempts(step['state']) + 1 < advisor.settings.batch.max_calls, 'AI_BATCH_LIMIT')

    return evaluate_unknown_availability(service, event, user_id, meta, _verify_budget)


def validate_recovery(service, previous, status, data):
    if previous['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_validate(service, previous, status, data)
    check(previous['status'] == 'UNKNOWN' and 'batch' in previous['data']
          and previous['data'].get('fail_safe') is True, 'AI_JOB_CORRUPT')
    validate_common_recovery_fields(
        previous, status, data,
        expected_status='RUNNING',
        forbidden_keys=('error', 'finished_at', 'retry_after', 'retry_failure', 'unknown_reason'),
    )
    checkpoint = data.get('resume_checkpoint')
    check(isinstance(checkpoint, dict) and set(checkpoint) == {'key', 'sha256'}
          and isinstance(checkpoint['key'], str) and re.fullmatch(r'step\.\d{8}', checkpoint['key'])
          and isinstance(checkpoint['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', checkpoint['sha256']), 'AI_JOB_CORRUPT')
    ledger = Ledger(service, previous); after, _ = ledger.get(checkpoint['key'], checkpoint)
    sequence = after.get('sequence')
    check(type(sequence) is int and sequence > 1 and checkpoint['key'] == f'step.{sequence:08d}', 'AI_JOB_CORRUPT')
    before, ref = ledger.get(f'step.{sequence - 1:08d}')
    check(recovery_info(previous, before, ref) is not None, 'AI_JOB_CORRUPT')
    check(isinstance(before, dict) and before.get('status') == 'RESERVED' and isinstance(before.get('state'), dict)
          and after.get('status') == 'READY' and after.get('previous_sha256') == ref['sha256']
          and after.get('state') == {**before['state'], 'unknown_calls': before['state'].get('unknown_calls', 0) + 1,
                                  'child_retries': 0, 'retry_after': None}
          and after.get('unknown_resolution') == {'actor_id': data['resumed_by'], 'request_id': data['resume_request_id'],
              'event_sha256': previous['sha256'], 'step_sha256': ref['sha256'], 'call_token': before.get('call_token'),
              'input_sha256': before.get('input_sha256'), 'acknowledge_duplicate': True}, 'AI_JOB_CORRUPT')
    attempts(before['state'])


def retry_unknown(service, user_id, job_id, request_id, expected_event_sha256, expected_step_sha256, acknowledge_duplicate):
    check(acknowledge_duplicate is True and all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v)
          for v in (expected_event_sha256, expected_step_sha256)), 'INVALID_REQUEST')
    job_id, request_id, event, meta, already_done = begin_job_recovery(
        service, user_id, job_id, request_id,
        unknown=True, expected_event=expected_event_sha256, expected_step=expected_step_sha256,
    )
    if already_done:
        return service.public(event)
    if event['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_retry(
            service, user_id, job_id, request_id,
            expected_event_sha256, expected_step_sha256, acknowledge_duplicate,
            _started=(event, meta),
        )
    check(event['status'] == 'UNKNOWN' and 'batch' in event['data'], 'AI_RESUME_UNAVAILABLE')
    ledger = Ledger(service, event); step, ref = ledger.last()
    info = recovery_info(event, step, ref)
    check(info is not None, 'AI_RESUME_UNAVAILABLE')
    check(event['sha256'] == expected_event_sha256 and ref['sha256'] == expected_step_sha256, 'REVISION_CONFLICT')
    verify_submission_snapshot(service, job_id, meta['snapshot'])
    service._check_credential_pin(event)
    advisor = service._job_advisor(event)
    check(advisor.describe()['config_sha256'] == event['data']['pins']['config_sha256'], 'AI_CONFIG_CHANGED')
    check(attempts(step['state']) + 1 < advisor.settings.batch.max_calls, 'AI_BATCH_LIMIT')
    state = {**step['state'], 'unknown_calls': step['state'].get('unknown_calls', 0) + 1, 'child_retries': 0, 'retry_after': None}
    resolution = {'actor_id': user_id, 'request_id': request_id, 'event_sha256': event['sha256'],
        'step_sha256': ref['sha256'], 'call_token': step['call_token'], 'input_sha256': step['input_sha256'],
        'acknowledge_duplicate': True}
    _, checkpoint = ledger.step(step, ref, 'READY', state, unknown_resolution=resolution)
    return append_recovered_job_event(
        service,
        event,
        user_id=user_id,
        request_id=request_id,
        batch=True,
        checkpoint=checkpoint,
        marker_key=PREFIX + request_id,
        marker_payload={
            'namespace': NAMESPACE, 'id': request_id, 'job_id': job_id, 'actor_id': user_id,
            'event_sha256': expected_event_sha256, 'step_sha256': expected_step_sha256,
        },
    )
