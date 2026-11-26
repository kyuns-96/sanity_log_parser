"""Explicit, idempotent resume of committed safe AI checkpoints."""
import re

from common.sanity_ai_retry import resumable
from common.sanity_submission import check
from oa.sanity_ai_ledger import Ledger
from oa.sanity_ai_parallel_recovery import (
    resume as parallel_resume,
    validate_resume as parallel_validate,
)
from oa.sanity_ai_recovery import (
    append_recovered_job_event,
    begin_job_recovery,
    validate_common_recovery_fields,
)
from oa.sanity_ai_store import RESUME_NAMESPACE as NAMESPACE, RESUME_PREFIX as PREFIX


def validate_resume(service, previous, status, data):
    """Validate the terminal-to-active ledger transition without reading input."""
    if previous['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_validate(service, previous, status, data)
    batch = 'batch' in previous['data']
    checkpoint = data.get('resume_checkpoint')
    ready = False
    if batch:
        check(isinstance(checkpoint, dict) and set(checkpoint) == {'key', 'sha256'}
              and isinstance(checkpoint['key'], str) and re.fullmatch(r'step\.\d{8}', checkpoint['key'])
              and isinstance(checkpoint['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', checkpoint['sha256']),
              'AI_JOB_CORRUPT')
        ledger = Ledger(service, previous)
        step, _ = ledger.get(checkpoint['key'], checkpoint)
        sequence = step.get('sequence')
        check(type(sequence) is int and sequence > 1 and checkpoint['key'] == f'step.{sequence:08d}'
              and step.get('resume') == {'request_id': data.get('resume_request_id'),
                                       'actor_id': previous['data']['command']['user_id']}, 'AI_JOB_CORRUPT')
        before, before_ref = ledger.get(f'step.{sequence - 1:08d}')
        check(isinstance(before, dict) and isinstance(before.get('state'), dict)
              and before.get('status') == 'READY' and step.get('previous_sha256') == before_ref['sha256']
              and step.get('state') == {**before['state'], 'child_retries': 0, 'retry_after': None}, 'AI_JOB_CORRUPT')
        ready = step.get('status') == 'READY'
    else:
        check(checkpoint is None, 'AI_JOB_CORRUPT')
    check(resumable(previous, checkpoint_ready=ready), 'AI_JOB_CORRUPT')
    validate_common_recovery_fields(
        previous, status, data,
        expected_status='RUNNING' if batch else 'PENDING',
        forbidden_keys=('error', 'finished_at'),
    )


def resume(service, user_id, job_id, request_id):
    job_id, request_id, event, meta, already_done = begin_job_recovery(service, user_id, job_id, request_id)
    if already_done:
        return service.public(event)
    if event['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
        return parallel_resume(service, user_id, job_id, request_id, _started=(event, meta))
    batch = 'batch' in event['data']
    step = ref = ledger = checkpoint = None
    if batch:
        ledger = Ledger(service, event)
        step, ref = ledger.last()
    check(resumable(event, checkpoint_ready=bool(step and step['status'] == 'READY')), 'AI_RESUME_UNAVAILABLE')
    service._check_credential_pin(event)
    if batch:
        # Reset this child's per-cycle retries only. Global failed_calls and all
        # completed result references remain bound to the original job.
        _, checkpoint = ledger.step(step, ref, 'READY', {**step['state'], 'child_retries': 0, 'retry_after': None},
                                    resume={'request_id': request_id, 'actor_id': user_id})
    return append_recovered_job_event(
        service,
        event,
        user_id=user_id,
        request_id=request_id,
        batch=batch,
        checkpoint=checkpoint,
        marker_key=PREFIX + request_id,
        marker_payload={'namespace': NAMESPACE, 'id': request_id, 'job_id': job_id, 'actor_id': user_id},
    )
