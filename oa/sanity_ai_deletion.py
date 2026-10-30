"""Explicit removal of an owner's terminal AI execution history.

Single-account deployments use their existing OA table DELETE privilege. No
function installation or privilege change is performed by the application.
Report evidence and human decisions are retained, along with a small immutable
marker that prevents reuse of a deleted inference request ID.
"""
from common.sanity_common import digest
from common.sanity_db import uuid_value
from common.sanity_submission import check
from oa.sanity_workspace import record_id

PREFIX, NAMESPACE = 'ai.deleted.', 'oa-ai-deleted-v1'


def available(service):
    if service.db.side != 'oa' or service.db.privilege_mode != 'single_account':
        return False
    row = service.db.execute("SELECT has_table_privilege(current_user, "
        "quote_ident(%s) || '.sanity_oa_data', 'DELETE') AS allowed",
        (service.db.schema,)).fetchone()
    return bool(row and row['allowed'])


def marker(service, identity):
    from oa.sanity_ai_jobs import _NULL_COLUMNS
    key = PREFIX + str(uuid_value(identity, 'job_id'))
    row = service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
    if row is None:
        return None
    value = row['payload']
    check(isinstance(value, dict) and set(value) == {'namespace', 'id', 'actor_id', 'sha256'}
          and value['namespace'] == NAMESPACE and value['id'] == str(identity)
          and isinstance(value['actor_id'], str) and 1 <= len(value['actor_id']) <= 100
          and value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'})
          and row['record_id'] == record_id('WS_CONFIG', key) and row['state'] == {} and row['version'] == 0
          and row['status'] == 'RECORDED' and row['created_at'] == row['updated_at']
          and all(row.get(k) is None for k in _NULL_COLUMNS), 'AI_JOB_CORRUPT')
    return value


def ensure_not_deleted(service, identity):
    check(marker(service, identity) is None, 'AI_JOB_DELETED')


def worker_load(service, identity, loader=None):
    event = (loader or service._load)(identity, required=False)
    check(event is not None or marker(service, identity) is not None, 'AI_JOB_NOT_FOUND')
    return event


def delete(service, user_id, identity):
    from oa.sanity_ai_jobs import TERMINAL
    from oa.sanity_ai_submissions import Submissions, header, job_lock

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
    check(current['status'] in TERMINAL, 'AI_JOB_ACTIVE')
    if intent and event:
        # Preparation resolves an omitted model_id to its pinned default model.
        check({**intent['data']['command'], 'model_id': intent['data']['model']}
              == {**command, 'model_id': event['data']['model']}, 'AI_JOB_CORRUPT')
    check(available(service), 'AI_JOB_DELETE_NOT_CONFIGURED')
    value = {'namespace': NAMESPACE, 'id': identity, 'actor_id': user_id}
    value['sha256'] = digest(value)
    # The marker and all execution rows change atomically, including if DELETE
    # is revoked between the permission check and the actual statement.
    with service.db.transaction():
        service.ws.put('WS_CONFIG', PREFIX + identity, value)
        service.db.execute("DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND ("
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s) OR "
            "(workspace_key LIKE %s AND payload->>'namespace'=%s AND payload->>'job_id'=%s))",
            ('ai1.job.' + identity + '.%', 'oa-ai-v1',
             'ai2.' + identity + '.%', 'oa-ai-batch-v1',
             'ai.live.' + identity + '.%', 'ai-live-v1',
             'ai.intent.' + identity + '.%', 'oa-ai-submission-v1',
             'ai.resume.%', 'oa-ai-resume-v1', identity))
    return {'id': identity, 'deleted': True}
