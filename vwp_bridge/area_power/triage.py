"""Central VWP execution of one instance triage run.

Reads the frozen comparison, selects candidates, calls the requester's pinned
project-comparison connection in batches and stores one judgement per instance. A missing
or failing model never becomes a success: affected instances stay
``needs_review`` and the run is marked failed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import monotonic

from common.sanity_ai_retry import receipt, retry_delay_seconds
from common.sanity_common import ReviewError
from .ai_runtime import runtime_for
from common.area_power.analysis import advisory_lock, existing_dedupe, observation_view
from common.area_power.errors import ConfigurationError, DomainError
from common.area_power.jsonutil import digest
from common.area_power.tracking import match_approved_expectations
from common.area_power.triage import (BATCH_SIZE, PROMPT_VERSION, Thresholds, batch_request, build_card, select_items,
                                      side_context, validate_batch)
from .ai_models import ACTOR, AI_CONTEXT_ERRORS, failure_detail
from .tracking.dispatch import on_triage_finished

FINISHED = ('completed', 'failed')
_OBSERVED_SUB_PHASES = {'SENDING': 'sending', 'SERVER_RESPONDED': 'server_responded',
                        'RESPONSE_RECEIVED': 'response_received', 'VALIDATING': 'validating'}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _aborts(code: str) -> bool:
    return code in AI_CONTEXT_ERRORS or code.startswith(('AUTHORIZATION_', 'DATABASE_', 'DBAAS_'))


def _http_status(error) -> int | None:
    status = getattr(error, 'http_status', None)
    return status if isinstance(status, int) and 100 <= status <= 599 else None


def judge(runtime, cards, notes, context, same_rtl, kind, task, retry_limit, coverage, *, on_progress=None, attribution=None):
    """Retry this batch only after a confirmed, retryable failure."""
    evidence, instructions = batch_request(cards, notes, context, same_rtl, kind, attribution=attribution)
    retries = 0
    max_attempts = retry_limit + 1

    def report(**fields):
        if on_progress is not None:
            on_progress(**fields)

    while True:
        retry_error = None
        last_error = None
        last_http_status = None
        attempt = retries + 1
        report(phase='calling_model', sub_phase='preparing', attempt=attempt, max_attempts=max_attempts,
               retry_wait_seconds=None, retry_at=None)

        def observe(step):
            mapped = _OBSERVED_SUB_PHASES.get(str(step))
            if mapped is not None:
                report(phase='calling_model', sub_phase=mapped, attempt=attempt, max_attempts=max_attempts,
                       retry_wait_seconds=None, retry_at=None)

        try:
            response = runtime.infer(task, evidence, instructions, PROMPT_VERSION, observer=observe)
        except ReviewError as exc:
            if _aborts(exc.code) or exc.code == 'AI_INPUT_LIMIT':
                raise
            if retries >= retry_limit or receipt(exc) is None:
                return validate_batch(cards, notes, None, exc.code)
            retry_error = exc
            last_error = exc.code
            last_http_status = _http_status(exc)
        except (ValueError, KeyError, IndexError, TypeError):
            return validate_batch(cards, notes, None, 'ai_invalid_response')
        else:
            results = validate_batch(cards, notes, response)
            if retries >= retry_limit or not any(item['failed'] for item in results):
                return results
            last_error = next((item.get('failure') for item in results if item.get('failed')), 'ai_invalid_response')
        retries += 1
        delay = retry_delay_seconds(retries, retry_error)
        retry_at = (_now() + timedelta(seconds=delay)).isoformat()
        report(phase='retrying', sub_phase=None, attempt=attempt, max_attempts=max_attempts,
               last_error=last_error, last_http_status=last_http_status,
               retry_wait_seconds=round(float(delay), 1), retry_at=retry_at)
        runtime.wait_for_retry(delay)
        coverage['retry_calls'] += 1


def _update(db, run_id, *, state=None, error=None, **payload):
    with db.connect() as conn:
        row = db.record(conn, run_id, lock=True, kind='ai_run')
        fields = {'payload': {**row['payload'], **payload}}
        if state:
            fields.update(state=state, error_detail=error)
        return db.update_record(conn, run_id, ACTOR, **fields)


def _store(db, run, results):
    scope = {key: run[key] for key in ('project_id', 'version_id', 'block_id', 'snapshot_id') if run.get(key) is not None}
    for result in results:
        key = digest({'run': run['id'], 'group': result['group_id']})
        with db.connect() as conn:
            advisory_lock(conn, key)
            if existing_dedupe(db, conn, 'judgement', key) is None:
                db.insert_record(conn, 'judgement', result, ACTOR, parent_id=run['id'], state=result['verdict'], dedupe_key=key,
                                 error_detail={'code': result['failure']} if result['failed'] else None, **scope)


def run_triage(db, settings, run_id):
    with db.connect() as conn:
        run = db.record(conn, run_id, kind='ai_run')
        comparison = db.record(conn, run['parent_id'], kind='comparison')
    if run['state'] in FINISHED:
        return run
    try:
        final = _run(db, settings, run, comparison)
    except ConfigurationError:
        raise
    except Exception as exc:
        if isinstance(exc, DomainError) and exc.code in ('not_found', 'analysis_deleted'):
            raise
        # Never leave a run that looks like it is still working.
        detail = failure_detail(exc)
        now = _now_iso()
        with db.connect() as conn:
            latest = db.record(conn, run_id, lock=True, kind='ai_run')
            merged_progress = {
                **(latest['payload'].get('progress') or {}),
                'phase': 'failed', 'sub_phase': None, 'last_error': detail.get('code'),
                'retry_wait_seconds': None, 'retry_at': None, 'phase_started_at': now, 'updated_at': now,
            }
            failed_run = db.update_record(conn, run_id, ACTOR, state='failed', error_detail=detail,
                                          payload={**latest['payload'], 'progress': merged_progress})
        on_triage_finished(db, settings, failed_run)
        raise
    on_triage_finished(db, settings, final)
    return final


def _run(db, settings, run, comparison):
    payload = run['payload']
    thresholds = Thresholds.model_validate(payload['thresholds'])
    notes, process = payload.get('notes', []), payload.get('process') or {}
    runtime = runtime_for(db, run, comparison)
    # Old runs keep their pinned REVIEW choice, including explicit reruns.
    task = 'AREA_TRIAGE' if 'AREA_TRIAGE' in runtime.context else 'REVIEW'
    started_at = _now_iso()
    verdict_counts = {'unexpected': 0, 'needs_review': 0, 'expected': 0, 'minor': 0, 'failed': 0}
    progress = {
        'phase': 'validating', 'sub_phase': None,
        'started_at': started_at, 'phase_started_at': started_at, 'updated_at': started_at,
        'current_batch': None, 'total_batches': (payload.get('coverage') or {}).get('batches'),
        'batch_items': None, 'split_remaining': 0,
        'attempt': None, 'max_attempts': payload.get('retry_limit', 0) + 1,
        'last_error': None, 'last_http_status': None,
        'retry_wait_seconds': None, 'retry_at': None,
        'timeout_seconds': None, 'avg_batch_seconds': None, 'last_batch_seconds': None,
        'verdict_counts': dict(verdict_counts),
    }

    def publish_progress(*, state=None, error=None, coverage=None, best_effort=False, extra=None, **changes):
        now = _now_iso()
        prev_phase = progress.get('phase')
        prev_batch = progress.get('current_batch')
        prev_attempt = progress.get('attempt')
        progress.update(changes)
        progress['updated_at'] = now
        if (progress.get('phase') != prev_phase or progress.get('current_batch') != prev_batch
                or progress.get('attempt') != prev_attempt):
            progress['phase_started_at'] = now
        if runtime.timeout_seconds is not None:
            progress['timeout_seconds'] = runtime.timeout_seconds
        progress['verdict_counts'] = dict(verdict_counts)
        updates = {'progress': dict(progress)}
        if coverage is not None:
            updates['coverage'] = dict(coverage)
        if extra:
            updates.update(extra)
        try:
            return _update(db, run['id'], state=state, error=error, **updates)
        except Exception:
            if best_effort:
                return None
            raise

    publish_progress(state='running', error=None, phase='validating')
    abort = None
    try:
        runtime.validate(tasks=(task,))
    except ConfigurationError:
        raise
    except Exception as exc:
        abort = failure_detail(exc)
    publish_progress(phase='loading_comparison', sub_phase=None, last_error=abort['code'] if abort else None)
    view = observation_view(db, settings, comparison)
    context = side_context(view, process.get('current'), process.get('baseline'))
    selection = select_items(view, thresholds, payload.get('watched_paths', []), context)
    if selection.over_limit:
        fail_coverage = {'expected_items': selection.counts['total'], 'processed_items': 0, 'failed_items': 0,
                         'batches': 0, 'processed_batches': 0, 'retry_calls': 0}
        return publish_progress(
            state='failed', error={'code': 'triage_candidate_limit'}, coverage=fail_coverage,
            phase='failed', sub_phase=None, total_batches=0, last_error='triage_candidate_limit',
            extra={'side_context': context, 'counts': selection.counts, 'missing_watched': selection.missing_watched},
        )
    cards = [build_card(view, item, context, thresholds) for item in selection.items]
    approved_expectations = payload.get('approved_expectations') or []
    comparison_condition = payload.get('comparison_condition') or 'same_condition'
    if approved_expectations:
        publish_progress(phase='evaluating_expectations', sub_phase=None)
    code_explained_results = []
    ai_cards = []
    for card in cards:
        if approved_expectations:
            matched = match_approved_expectations(
                card, approved_expectations, comparison_condition=comparison_condition,
                separator=view.cm.hierarchy_separator,
            )
            card['expectation_matches'] = matched['matches']
            if matched['fully_explained']:
                quotes = [ev['quote'] for ev in matched['evidence'] if ev.get('quote')]
                code_explained_results.append({
                    'group_id': card['id'], 'card': card, 'verdict': 'expected', 'cause': 'note',
                    'explanation': '승인된 예상 변화 범위와 일치하는 변화입니다.',
                    'note_ids': [], 'quotes': quotes[:3], 'failed': False, 'failure': None,
                    'adjusted': [], 'code_explained': True, 'expectation_matches': matched['matches'],
                })
                continue
        ai_cards.append(card)
    batches = [ai_cards[start:start + BATCH_SIZE] for start in range(0, len(ai_cards), BATCH_SIZE)]
    coverage = {
        'expected_items': len(cards), 'processed_items': 0, 'failed_items': 0,
        'code_explained_items': len(code_explained_results),
        'batches': len(batches), 'processed_batches': 0, 'retry_calls': 0,
    }
    publish_progress(
        state='running', error=None, coverage=coverage,
        phase='saving_batch' if code_explained_results and not batches else ('calling_model' if batches else 'saving_batch'),
        sub_phase='preparing' if batches else None,
        total_batches=len(batches),
        extra={'prompt_version': PROMPT_VERSION, 'side_context': context,
               'counts': selection.counts, 'attribution': selection.attribution,
               'missing_watched': selection.missing_watched},
    )
    if code_explained_results:
        _store(db, run, code_explained_results)
        coverage['processed_items'] += len(code_explained_results)
        verdict_counts['expected'] += len(code_explained_results)
        publish_progress(coverage=coverage, phase='saving_batch', sub_phase=None)
    call_durations: list[float] = []

    def tally(results):
        for result in results:
            verdict = result.get('verdict')
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
            if result.get('failed'):
                verdict_counts['failed'] += 1

    for batch_index, batch in enumerate(batches, start=1):
        pending = [batch]
        while pending:
            part = pending.pop()
            if abort is None:
                call_started = monotonic()

                def on_call_progress(**fields):
                    publish_progress(
                        coverage=coverage, best_effort=True,
                        current_batch=batch_index, total_batches=len(batches),
                        batch_items=len(part), split_remaining=len(pending),
                        **fields,
                    )

                try:
                    results = judge(runtime, part, notes, context, payload.get('same_rtl', False),
                                    payload.get('comparison_kind'), task, payload.get('retry_limit', 0),
                                    coverage, on_progress=on_call_progress, attribution=selection.attribution)
                except ReviewError as exc:
                    if exc.code == 'AI_INPUT_LIMIT':
                        if len(part) > 1:
                            middle = len(part) // 2
                            pending.extend((part[middle:], part[:middle]))
                            publish_progress(
                                coverage=coverage, best_effort=True,
                                phase='splitting_batch', sub_phase=None,
                                current_batch=batch_index, total_batches=len(batches),
                                batch_items=middle, split_remaining=len(pending),
                                last_error='AI_INPUT_LIMIT', last_http_status=None,
                                retry_wait_seconds=None, retry_at=None,
                            )
                            continue
                        results = validate_batch(part, notes, None, 'ai_context_limit')
                    else:
                        abort = failure_detail(exc)
                else:
                    elapsed = round(max(0.0, monotonic() - call_started), 1)
                    call_durations.append(elapsed)
                    progress['last_batch_seconds'] = elapsed
                    progress['avg_batch_seconds'] = round(sum(call_durations) / len(call_durations), 1)
            if abort is not None:
                results = validate_batch(part, notes, None, abort['code'])
            _store(db, run, results)
            tally(results)
            coverage['processed_items'] += len(results)
            coverage['failed_items'] += sum(result['failed'] for result in results)
            if pending:
                publish_progress(
                    coverage=coverage, phase='saving_batch', sub_phase=None,
                    current_batch=batch_index, total_batches=len(batches),
                    batch_items=len(part), split_remaining=len(pending),
                    retry_wait_seconds=None, retry_at=None,
                )
        coverage['processed_batches'] += 1
        publish_progress(
            coverage=coverage, phase='saving_batch', sub_phase=None,
            current_batch=batch_index, total_batches=len(batches),
            batch_items=len(batch), split_remaining=0,
            retry_wait_seconds=None, retry_at=None,
        )
    failed = coverage['failed_items'] > 0
    error = {**(abort or {'code': 'ai_incomplete'}), 'failed_items': coverage['failed_items']} if failed else None
    return publish_progress(
        state='failed' if failed else 'completed', error=error, coverage=coverage,
        phase='failed' if failed else 'completed', sub_phase=None,
        last_error=error['code'] if error else None,
        retry_wait_seconds=None, retry_at=None,
        extra={'provider_metadata': runtime.receipts},
    )
