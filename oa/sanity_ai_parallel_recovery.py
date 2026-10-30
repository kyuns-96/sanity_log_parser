"""Explicit recovery of durable parallel-batch checkpoints.

Only a drained wave can be resumed. In particular, UNKNOWN children require
one explicit acknowledgment covering the exact immutable set of reservations.
No source records or model requests are hydrated by the display preflight.
"""
from datetime import timedelta
import re

from common.sanity_common import ReviewError, digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_submission import check
from oa.sanity_ai_batch_jobs import attempts
from oa.sanity_ai_retry import MAX_RESUMES, resumable, valid_receipt


def _ledger(service, event):
    from oa.sanity_ai_parallel_ledger import ParallelLedger
    return ParallelLedger(service, event)


def _wave(state):
    check(isinstance(state, dict), 'AI_BATCH_CORRUPT')
    wave = state.get('wave', [])
    check(isinstance(wave, list) and len(wave) <= 8, 'AI_BATCH_CORRUPT')
    check(all(isinstance(slot, dict) and slot.get('status') in
              ('RESERVED', 'DONE', 'RETRY', 'FAILED', 'UNKNOWN') and
              type(slot.get('index')) is int and slot['index'] >= 0 for slot in wave), 'AI_BATCH_CORRUPT')
    check(len({slot['index'] for slot in wave}) == len(wave), 'AI_BATCH_CORRUPT')
    return wave


def can_resume(event, state):
    wave = _wave(state)
    return (not any(slot['status'] in ('RESERVED', 'UNKNOWN') for slot in wave)
            and not any(slot['status'] == 'FAILED' and
                        (not valid_receipt(slot.get('receipt')) or
                         slot.get('error') not in (slot['receipt']['error'], 'AI_BATCH_LIMIT')) for slot in wave)
            and resumable(event, checkpoint_ready=True))


def _info(event, step, ref):
    if (event['status'] != 'UNKNOWN' or not event['data'].get('fail_safe')
            or event['data'].get('resume_count', 0) >= MAX_RESUMES
            or not isinstance(step, dict) or step.get('status') != 'READY'
            or not isinstance(ref, dict)):
        return None
    state = step.get('state')
    wave = _wave(state)
    unknown = [slot for slot in wave if slot['status'] == 'UNKNOWN']
    if not unknown or any(slot['status'] == 'RESERVED' for slot in wave):
        return None
    if any(slot['status'] == 'FAILED' and
           (not valid_receipt(slot.get('receipt')) or slot.get('error') not in (slot['receipt']['error'], 'AI_BATCH_LIMIT'))
           for slot in wave):
        return None
    phase = state.get('phase', 'CLASSIFY' if state.get('round', 0) == 0 else 'MERGE')
    check(phase in ('TEXT', 'TEXT_MERGE', 'CLASSIFY', 'MERGE', 'REVIEW', 'REVIEW_MERGE')
          and type(state.get('round')) is int and state['round'] >= 0
          and type(state.get('round_total')) is int and state['round_total'] > 0,
          'AI_BATCH_CORRUPT')
    details = []
    for slot in sorted(unknown, key=lambda item: item['index']):
        check(slot['index'] < state['round_total'] and isinstance(slot.get('call_token'), str)
              and re.fullmatch(r'[0-9a-f-]{36}', slot['call_token'])
              and type(slot.get('input_bytes')) is int and 0 < slot['input_bytes'] <= 67108864
              and isinstance(slot.get('input_sha256'), str)
              and re.fullmatch(r'[0-9a-f]{64}', slot['input_sha256'])
              and isinstance(slot.get('reservation'), dict)
              and set(slot['reservation']) == {'key','sha256'}
              and isinstance(slot['reservation']['key'], str)
              and isinstance(slot['reservation']['sha256'], str)
              and re.fullmatch(r'[0-9a-f]{64}', slot['reservation']['sha256']), 'AI_BATCH_CORRUPT')
        details.append({'phase':phase, 'round':state['round'], 'index':slot['index'],
                        'total':state['round_total'], 'input_bytes':slot['input_bytes'],
                        'call_token':slot['call_token'], 'event_sha256':event['sha256'],
                        'step_sha256':slot['reservation']['sha256']})
    check(len({item['call_token'] for item in details}) == len(details)
          and len({item['step_sha256'] for item in details}) == len(details), 'AI_BATCH_CORRUPT')
    primary = {**details[0], 'event_sha256':event['sha256'], 'step_sha256':ref['sha256']}
    return primary, details


def recovery_info(service, event):
    step, ref = _ledger(service, event).last()
    return _info(event, step, ref)


def _budget(service, event, state, count):
    advisor = service._job_advisor(event)
    check(advisor.describe()['config_sha256'] == event['data']['pins']['config_sha256'], 'AI_CONFIG_CHANGED')
    check(attempts(state) + count <= advisor.settings.batch.max_calls, 'AI_BATCH_LIMIT')


def unknown_availability(service, event, user_id, meta, job):
    from oa.sanity_ai_unknown_recovery import AVAILABILITY_ERRORS
    from oa.sanity_ai_submissions import Submissions, header
    if event['status'] != 'UNKNOWN':
        return False, None
    if event['data']['command']['user_id'] != user_id or not meta['can_review']:
        return False, 'FORBIDDEN'
    try:
        current = header(service, {**event['data']['command'], 'user_id':user_id})
        check(current['can_review'], 'FORBIDDEN')
        check(current['ready'] and current['group_ready'], 'AI_INPUT_STALE')
        ledger = _ledger(service, event)
        step, ref = ledger.last()
        info = _info(event, step, ref)
        check(info is not None and info[0] == job.get('unknown_recovery')
              and info[1] == job.get('unknown_recoveries'), 'AI_RESUME_UNAVAILABLE')
        intent = Submissions(service).load(event['job_id'], required=False)
        if intent:
            check(intent['data']['snapshot'] == current['snapshot'], 'AI_INPUT_STALE')
        service._check_credential_pin(event)
        _budget(service, event, step['state'], sum(slot['status'] != 'DONE' for slot in _wave(step['state'])))
        return True, None
    except ReviewError as error:
        if error.code not in AVAILABILITY_ERRORS:
            raise
        return False, error.code


def _reset(state, statuses):
    result = {**state, 'wave':[]}
    for slot in _wave(state):
        if slot['status'] in statuses:
            slot = {**slot, 'status':'RETRY', 'retries':0, 'retry_after':None}
            slot.pop('receipt', None)
            slot.pop('validation_failure', None)
        result['wave'].append(slot)
    result['cancel_requested'] = False
    result.pop('cancelled_by', None)
    return result


def _common_transition(previous, status, data):
    check(status == 'RUNNING' and type(data.get('resume_count')) is int
          and data['resume_count'] == previous['data'].get('resume_count', 0) + 1
          and data['resume_count'] <= MAX_RESUMES
          and data.get('resumed_by') == previous['data']['command']['user_id']
          and isinstance(data.get('resume_request_id'), str)
          and str(uuid_value(data['resume_request_id'])) == data['resume_request_id']
          and data.get('retry_count', 0) == 0
          and all(k not in data for k in ('error','finished_at','cancelled_by','retry_after','retry_failure','unknown_reason')),
          'AI_JOB_CORRUPT')


def _before_after(service, previous, data):
    checkpoint = data.get('resume_checkpoint')
    check(isinstance(checkpoint, dict) and set(checkpoint) == {'key','sha256'}
          and isinstance(checkpoint['key'], str) and re.fullmatch(r'step\.\d{8}', checkpoint['key'])
          and isinstance(checkpoint['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', checkpoint['sha256']),
          'AI_JOB_CORRUPT')
    ledger = _ledger(service, previous)
    after, _ = ledger.get(checkpoint['key'], checkpoint)
    check(isinstance(after,dict), 'AI_JOB_CORRUPT')
    sequence = after.get('sequence')
    check(type(sequence) is int and sequence > 1 and checkpoint['key'] == f'step.{sequence:08d}', 'AI_JOB_CORRUPT')
    before, ref = ledger.get(f'step.{sequence-1:08d}')
    check(isinstance(before,dict) and before.get('status') == 'READY' and after.get('status') == 'READY'
          and after.get('previous_sha256') == ref['sha256'], 'AI_JOB_CORRUPT')
    # Revalidate the historical reservations at this transition, even after
    # later waves have changed what ParallelLedger.last() returns.
    state = before.get('state')
    for slot in _wave(state):
        reservation = slot.get('reservation')
        if reservation is None:
            check(slot['status'] in ('RETRY','DONE') and slot.get('attempt', 0) == 0, 'AI_JOB_CORRUPT')
            continue
        check(isinstance(reservation,dict) and set(reservation) == {'key','sha256'}
              and isinstance(reservation['key'],str) and isinstance(reservation['sha256'],str), 'AI_JOB_CORRUPT')
        call, call_ref = ledger.get(reservation['key'],reservation)
        check(isinstance(call,dict) and call.get('kind') == 'RESERVATION' and call.get('call_token') == slot.get('call_token')
              and call.get('round') == state.get('round') and call.get('index') == slot['index']
              and call.get('attempt') == slot.get('attempt') and call.get('input_sha256') == slot.get('input_sha256')
              and call.get('input_bytes') == slot.get('input_bytes') and call.get('deadline') == slot.get('deadline'),
              'AI_JOB_CORRUPT')
        if slot['status'] != 'RESERVED':
            outcome, _ = ledger.get(call_ref['key'] + '.outcome')
            check(isinstance(outcome,dict) and outcome.get('kind') == 'OUTCOME' and outcome.get('reservation') == call_ref
                  and outcome.get('call_token') == call.get('call_token'), 'AI_JOB_CORRUPT')
            if slot['status'] == 'UNKNOWN':
                check(outcome.get('status') == 'UNKNOWN', 'AI_JOB_CORRUPT')
            if slot['status'] == 'DONE':
                check(outcome.get('status') == 'SUCCESS' and outcome.get('result') == slot.get('result'), 'AI_JOB_CORRUPT')
    return before, ref, after


def validate_resume(service, previous, status, data):
    _common_transition(previous, status, data)
    before, _, after = _before_after(service, previous, data)
    check(can_resume(previous, before['state'])
          and after.get('resume') == {'request_id':data['resume_request_id'], 'actor_id':data['resumed_by']}
          and after.get('state') == _reset(before['state'], {'FAILED','RETRY'}), 'AI_JOB_CORRUPT')


def _resolution(previous, ref, state, user_id, request_id):
    return {'actor_id':user_id, 'request_id':request_id, 'event_sha256':previous['sha256'],
            'step_sha256':ref['sha256'], 'call_tokens':sorted(slot['call_token'] for slot in _wave(state)
                                                                if slot['status'] == 'UNKNOWN'),
            'acknowledge_duplicate':True}


def validate_recovery(service, previous, status, data):
    _common_transition(previous, status, data)
    before, ref, after = _before_after(service, previous, data)
    check(_info(previous, before, ref) is not None
          and after.get('unknown_resolution') == _resolution(previous, ref, before['state'],
                                                             data['resumed_by'], data['resume_request_id'])
          and after.get('state') == _reset(before['state'], {'UNKNOWN','FAILED','RETRY'}), 'AI_JOB_CORRUPT')


def _start(service, user_id, job_id, request_id, *, unknown=False, expected_event=None, expected_step=None):
    from oa.sanity_ai_jobs import ACTIVE
    from oa.sanity_ai_resume import PREFIX as RESUME_PREFIX, marker as resume_marker
    from oa.sanity_ai_unknown_recovery import marker as unknown_marker
    from oa.sanity_ai_submissions import Submissions, header, job_lock, PREFIX as INTENT_PREFIX, ACTIVE as INTENT_ACTIVE
    job_id, request_id = str(uuid_value(job_id, 'job_id')), str(uuid_value(request_id, 'request_id'))
    service.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(RESUME_PREFIX+request_id),))
    job_lock(service, job_id)
    event = service._load(job_id)
    command = event['data']['command']
    check(command['user_id'] == user_id, 'FORBIDDEN')
    meta = header(service, {**command,'user_id':user_id}, write=True)
    old = unknown_marker(service,request_id) if unknown else resume_marker(service,request_id)
    if old:
        check(old['job_id'] == job_id and old['actor_id'] == user_id
              and (not unknown or old['event_sha256'] == expected_event and old['step_sha256'] == expected_step),
              'REQUEST_CONFLICT')
        return event, meta, True
    check((resume_marker(service,request_id) if unknown else unknown_marker(service,request_id)) is None, 'REQUEST_CONFLICT')
    check(meta['ready'] and meta['can_review'] and meta['group_ready'], 'AI_INPUT_STALE')
    target = event['data']['target']
    service.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(INTENT_PREFIX+target),))
    latest, intent = service._latest(target=target), Submissions(service).latest(target=target)
    check(not latest or latest['job_id'] == job_id or latest['status'] not in ACTIVE, 'AI_JOB_ACTIVE')
    check(not intent or intent['id'] == job_id or intent['status'] not in INTENT_ACTIVE, 'AI_JOB_ACTIVE')
    return event, meta, False


def _append(service, event, user_id, request_id, checkpoint):
    from oa.sanity_ai_jobs import _db_now, _timestamp
    data = {k:v for k,v in event['data'].items() if k not in
            ('finished_at','cancelled_by','error','retry_after','retry_failure','resume_checkpoint','unknown_reason','validation_failure')}
    data.update(resume_count=event['data'].get('resume_count',0)+1, resume_request_id=request_id,
                resumed_by=user_id, retry_count=0, resume_checkpoint=checkpoint)
    now = _db_now(service.db)
    duration = _timestamp(event['data']['deadline']) - _timestamp(event['data']['started_at'])
    check(timedelta(seconds=60) <= duration <= timedelta(seconds=604800), 'AI_JOB_CORRUPT')
    data.update(started_at=now.isoformat(), deadline=(now+duration).isoformat())
    return service._append(event, 'RUNNING', data, now=now)


def resume(service, user_id, job_id, request_id):
    from oa.sanity_ai_resume import PREFIX, NAMESPACE
    from oa.sanity_ai_submissions import Submissions
    job_id, request_id = str(uuid_value(job_id, 'job_id')), str(uuid_value(request_id, 'request_id'))
    event, meta, old = _start(service,user_id,job_id,request_id)
    if old:
        return service.public(event)
    ledger = _ledger(service,event)
    step, ref = ledger.last()
    check(step is not None and step.get('status') == 'READY' and can_resume(event,step['state']), 'AI_RESUME_UNAVAILABLE')
    ledger.audit(allow_unresolved=True)
    intent = Submissions(service).load(job_id,required=False)
    if intent:
        check(intent['data']['snapshot'] == meta['snapshot'], 'AI_INPUT_STALE')
    service._check_credential_pin(event)
    _budget(service,event,step['state'],sum(slot['status'] != 'DONE' for slot in _wave(step['state'])))
    _, checkpoint = ledger.step(step,ref,'READY',_reset(step['state'], {'FAILED','RETRY'}),
                                resume={'request_id':request_id,'actor_id':user_id})
    result = _append(service,event,user_id,request_id,checkpoint)
    value = {'namespace':NAMESPACE,'id':request_id,'job_id':job_id,'actor_id':user_id}
    value['sha256'] = digest(value)
    service.ws.put('WS_CONFIG', PREFIX+request_id, value)
    return service.public(result)


def retry_unknown(service, user_id, job_id, request_id, expected_event_sha256, expected_step_sha256, acknowledge_duplicate):
    from oa.sanity_ai_unknown_recovery import PREFIX, NAMESPACE
    from oa.sanity_ai_submissions import Submissions
    check(acknowledge_duplicate is True and all(isinstance(v,str) and re.fullmatch(r'[0-9a-f]{64}',v)
          for v in (expected_event_sha256,expected_step_sha256)), 'INVALID_REQUEST')
    job_id, request_id = str(uuid_value(job_id, 'job_id')), str(uuid_value(request_id, 'request_id'))
    event, meta, old = _start(service,user_id,job_id,request_id,unknown=True,
                              expected_event=expected_event_sha256,expected_step=expected_step_sha256)
    if old:
        return service.public(event)
    ledger = _ledger(service,event)
    step, ref = ledger.last()
    info = _info(event,step,ref)
    check(info is not None, 'AI_RESUME_UNAVAILABLE')
    check(event['sha256'] == expected_event_sha256 and ref['sha256'] == expected_step_sha256, 'REVISION_CONFLICT')
    ledger.audit(allow_unresolved=True)
    intent = Submissions(service).load(job_id,required=False)
    if intent:
        check(intent['data']['snapshot'] == meta['snapshot'], 'AI_INPUT_STALE')
    service._check_credential_pin(event)
    _budget(service,event,step['state'],sum(slot['status'] != 'DONE' for slot in _wave(step['state'])))
    resolution = _resolution(event,ref,step['state'],user_id,request_id)
    _, checkpoint = ledger.step(step,ref,'READY',_reset(step['state'], {'UNKNOWN','FAILED','RETRY'}),unknown_resolution=resolution)
    result = _append(service,event,user_id,request_id,checkpoint)
    value = {'namespace':NAMESPACE,'id':request_id,'job_id':job_id,'actor_id':user_id,
             'event_sha256':expected_event_sha256,'step_sha256':expected_step_sha256}
    value['sha256'] = digest(value)
    service.ws.put('WS_CONFIG', PREFIX+request_id, value)
    return service.public(result)
