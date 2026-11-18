"""Remove one request and its execution history while retaining shared inputs."""
from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from area_power.common.errors import DomainError
from area_power.common.triage import ENGINE
from .service import reject


def request_owner(row):
    if row['record_type'] == 'mapping_proposal':
        return row['created_by']
    if row['record_type'] == 'ai_run' and row['payload'].get('engine') == ENGINE:
        return row['payload'].get('requested_by') or row['created_by']
    return None


def _family(service, conn, identifier, user_id):
    db = service.db
    table = db.table('di_area_power_tracking_record')
    run = db.record(conn, identifier)
    service.authorize(conn, run, write=True)
    if request_owner(run) != user_id:
        reject('Only the original requester may remove this analysis.', 'forbidden', 403)
    # Rerun attempts are siblings under the comparison, linked by previous_id.
    comparison = run['parent_id']
    seen = set()
    while run['record_type'] == 'ai_run' and run['previous_id']:
        seen.add(run['id'])
        run = db.record(conn, run['previous_id'])
        if (run['id'] in seen or run['parent_id'] != comparison or run['record_type'] != 'ai_run'
                or request_owner(run) != user_id):
            reject('Invalid analysis attempt chain.', 'invalid_analysis_attempt', 409)
    root = db.record(conn, run['id'], lock=True)
    ids = [root['id']]
    if root['record_type'] == 'ai_run':
        layer = ids.copy()
        while layer:
            children = conn.execute(sql.SQL(
                "SELECT id, created_by, record_type, payload, parent_id FROM {} WHERE previous_id=ANY(%s) "
                "AND record_type='ai_run' ORDER BY id FOR UPDATE").format(table), (layer,)).fetchall()
            if any(r['parent_id'] != comparison or request_owner(r) != user_id for r in children):
                reject('Invalid analysis attempt chain.', 'invalid_analysis_attempt', 409)
            layer = [r['id'] for r in children]
            ids.extend(layer)
    return ids


def remove(service, identifier):
    actor = service.access.actor
    if actor.auth_method != 'sso':
        reject('Sign in to remove an analysis.', 'forbidden', 403)
    db = service.db
    table = db.table('di_area_power_tracking_record')
    with db.connect() as conn:
        try:
            ids = _family(service, conn, identifier, actor.user_id)
        except DomainError as exc:
            if exc.code != 'not_found' or conn.execute(sql.SQL("SELECT id FROM {} WHERE id=%s").format(table),
                                                       (identifier,)).fetchone():
                raise
            return {'removed': [identifier]}
        # Parent row locks serialize new attempts, judgements and mail requests through
        # their foreign keys. Lock mail parents before collecting their queued jobs.
        mail_ids = [row['id'] for row in conn.execute(sql.SQL(
            "SELECT id FROM {} WHERE parent_id=ANY(%s) AND record_type='triage_mail' ORDER BY id FOR UPDATE"
        ).format(table), (ids,)).fetchall()]
        try:
            conn.execute(sql.SQL(
                "DELETE FROM {} WHERE id=ANY(%s) OR (parent_id=ANY(%s) AND record_type='judgement') "
                "OR (parent_id=ANY(%s) AND record_type='job' AND job_kind='judge') "
                "OR (parent_id=ANY(%s) AND record_type='job' AND job_kind='mail')"
            ).format(table), (ids + mail_ids, ids, ids, mail_ids))
        except InsufficientPrivilege:
            reject('Area tracking table DELETE permission is unavailable.', 'analysis_delete_not_configured', 403)
        return {'removed': ids}
