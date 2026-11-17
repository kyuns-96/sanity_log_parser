"""OA side of the instance triage: which notes apply, preview, run and mail requests.

The model is never called here. A run request only records the frozen inputs
and enqueues a ``judge`` job for the central VWP process; the digest mail is a
``mail`` job for the OA mail consumer.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from area_power.common.analysis import observation_view
from area_power.common.models import COMPARISON_FORMAT
from area_power.common.triage import BATCH_SIZE, ENGINE, MAX_ITEMS, Thresholds, select_items, side_context
from .service import Input, addresses, reject

BINDING_PREFIX = 'design-atlas-v1.binding.'
FINISHED = ('completed', 'failed')


class TriageInput(Input):
    thresholds: Thresholds = Field(default_factory=Thresholds)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator('labels')
    @classmethod
    def display_labels(cls, value):
        if set(value) - {'current', 'baseline'} or any(len(text) > 300 or '\n' in text or '\r' in text for text in value.values()):
            raise ValueError('Labels are short single-line display names for current and baseline')
        return value


class TriageMailInput(Input):
    recipients: list[str] = Field(default_factory=list)
    excluded_ids: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)


def _host_db(service):
    store = getattr(getattr(service.access, 'actor', None), 'access', None)
    return getattr(store, 'db', None)


def net_vers(service, versions, block_id) -> dict[str, str | None]:
    """NET_VER of each Area version through the shared catalog; None when it is not linked.

    Names, order and timestamps are never used to guess a NET_VER.
    """
    result = {str(version['id']): None for version in versions}
    host = _host_db(service)
    if host is None:
        return result
    bindings = [row['payload'] for row in host.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s", (BINDING_PREFIX + '%',)).fetchall()]
    for version in versions:
        natives = {binding.get('version_id') for binding in bindings
                   if binding.get('area_version_id') == str(version['id']) and binding.get('area_block_id') == str(block_id)}
        direct = version['payload'].get('sanity_version_id')
        if not natives and isinstance(direct, str):
            natives = {direct}
        labels = set()
        for native in natives:
            row = host.execute("SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s", (native,)).fetchone()
            if row and row['payload'].get('net_ver'):
                labels.add(row['payload']['net_ver'])
        if len(labels) == 1:
            result[str(version['id'])] = labels.pop()
    return result


def triage_context(service, conn, comparison) -> dict:
    """Everything a run freezes besides thresholds: notes, watch list, process, RTL identity."""
    if comparison['payload'].get('result_format') != COMPARISON_FORMAT:
        reject('Create a new comparison to use AI analysis.', 'comparison_format_upgrade_required', 409)
    if comparison['state'] != 'completed':
        reject('Only prepared comparisons can be analyzed.', 'comparison_not_ready', 409)
    if not comparison.get('baseline_upload_id'):
        reject('AI analysis needs a baseline execution.', 'baseline_required', 409)
    kind = comparison['payload']['request'].get('comparison_kind')
    current = service.db.upload(conn, comparison['upload_id'])
    baseline = service.db.upload(conn, comparison['baseline_upload_id'])
    chain = service.lineage(conn, current['version_id'])
    interval = chain
    if kind == 'previous_version':
        interval = chain[:chain.index(baseline['version_id'])] if baseline['version_id'] in chain else chain
    wanted = list(dict.fromkeys([*interval, *([baseline['version_id']] if kind == 'previous_version' else [])]))
    labels = net_vers(service, [service.db.record(conn, identifier, kind='version') for identifier in wanted], current['block_id'])
    baseline_net = labels.get(str(baseline['version_id'])) if kind == 'previous_version' else None
    applicable = [label for label in dict.fromkeys(labels[str(identifier)] for identifier in reversed(interval))
                  if label is not None and label != baseline_net]
    warnings = []
    unresolved = sum(labels[str(identifier)] is None for identifier in interval)
    if unresolved:
        warnings.append({'code': 'net_ver_unresolved', 'count': unresolved})
    if kind == 'previous_version' and baseline_net is None:
        warnings.append({'code': 'baseline_net_ver_unresolved', 'count': 1})
    current_net = labels.get(str(current['version_id']))
    same_rtl = kind == 'previous_version' and current_net is not None and current_net == baseline_net
    notes = []
    for row in service.db.records(conn, 'change', project_id=current['project_id'], block_id=current['block_id'], latest=True, limit=500):
        payload = row['payload']
        if payload.get('archived'):
            continue
        if payload.get('note_kind') == 'net_ver':
            applies = payload.get('net_ver') in applicable
        elif payload.get('note_kind') == 'project_change':
            applies = kind == 'previous_project' and str(payload.get('baseline_project_id')) == str(baseline['project_id'])
        else:
            applies = False
        if applies:
            notes.append({'id': str(row['id']), 'entity_id': str(row['entity_id']), 'revision': row['revision'],
                          'kind': payload['note_kind'], 'net_ver': payload.get('net_ver'), 'text': payload['text'],
                          'updated_by': row.get('updated_by'), 'created_at': row.get('created_at')})
    order = {label: position for position, label in enumerate(applicable)}
    notes.sort(key=lambda note: (note['kind'] != 'project_change', order.get(note['net_ver'], -1), note['id']))
    watched = []
    for row in service.db.records(conn, 'target', project_id=current['project_id'], block_id=current['block_id'], latest=True, limit=500):
        if row['payload'].get('watch'):
            watched.extend(row['payload'].get('paths', []))
    raw = service.db.raw if service.access is not None else service.db
    process = {'current': raw.record(conn, current['project_id'], kind='project')['payload'].get('process'),
               'baseline': raw.record(conn, baseline['project_id'], kind='project')['payload'].get('process')}
    return {'comparison_kind': kind, 'notes': notes, 'note_warnings': warnings, 'same_rtl': same_rtl,
            'applicable_net_vers': applicable, 'watched_paths': list(dict.fromkeys(watched)), 'process': process}


def prepare(service, identifier) -> dict:
    with service.db.connect() as conn:
        comparison = service.db.record(conn, identifier, kind='comparison')
        prepared = {'comparison': comparison, **triage_context(service, conn, comparison)}
    host = _host_db(service)
    model = {'ready': False, 'error': 'AI_AUTHENTICATED_REQUEST_REQUIRED'}
    if host is not None:
        from oa.sanity_ai_connections import ConnectionStore
        _, selected, default = ConnectionStore(host).task_selection(service.access.actor.user_id, 'AREA_TRIAGE')
        error = (default.get('reason') if default and not default.get('applied') else
                 selected.get('credential_error') if selected and not selected.get('credential_ready') else None)
        model = {'ready': bool(selected and error is None), 'error': error,
                 'connection_label': selected.get('label') if selected else None,
                 'model': selected.get('model') if selected else None,
                 'site': selected.get('execution_site', 'oa') if selected else None}
    return {**prepared, 'ai_model': model}


def select(raw, prepared, thresholds):
    """Candidate selection on the cached comparison view; no authorization transaction is open."""
    view = observation_view(raw, raw.settings, prepared['comparison'])
    context = side_context(view, prepared['process']['current'], prepared['process']['baseline'])
    return context, select_items(view, thresholds, prepared['watched_paths'], context)


def preview_result(prepared, context, selection, thresholds) -> dict:
    return {'thresholds': thresholds.model_dump(mode='python'), 'counts': selection.counts,
            'estimated_calls': -(-len(selection.items) // BATCH_SIZE), 'max_items': MAX_ITEMS,
            'over_limit': selection.over_limit, 'missing_watched': selection.missing_watched,
            'watched_paths': prepared['watched_paths'], 'context': context,
            'notes': [{key: note[key] for key in ('id', 'kind', 'net_ver', 'revision')} for note in prepared['notes']],
            'note_warnings': prepared['note_warnings'], 'same_rtl': prepared['same_rtl'],
            'applicable_net_vers': prepared['applicable_net_vers'], 'process': prepared['process'],
            'ai_model': prepared['ai_model']}


def request_run(service, prepared, selection, body: TriageInput, actor) -> dict:
    """Record the frozen inputs and hand the run to the central VWP queue."""
    if selection.over_limit:
        reject(f'More than {MAX_ITEMS} instances would be analyzed; raise the thresholds.', 'triage_candidate_limit', 409)
    with service.db.connect() as conn:
        comparison = service.db.record(conn, prepared['comparison']['id'], kind='comparison', lock=True)
        service.authorize(conn, comparison, write=True)
        service.validate_comparison(conn, prepared['comparison'], write=True)
        from area_power.common.ai import capture_context
        ai_context = capture_context(service.access, tasks=('AREA_TRIAGE',))
        if selection.items and ai_context['AREA_TRIAGE'].get('error'):
            reject('The project comparison AI selection is unavailable.', ai_context['AREA_TRIAGE']['error'], 409)
        payload = {'engine': ENGINE, 'thresholds': body.thresholds.model_dump(mode='python'),
                   'notes': prepared['notes'], 'note_warnings': prepared['note_warnings'], 'same_rtl': prepared['same_rtl'],
                   'watched_paths': prepared['watched_paths'], 'process': prepared['process'],
                   'comparison_kind': prepared['comparison_kind'], 'labels': body.labels, 'requested_by': actor,
                   'ai_context': ai_context, 'coverage': {'expected_items': selection.counts['total']}}
        run = service.db.insert_record(conn, 'ai_run', payload, actor, state='queued', parent_id=comparison['id'],
                                       upload_id=comparison['upload_id'], baseline_upload_id=comparison['baseline_upload_id'],
                                       **service.scope(comparison))
        job = service.db.enqueue(conn, 'judge', actor=actor, parent_id=run['id'], payload={}, **service.scope(comparison))
        return {'ai_run': run, 'job': job}


def _summary(run):
    payload = run['payload']
    return {**{key: run.get(key) for key in ('id', 'state', 'created_at', 'updated_at', 'error_detail', 'parent_id')},
            'payload': {key: payload.get(key) for key in ('engine', 'thresholds', 'coverage', 'missing_watched', 'note_warnings',
                                                           'same_rtl', 'requested_by', 'side_context', 'counts', 'labels')},
            'note_count': len(payload.get('notes', []))}


def runs(service, identifier) -> dict:
    with service.db.connect() as conn:
        service.db.record(conn, identifier, kind='comparison')
        rows = [row for row in service.all_records(conn, 'ai_run', parent_id=identifier) if row['payload'].get('engine') == ENGINE]
        rows.sort(key=lambda row: str(row.get('created_at')), reverse=True)
        return {'runs': [_summary(row) for row in rows]}


def _run(service, conn, identifier, *, lock=False):
    run = service.db.record(conn, identifier, kind='ai_run', lock=lock)
    if run['payload'].get('engine') != ENGINE:
        reject('This analysis run uses another engine.', 'unsupported_analysis_run', 404)
    seen = {str(run['id'])}
    while run['payload'].get('rerun_ai_run_id'):
        next_id = str(run['payload']['rerun_ai_run_id'])
        if next_id in seen:
            reject('Analysis rerun references contain a cycle.', 'invalid_analysis_attempt', 409)
        attempt = service.db.record(conn, UUID(next_id), kind='ai_run', lock=lock)
        if (attempt['payload'].get('engine') != ENGINE or attempt.get('previous_id') != run['id']
                or attempt['parent_id'] != run['parent_id']):
            reject('Analysis rerun reference is inconsistent.', 'invalid_analysis_attempt', 409)
        seen.add(next_id)
        run = attempt
    return run


def results(service, identifier) -> dict:
    with service.db.connect() as conn:
        run = _run(service, conn, identifier)
        identifier = run['id']
        items = [] if run['state'] == 'queued' else [{'id': row['id'], 'state': row['state'], **row['payload']} for row in service.all_records(conn, 'judgement', parent_id=identifier)]
        mails = [{'id': row['id'], 'created_at': row.get('created_at'), **row['payload']}
                 for row in service.all_records(conn, 'triage_mail', parent_id=identifier)]
        jobs = {str(job['parent_id']): job for mail in mails for job in service.db.records(conn, 'job', parent_id=mail['id'], limit=1)}
        for mail in mails:
            job = jobs.get(str(mail['id']))
            mail['job_state'] = job['state'] if job else None
            mail['job_error'] = job.get('error_detail') if job else None
        attempt_job = run['payload'].get('explicit_attempt_job_id')
        analysis_jobs = ([service.db.record(conn, UUID(str(attempt_job)), kind='job')] if attempt_job else
                         service.db.records(conn, 'job', parent_id=identifier, limit=1))
        job = analysis_jobs[0] if analysis_jobs else None
        retry_job = ({'id': job['id'], 'row_version': job['row_version'], 'state': job['state']}
                     if job and job['job_kind'] == 'judge' and job['state'] == 'failed' and run['state'] == 'failed'
                     and job.get('finished_at') and service.access and job['created_by'] == service.access.actor.user_id else None)
        return {'ai_run': {**_summary(run), 'notes': run['payload'].get('notes', [])}, 'items': items, 'mails': mails, 'retry_job': retry_job}


def request_mail(service, identifier, body: TriageMailInput, actor) -> dict:
    recipients = addresses(body.recipients)
    with service.db.connect() as conn:
        run = _run(service, conn, identifier, lock=True)
        identifier = run['id']
        service.authorize(conn, run, write=True)
        if run['state'] not in FINISHED:
            reject('The analysis is still running.', 'analysis_not_finished', 409)
        known = {row['payload']['group_id'] for row in service.all_records(conn, 'judgement', parent_id=identifier)}
        if set(body.excluded_ids) - known:
            reject('Excluded instances are not part of this analysis.', 'unknown_analysis_item')
        scope = service.scope(run)
        request = service.db.insert_record(conn, 'triage_mail', {'recipients': recipients, 'excluded_ids': list(dict.fromkeys(body.excluded_ids)),
                                                                  'requested_by': actor}, actor, state='requested', parent_id=run['id'], **scope)
        job = service.db.enqueue(conn, 'mail', actor=actor, parent_id=request['id'], payload={'recipients': recipients}, **scope)
        return {'mail_request': request, 'job': job}
