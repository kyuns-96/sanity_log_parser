"""Central VWP execution of one instance triage run.

Reads the frozen comparison, selects candidates, calls the requester's pinned
project-comparison connection in batches and stores one judgement per instance. A missing
or failing model never becomes a success: affected instances stay
``needs_review`` and the run is marked failed.
"""
from __future__ import annotations

from common.sanity_common import ReviewError
from area_power.common.ai import runtime_for
from area_power.common.analysis import advisory_lock, existing_dedupe, observation_view
from area_power.common.errors import ConfigurationError
from area_power.common.jsonutil import digest
from area_power.common.triage import (BATCH_SIZE, PROMPT_VERSION, Thresholds, batch_request, build_card, select_items,
                                      side_context, validate_batch)
from .ai import ACTOR, AI_CONTEXT_ERRORS, failure_detail

FINISHED = ('completed', 'failed')


def _aborts(code: str) -> bool:
    return code in AI_CONTEXT_ERRORS or code.startswith(('AUTHORIZATION_', 'DATABASE_', 'DBAAS_'))


def judge(runtime, cards, notes, context, same_rtl, kind, task):
    """One model call for a batch; a batch over the input limit is halved."""
    evidence, instructions = batch_request(cards, notes, context, same_rtl, kind)
    try:
        response = runtime.infer(task, evidence, instructions, PROMPT_VERSION)
    except ReviewError as exc:
        if _aborts(exc.code):
            raise
        if exc.code == 'AI_INPUT_LIMIT':
            if len(cards) > 1:
                middle = len(cards) // 2
                return (judge(runtime, cards[:middle], notes, context, same_rtl, kind, task)
                        + judge(runtime, cards[middle:], notes, context, same_rtl, kind, task))
            return validate_batch(cards, notes, None, 'ai_context_limit')
        return validate_batch(cards, notes, None, exc.code)
    except ConfigurationError:
        raise
    except (ValueError, KeyError, IndexError, TypeError):
        return validate_batch(cards, notes, None, 'ai_invalid_response')
    return validate_batch(cards, notes, response)


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
        return _run(db, settings, run, comparison)
    except ConfigurationError:
        raise
    except Exception as exc:
        # Never leave a run that looks like it is still working.
        _update(db, run_id, state='failed', error=failure_detail(exc))
        raise


def _run(db, settings, run, comparison):
    payload = run['payload']
    thresholds = Thresholds.model_validate(payload['thresholds'])
    notes, process = payload.get('notes', []), payload.get('process') or {}
    runtime = runtime_for(db, run, comparison)
    # Old runs keep their pinned REVIEW choice, including explicit reruns.
    task = 'AREA_TRIAGE' if 'AREA_TRIAGE' in runtime.context else 'REVIEW'
    abort = None
    try:
        runtime.validate(tasks=(task,))
    except ConfigurationError:
        raise
    except Exception as exc:
        abort = failure_detail(exc)
    view = observation_view(db, settings, comparison)
    context = side_context(view, process.get('current'), process.get('baseline'))
    selection = select_items(view, thresholds, payload.get('watched_paths', []), context)
    if selection.over_limit:
        return _update(db, run['id'], state='failed', error={'code': 'triage_candidate_limit'}, side_context=context,
                       counts=selection.counts, missing_watched=selection.missing_watched,
                       coverage={'expected_items': selection.counts['total'], 'processed_items': 0, 'failed_items': 0,
                                 'batches': 0, 'processed_batches': 0})
    cards = [build_card(view, item, context, thresholds) for item in selection.items]
    batches = [cards[start:start + BATCH_SIZE] for start in range(0, len(cards), BATCH_SIZE)]
    coverage = {'expected_items': len(cards), 'processed_items': 0, 'failed_items': 0, 'batches': len(batches), 'processed_batches': 0}
    _update(db, run['id'], state='running', error=None, prompt_version=PROMPT_VERSION, side_context=context,
            counts=selection.counts, missing_watched=selection.missing_watched, coverage=coverage)
    for batch in batches:
        if abort is None:
            try:
                results = judge(runtime, batch, notes, context, payload.get('same_rtl', False), payload.get('comparison_kind'), task)
            except ConfigurationError:
                raise
            except ReviewError as exc:
                # The requester's authority or AI connection changed: stop calling the model.
                abort = failure_detail(exc)
        if abort is not None:
            results = validate_batch(batch, notes, None, abort['code'])
        _store(db, run, results)
        coverage['processed_items'] += len(results)
        coverage['failed_items'] += sum(result['failed'] for result in results)
        coverage['processed_batches'] += 1
        _update(db, run['id'], coverage=dict(coverage))
    failed = coverage['failed_items'] > 0
    error = {**(abort or {'code': 'ai_incomplete'}), 'failed_items': coverage['failed_items']} if failed else None
    return _update(db, run['id'], state='failed' if failed else 'completed', error=error, coverage=coverage,
                   provider_metadata=runtime.receipts)
