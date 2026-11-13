"""Internal DB consumer owned by the VWP API lifespan.

Claims commit before CSV/HTTP work starts. Graceful shutdown drains a claim
already underway and its job; forced termination does not recover running jobs.
"""
from __future__ import annotations

import logging
import threading
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb
from area_power.common.errors import DomainError
from area_power.common.jsonutil import dumps
from area_power.vwp_server.analysis import all_records, compute_comparison, parse_upload
from area_power.vwp_server.ai import (AIError, ensure_run, extract_one, failure_detail,
                           prepare_extractions, publish_numeric_alerts, run_judgement, new_attempt, mirror_attempt)

LOG = logging.getLogger('area_power.vwp.worker')
ACTOR = 'vwp-worker'


def compare(db, settings, comparison_id, requested_run=None, attempt_key=None):
    with db.connect() as conn:
        comparison = db.record(conn, comparison_id, kind='comparison')
    comparison = compute_comparison(db, settings, comparison_id)
    publish_numeric_alerts(db, comparison)
    run = ensure_run(db, comparison, run_id=requested_run)
    if attempt_key:
        run = new_attempt(db, comparison, run, attempt_key)
    extractions = prepare_extractions(db, settings, comparison, run)
    if requested_run or attempt_key:
        runs = [run]
    else:
        with db.connect() as conn:
            runs = all_records(db, conn, 'ai_run', parent_id=comparison_id)
    failure = any(pending['state'] == 'failed' for pending in runs)
    for pending in runs:
        if pending['state'] in ('completed', 'failed'):
            continue
        result = run_judgement(db, settings, comparison_id, pending['id'], extractions=extractions if str(pending['id']) == str(run['id']) else None)
        failure |= result['state'] == 'failed'
    if failure:
        raise AIError('ai_incomplete')


def dispatch(db, settings, job):
    kind = job['job_kind']
    if kind == 'compare':
        compare(db, settings, job['parent_id'], attempt_key=job['id'] if job.get('previous_id') else None)
    elif kind == 'judge':
        with db.connect() as conn:
            subject = db.record(conn, job['parent_id'])
            if subject['record_type'] == 'ai_run':
                run_id, comparison_id = subject['id'], subject['parent_id']
                comparison = db.record(conn, comparison_id, kind='comparison')
            elif subject['record_type'] == 'comparison':
                run_id, comparison_id, comparison = None, subject['id'], subject
            else:
                raise DomainError('Judge job requires an AI run or comparison', code='invalid_job_subject')
        original_run = None
        if job.get('previous_id'):
            original_run = ensure_run(db, comparison, run_id=run_id)
            attempt = new_attempt(db, comparison, original_run, job['id'])
            run_id = attempt['id']
        error = None
        try:
            if comparison['state'] != 'completed':
                compare(db, settings, comparison_id, requested_run=run_id)
            else:
                result = run_judgement(db, settings, comparison_id, run_id)
                if result['state'] == 'failed':
                    raise AIError('ai_incomplete')
        except Exception as exc:
            error = failure_detail(exc)
            raise
        finally:
            if original_run and subject['record_type'] == 'ai_run':
                mirror_attempt(db, original_run['id'], run_id, error)
    elif kind == 'extract':
        with db.connect() as conn:
            subject = db.record(conn, job['parent_id'])
        if subject['record_type'] == 'change':
            result = extract_one(db, settings, subject, attempt_id=job['id'])
            ids, failed = [result['id']], result['state'] == 'failed'
        elif subject['record_type'] == 'ai_run':
            with db.connect() as conn:
                comparison = db.record(conn, subject['parent_id'], kind='comparison')
            extractions = prepare_extractions(db, settings, comparison, subject, attempt_id=job['id'])
            ids, failed = [e['extraction_id'] for e in extractions], any(e.get('failed') for e in extractions)
            with db.connect() as conn:
                current = db.record(conn, subject['id'], lock=True, kind='ai_run')
                payload = dict(current['payload'])
                payload['extraction_ids'] = ids
                payload['extraction_coverage'] = {'expected_changes': len(extractions), 'processed_changes': len(extractions), 'failed': failed}
                db.update_record(conn, subject['id'], ACTOR, payload=payload)
        else:
            raise DomainError('Extract job requires a change or AI run', code='invalid_job_subject')
        with db.connect() as conn:
            current = db.record(conn, job['id'], lock=True, kind='job')
            payload = dict(current['payload'])
            payload.update({'extraction_ids': ids, 'coverage': {'expected_changes': len(ids), 'processed_changes': len(ids), 'failed': failed}})
            db.update_record(conn, job['id'], ACTOR, payload=payload)
        if failed:
            raise AIError('ai_extraction_failed')
    elif kind == 'ingest':
        with db.connect() as conn:
            upload = db.upload(conn, job['upload_id'], include_blob=True)
        # HTTP ingestion is synchronous. Legacy/manual jobs revalidate the actual ready original;
        # a pending/failed upload needs a new upload, not invented success.
        parsed, metadata = parse_upload(upload, settings)
        with db.connect() as conn:
            snapshot = db.record(conn, upload['snapshot_id'], kind='snapshot')
            stored = snapshot['payload'].get('structure_digest')
            if isinstance(stored, str):
                stored = bytes.fromhex(stored)
            if stored is not None and bytes(stored) != bytes(parsed.structure_digest):
                raise DomainError('Stored upload differs from its snapshot', code='stored_upload_mismatch')
            locked = db.record(conn, job['id'], lock=True, kind='job')
            payload = dict(locked['payload'])
            payload.update({'revalidated_upload_id': upload['id'], 'row_count': parsed.row_count, 'structure_digest': bytes(parsed.structure_digest).hex(), 'condition_key': bytes(parsed.condition_key).hex()})
            db.update_record(conn, job['id'], ACTOR, payload=payload)
    else:
        raise DomainError('Unsupported VWP job kind', code='unsupported_job_kind')


def persist_failure(db, job, error):
    """Terminal bookkeeping never changes completed numeric observations or prior run history."""
    with db.connect() as conn:
        if job['job_kind'] == 'ingest':
            conn.execute(sql.SQL("UPDATE {} SET state='failed', error_detail=%s WHERE id=%s AND (state<>'ready' OR %s)").format(db.table('di_area_power_csv')),
                         (Jsonb(error, dumps=dumps), job['upload_id'], error.get('code') in ('stored_upload_mismatch', 'invalid_csv')))
            return
        subject = db.record(conn, job['parent_id'], lock=True)
        if subject['record_type'] == 'comparison':
            payload = dict(subject['payload'])
            if subject['state'] != 'completed':
                payload['failure'] = error
                db.update_record(conn, subject['id'], ACTOR, state='failed', payload=payload, error_detail=error)
            else:
                payload['ai_state'] = 'failed'
                db.update_record(conn, subject['id'], ACTOR, payload=payload)
        elif subject['record_type'] == 'ai_run':
            if subject['state'] != 'completed':
                payload = dict(subject['payload'])
                payload['failure'] = error
                db.update_record(conn, subject['id'], ACTOR, state='failed', payload=payload, error_detail=error)
            comparison = db.record(conn, subject['parent_id'], lock=True, kind='comparison')
            payload = dict(comparison['payload'])
            payload['ai_state'] = 'failed'
            db.update_record(conn, comparison['id'], ACTOR, payload=payload)


def consume_once(db, settings, owner, *, claim_job=None):
    job = claim_job() if claim_job is not None else db.claim_job('vwp_server', owner)
    if job is None:
        return False
    try:
        dispatch(db, settings, job)
    except Exception as exc:
        error = failure_detail(exc)
        if isinstance(exc, DomainError):
            error['code'] = getattr(exc, 'code', 'validation_error')
        LOG.error('Job failed: %s', type(exc).__name__)
        try:
            persist_failure(db, job, error)
        except Exception as persistence_error:
            LOG.error('Failure persistence failed: %s', type(persistence_error).__name__)
        db.finish_job(job['id'], owner, error=error)
    else:
        db.finish_job(job['id'], owner)
    return True


class ProcessingWorker:
    """One non-daemon consumer; fatal claim/finish failures make readiness false."""

    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.owner = 'vwp-server:' + str(uuid4())
        self._stopping = threading.Event()
        self._started = threading.Event()
        self._failed = threading.Event()
        self._claim_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name='area-power-processing', daemon=False)

    @property
    def ready(self):
        return self._started.is_set() and not self._stopping.is_set() and not self._failed.is_set() and self._thread.is_alive()

    def _claim_job(self):
        # stop() linearizes with claiming. A claim already in progress is drained,
        # never abandoned in the committed running state during graceful shutdown.
        with self._claim_lock:
            if self._stopping.is_set():
                return None
            job = self.db.claim_job('vwp_server', self.owner)
            self._started.set()
            return job

    def _run(self):
        try:
            while not self._stopping.is_set():
                consumed = consume_once(self.db, self.settings, self.owner, claim_job=self._claim_job)
                if not consumed:
                    self._stopping.wait(self.settings.worker_poll_seconds)
        except BaseException as exc:
            self._failed.set()
            LOG.error('Processing stopped: %s', type(exc).__name__)
        finally:
            self._started.set()

    def start(self):
        self._thread.start()
        self._started.wait()
        if not self.ready:
            raise RuntimeError('VWP processing could not start')

    def stop(self):
        self._stopping.set()
        # Synchronize with a claim already underway before joining its processing.
        with self._claim_lock:
            pass
        if self._thread.ident is not None:
            self._thread.join()
