from oa import sanity_ai_store as _ai_store
"""Refill a durable, bounded window after each completed HTTP call."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from common.sanity_common import ReviewError
from oa.sanity_ai_ledger import attempts, finish_round
from oa.sanity_ai_parallel_jobs import (
    STALE, execute_call, expire_wave, prepare_parallel, reserve_wave_slot, save_outcome, settle_wave,
)
from oa.sanity_ai_parallel_ledger import ParallelLedger
from oa.sanity_ai_scheduler import allocation
from oa.sanity_ai_store import error_code, parse_timestamp, worker_load


def _advance(open_service, job_id, owned, model=None, revision=None, *, now=None):
    dispatch = []
    with open_service() as service:
        service._lock(job_id)
        event = worker_load(service, job_id)
        if event is None:
            return None, dispatch, model, revision
        if event['status'] == 'RESULT_READY':
            return service.public(service._apply(event)), dispatch, model, revision
        if event['status'] not in ('PENDING', 'RUNNING'):
            return service.public(event), dispatch, model, revision
        try:
            if event['status'] == 'RUNNING':
                ledger = ParallelLedger(service, event)
                event = expire_wave(service, event, ledger, now or _ai_store.db_now(service.db))
                if event['status'] != 'RUNNING':
                    return service.public(event), dispatch, model, revision
                state = ledger.last()[0]['state']
                # Another coordinator may own the remaining reservations. Never
                # refill its window or take a connection across network I/O.
                if any(slot['status'] == 'RESERVED' and slot['call_token'] not in owned for slot in state['wave']):
                    return service.public(event), dispatch, model, revision
                if state.get('cancel_requested') or state.get('dispatch_error') or any(
                        slot['status'] in ('UNKNOWN', 'FAILED') for slot in state['wave']):
                    return service.public(settle_wave(service, event, ledger)), dispatch, model, revision
            if model is None:
                event, ledger, model, revision = prepare_parallel(service, event, now)
            else:
                # Keep the prepared model, but revalidate the complete input,
                # authorization, configuration and personal credential pins.
                service._inputs(event)
                ledger = ParallelLedger(service, event)
            step, ref = ledger.last()
            state = step['state']
            current = now or _ai_store.db_now(service.db)
            if parse_timestamp(event['data']['deadline']) <= current:
                raise ReviewError('AI_BATCH_DEADLINE', 'Overall batch deadline exceeded')
            done = sum(slot['status'] == 'DONE' for slot in state['wave'])
            if done:
                state = {**state, 'index': state['index'] + done,
                         'wave': [slot for slot in state['wave'] if slot['status'] != 'DONE']}
                step, ref = ledger.step(step, ref, 'READY', state)
            manifest = ledger.round(state['round'])
            if state['index'] == manifest['count']:
                event = finish_round(service, event, ledger, model, step, ref)
                return service.public(event), dispatch, model, revision
            # Backoff pauses new requests for this job as well as retries. In
            # particular, a 429 must not be bypassed with fresh descriptors.
            if any(slot['status'] == 'RETRY' and slot.get('retry_after') and
                   parse_timestamp(slot['retry_after']) > current for slot in state['wave']):
                return service.public(event), dispatch, model, revision
            with service.db.transaction():
                width = event['data']['batch']['max_concurrency']
                share = allocation(service, job_id)
                if share is not None:
                    width = min(width, share)
                    state = {**state, 'allocated_concurrency': width}
                while True:
                    old = next((slot for slot in state['wave'] if slot['status'] == 'RETRY'), None)
                    if old is None:
                        if len(state['wave']) >= width or state['next_index'] >= manifest['count']:
                            break
                    reserved = sum(slot['status'] == 'RESERVED' for slot in state['wave'])
                    if attempts(state) + reserved >= model.settings.max_calls:
                        state = {**state, 'dispatch_error': 'AI_BATCH_LIMIT'}
                        break
                    if old is None:
                        old = {'index': state['next_index'], 'status': 'RETRY', 'attempt': 0, 'retries': 0}
                        state = {**state, 'next_index': state['next_index'] + 1, 'wave': [*state['wave'], old]}
                    state, item, reserved_ok = reserve_wave_slot(
                        service, job_id, event, ledger, model, state, old, revision,
                        current=current, now=now, rolling=True,
                    )
                    if not reserved_ok:
                        break
                    if item is not None:
                        dispatch.append(item)
                ledger.step(step, ref, 'READY', state)
            return service.public(settle_wave(service, event, ledger)), dispatch, model, revision
        except ReviewError as error:
            # A failed preparation rolls back every reservation prepared in its
            # savepoint. Drain existing calls before publishing the failure.
            dispatch = []
            if event['status'] == 'RUNNING' and error.code != 'AI_BATCH_CORRUPT':
                ledger = ParallelLedger(service, event)
                step, ref = ledger.last()
                ledger.step(step, ref, 'READY', {**step['state'], 'dispatch_error': error_code(error)})
                event = settle_wave(service, event, ledger)
            else:
                event = service._failed(event, error, status='STALE' if error.code in STALE else 'FAILED')
            return service.public(event), dispatch, model, revision


def run_step(open_service, job_id, *, now=None):
    result, dispatch, model, revision = _advance(open_service, job_id, set(), now=now)
    if not dispatch:
        return result
    with ThreadPoolExecutor(max_workers=result['progress']['concurrency'], thread_name_prefix='ai-batch') as executor:
        futures = {}
        while True:
            for ctx, child, descriptor, nodes, prepared, slot, credential_revision in dispatch:
                future = executor.submit(ctx.run, execute_call, child, descriptor, nodes, prepared)
                futures[future] = (slot, descriptor, credential_revision)
            if not futures:
                return result
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            # Settle all outcomes already available before deciding to refill.
            while completed:
                for future in completed:
                    slot, descriptor, credential_revision = futures.pop(future)
                    value, error, seconds = future.result()
                    result = save_outcome(open_service, job_id, slot, descriptor, credential_revision, value, error, seconds, model.settings.max_calls)
                completed = {future for future in futures if future.done()}
            owned = {slot['call_token'] for slot, _, _ in futures.values()}
            result, dispatch, model, revision = _advance(open_service, job_id, owned, model, revision, now=now)
