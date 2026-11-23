"""Explicit removal of an owner's AI execution history, including active jobs.

Single-account deployments use their existing OA table DELETE privilege. No
function installation or privilege change is performed by the application.
Report evidence and human decisions are retained, along with a small immutable
marker that prevents reuse of a deleted inference request ID.
"""
from common.sanity_db import uuid_value
from common.sanity_submission import check
from oa.sanity_ai_store import (
    DELETE_NAMESPACE as NAMESPACE, DELETE_PREFIX as PREFIX, deletion_available as available,
    deletion_marker as marker, ensure_not_deleted, job_lock, report_header as header, worker_load,
)
from oa.sanity_ai_submissions import Submissions


def delete(service, user_id, identity):
    identity = str(uuid_value(identity, 'job_id'))
    job_lock(service, identity)
    previous = marker(service, identity)
    if previous:
        check(previous['actor_id'] == user_id, 'FORBIDDEN')
        return {'id': identity, 'deleted': True}
    intent = Submissions(service).load(identity, required=False)
    event = service._load(identity, required=False)
    current = event or intent
    check(current is not None, 'AI_JOB_NOT_FOUND')
    command = current['data']['command']
    check(command['user_id'] == user_id, 'FORBIDDEN')
    header(service, command, write=True)
    if intent and event:
        # Preparation resolves an omitted model_id to its pinned default model.
        check({**intent['data']['command'], 'model_id': intent['data']['model']}
              == {**command, 'model_id': event['data']['model']}, 'AI_JOB_CORRUPT')
    check(available(service), 'AI_JOB_DELETE_NOT_CONFIGURED')
    return purge_locked(service, identity, user_id)


def purge_locked(service, identity, user_id):
    """Internal scope purge; caller has authorized the containing report."""
    identity = str(uuid_value(identity, 'job_id'))
    job_lock(service, identity)
    previous = marker(service, identity)
    if previous:
        check(previous['actor_id'] == user_id, 'AI_JOB_CORRUPT')
        return {'id': identity, 'deleted': True}
    value = {'namespace': NAMESPACE, 'id': identity, 'actor_id': user_id}
    # The marker and all execution rows change atomically, including if DELETE
    # is revoked between the permission check and the actual statement.
    with service.db.transaction():
        service.ws.put('WS_CONFIG', PREFIX + identity, value)
        service.db.execute("DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND ("
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s AND payload->>'job_id'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s AND payload->>'job_id'=%s))",
            ('ai1.job.' + identity + '.%', 'oa-ai-v1',
             'ai2.' + identity + '.%', 'oa-ai-batch-v1',
             'ai.live.' + identity + '.%', 'ai-live-v1',
             'ai.intent.' + identity + '.%', 'oa-ai-submission-v1',
             'ai.resume.%', 'oa-ai-resume-v1', identity,
             'ai.unknown.%', 'oa-ai-unknown-recovery-v1', identity))
    return {'id': identity, 'deleted': True}
