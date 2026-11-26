"""OA side of the instance triage: which notes apply, preview, run and mail requests.

The model is never called here. A run request only records the frozen inputs
and enqueues a ``judge`` job for the central VWP process; the digest mail is a
``mail`` job for the OA mail consumer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from psycopg import sql
from pydantic import Field, field_validator

from common.area_power.analysis import observation_view
from common.area_power.atlas_bindings import (
    BINDING_PREFIX,
    collect_applicable_triage_inputs,
    resolve_area_version_info,
)
from common.area_power.errors import DomainError
from common.area_power.models import COMPARISON_FORMAT
from common.area_power.triage import BATCH_SIZE, ENGINE, MAX_ITEMS, Thresholds, select_items, side_context
from common.sanity_common import ReviewError
from .ai_context import describe_area_triage_model, pin_area_triage_task
from .analysis_deletion import request_owner
from .schemas import Input, addresses, reject

FINISHED = ('completed', 'failed')
WORKER_ONLINE_TTL_SECONDS = 60


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
    """NET_VER of each Area version through explicit version payload or shared catalog; None when unlinked."""
    host = _host_db(service)
    bindings = None
    if host is not None:
        bindings = [
            row['payload']
            for row in host.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                (BINDING_PREFIX + '%',),
            ).fetchall()
        ]
    return {
        str(version['id']): resolve_area_version_info(
            service.db, None, version, block_id, host_db=host, cached_bindings=bindings
        ).get('net_ver')
        for version in versions
    }


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
    return collect_applicable_triage_inputs(
        service.db,
        conn,
        current,
        baseline,
        kind,
        raw_db=service.db.raw,
        host_db=_host_db(service),
        lineage_fn=service.lineage,
    )


def prepare(service, identifier) -> dict:
    with service.db.connect() as conn:
        comparison = service.db.record(conn, identifier, kind='comparison')
        prepared = {'comparison': comparison, **triage_context(service, conn, comparison)}
    actor = getattr(service.access, 'actor', None)
    model = describe_area_triage_model(_host_db(service), getattr(actor, 'user_id', None))
    return {**prepared, 'ai_model': model}


def select(raw, prepared, thresholds):
    """Candidate selection on the cached comparison view; no authorization transaction is open."""
    view = observation_view(raw, raw.settings, prepared['comparison'])
    context = side_context(view, prepared['process']['current'], prepared['process']['baseline'])
    return context, select_items(view, thresholds, prepared['watched_paths'], context)


def preview_result(prepared, context, selection, thresholds) -> dict:
    return {'thresholds': thresholds.model_dump(mode='python'), 'counts': selection.counts,
            'attribution': selection.attribution,
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
        ai_context, retry_limit = pin_area_triage_task(service.access, _host_db(service))
        if selection.items and ai_context['AREA_TRIAGE'].get('error'):
            reject('The project comparison AI selection is unavailable.', ai_context['AREA_TRIAGE']['error'], 409)
        expected_batches = -(-len(selection.items) // BATCH_SIZE)
        now_iso = datetime.now(timezone.utc).isoformat()
        model_meta = prepared.get('ai_model') or {}
        ai_model = {key: model_meta.get(key) for key in ('connection_label', 'model', 'site')}
        payload = {'engine': ENGINE, 'retry_limit': retry_limit, 'thresholds': body.thresholds.model_dump(mode='python'),
                   'attribution': selection.attribution, 'counts': selection.counts,
                   'notes': prepared['notes'], 'note_warnings': prepared['note_warnings'], 'same_rtl': prepared['same_rtl'],
                   'watched_paths': prepared['watched_paths'], 'approved_expectations': prepared.get('approved_expectations', []),
                   'process': prepared['process'],
                   'comparison_kind': prepared['comparison_kind'], 'labels': body.labels, 'requested_by': actor,
                   'ai_context': ai_context, 'ai_model': ai_model,
                   'coverage': {'expected_items': selection.counts['total'], 'processed_items': 0, 'failed_items': 0,
                                'batches': expected_batches, 'processed_batches': 0, 'retry_calls': 0},
                   'progress': {'phase': 'queued', 'sub_phase': None, 'phase_started_at': now_iso,
                                'updated_at': now_iso, 'total_batches': expected_batches,
                                'max_attempts': retry_limit + 1}}
        run = service.db.insert_record(conn, 'ai_run', payload, actor, state='queued', parent_id=comparison['id'],
                                       upload_id=comparison['upload_id'], baseline_upload_id=comparison['baseline_upload_id'],
                                       **service.scope(comparison))
        job = service.db.enqueue(conn, 'judge', actor=actor, parent_id=run['id'], payload={}, **service.scope(comparison))
        return {'ai_run': run, 'job': job}


def _summary(run, actor):
    payload = run['payload']
    return {**{key: run.get(key) for key in ('id', 'state', 'created_at', 'updated_at', 'error_detail', 'parent_id')},
            'payload': {key: payload.get(key) for key in ('engine', 'retry_limit', 'thresholds', 'coverage', 'missing_watched', 'note_warnings',
                                                           'same_rtl', 'requested_by', 'side_context', 'counts', 'attribution', 'labels', 'progress', 'ai_model')},
            'can_delete': actor.auth_method == 'sso' and request_owner(run) == actor.user_id,
            'note_count': len(payload.get('notes', []))}


def runs(service, identifier) -> dict:
    with service.db.connect() as conn:
        service.db.record(conn, identifier, kind='comparison')
        rows = [row for row in service.all_records(conn, 'ai_run', parent_id=identifier) if row['payload'].get('engine') == ENGINE]
        rows.sort(key=lambda row: str(row.get('created_at')), reverse=True)
        return {'runs': [_summary(row, service.access.actor) for row in rows]}


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


def _judge_job(service, conn, run):
    attempt_job_id = run['payload'].get('explicit_attempt_job_id')
    if attempt_job_id:
        try:
            job = service.db.record(conn, UUID(str(attempt_job_id)), kind='job')
            if job.get('job_kind') == 'judge':
                return job
        except (DomainError, ReviewError, ValueError):
            pass
    jobs = [row for row in service.db.records(conn, 'job', parent_id=run['id'], limit=5) if row.get('job_kind') == 'judge']
    return jobs[0] if jobs else None


def _job_summary(job):
    if job is None:
        return None
    return {
        'id': job['id'],
        'state': job['state'],
        'job_kind': job.get('job_kind'),
        'created_at': job.get('created_at'),
        'claimed_at': job.get('claimed_at'),
        'started_at': job.get('started_at'),
        'finished_at': job.get('finished_at'),
        'error_detail': job.get('error_detail'),
        'claimed': bool(job.get('claim_owner')),
    }


def _vwp_observability(service, conn, run, job, server_time):
    """Aggregate VWP worker liveness and queue depth without exposing other projects."""
    raw = service.db.raw
    table = raw.table('di_area_power_tracking_record')
    heartbeat = raw.worker_heartbeat(conn, 'vwp_server') if hasattr(raw, 'worker_heartbeat') else None
    activity = conn.execute(sql.SQL(
        "SELECT MAX(COALESCE(finished_at, started_at, claimed_at)) AS last_at "
        "FROM {} WHERE record_type = 'job' AND consumer_server = 'vwp_server' AND claimed_at IS NOT NULL"
    ).format(table)).fetchone()
    heartbeat_at = heartbeat['updated_at'] if heartbeat else None
    heartbeat_payload = (heartbeat.get('payload') or {}) if heartbeat else {}
    online = None if heartbeat_at is None else (server_time - heartbeat_at).total_seconds() <= WORKER_ONLINE_TTL_SECONDS
    worker = {
        'online': online,
        'state': heartbeat_payload.get('worker_state') if heartbeat else None,
        'current_job_kind': heartbeat_payload.get('job_kind') if heartbeat else None,
        'heartbeat_at': heartbeat_at,
        'last_job_activity_at': activity['last_at'] if activity else None,
    }
    active = run['state'] not in FINISHED or (job is not None and job['state'] in ('pending', 'running'))
    if not active:
        return worker, None
    total_pending = conn.execute(sql.SQL(
        "SELECT COUNT(*) AS cnt FROM {} WHERE record_type = 'job' AND consumer_server = 'vwp_server' AND state = 'pending'"
    ).format(table)).fetchone()['cnt']
    pending_ahead = 0
    if job is not None and job['state'] == 'pending':
        pending_ahead = conn.execute(sql.SQL(
            "SELECT COUNT(*) AS cnt FROM {} WHERE record_type = 'job' AND consumer_server = 'vwp_server' "
            "AND state = 'pending' AND (created_at, id) < (%s, %s)"
        ).format(table), (job['created_at'], job['id'])).fetchone()['cnt']
    job_id = job['id'] if job is not None else None
    other_running = conn.execute(sql.SQL(
        "SELECT job_kind, started_at, claimed_at FROM {} "
        "WHERE record_type = 'job' AND consumer_server = 'vwp_server' AND state = 'running' "
        "AND (%s::uuid IS NULL OR id <> %s::uuid) ORDER BY started_at ASC NULLS LAST, id ASC LIMIT 5"
    ).format(table), (job_id, job_id)).fetchall()
    first_other = other_running[0] if other_running else None
    queue = {
        'pending_ahead': pending_ahead,
        'total_pending': total_pending,
        'other_running_count': len(other_running),
        'other_running_job_kind': first_other['job_kind'] if first_other else None,
        'other_running_since': (first_other['started_at'] or first_other['claimed_at']) if first_other else None,
    }
    return worker, queue


def results(service, identifier) -> dict:
    with service.db.connect() as conn:
        server_time = conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()['now']
        run = _run(service, conn, identifier)
        identifier = run['id']
        job = _judge_job(service, conn, run)
        worker, queue = _vwp_observability(service, conn, run, job, server_time)
        items = [] if run['state'] == 'queued' else [{'id': row['id'], 'state': row['state'], **row['payload']} for row in service.all_records(conn, 'judgement', parent_id=identifier)]
        mails = [{'id': row['id'], 'created_at': row.get('created_at'), **row['payload']}
                 for row in service.all_records(conn, 'triage_mail', parent_id=identifier)]
        jobs = {str(mail_job['parent_id']): mail_job for mail in mails for mail_job in service.db.records(conn, 'job', parent_id=mail['id'], limit=1)}
        for mail in mails:
            mail_job = jobs.get(str(mail['id']))
            mail['job_state'] = mail_job['state'] if mail_job else None
            mail['job_error'] = mail_job.get('error_detail') if mail_job else None
        return {
            'ai_run': {**_summary(run, service.access.actor), 'notes': run['payload'].get('notes', [])},
            'items': items, 'mails': mails,
            'job': _job_summary(job), 'queue': queue, 'worker': worker, 'server_time': server_time,
        }


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
