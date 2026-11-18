"""Queue mapping advice for two ready uploads before any numeric comparison."""
from uuid import UUID

from psycopg import sql
from pydantic import Field, StrictInt

from area_power.common.ai import capture_context
from area_power.common.analysis import advisory_lock, existing_dedupe
from area_power.common.jsonutil import digest
from area_power.common.models import MappingRule
from oa.sanity_ai_task_defaults import TaskDefaults
from .service import ConfigInput, Input, reject


class ProposalInput(Input):
    request_id: UUID
    current_upload_id: UUID
    baseline_upload_id: UUID
    mapping_id: UUID | None = None
    draft: list[MappingRule] = Field(default_factory=list)


def submit(service, body, actor):
    host = getattr(actor, 'access', None)
    if actor.auth_method != 'sso' or host is None:
        reject('SSO is required for mapping suggestions.', 'AI_AUTHENTICATED_REQUEST_REQUIRED', 403)
    with service.db.connect() as conn:
        current = service.ready_upload(conn, body.current_upload_id)
        baseline = service.ready_upload(conn, body.baseline_upload_id)
        service.authorize(conn, current, write=True)
        if current['project_id'] == baseline['project_id']:
            if (current['block_id'] != baseline['block_id']
                    or baseline['version_id'] not in service.lineage(conn, current['version_id'])[1:]):
                reject('Select a predecessor of the current version in the same block.', 'invalid_baseline')
        else:
            service.designated_baseline(conn, current['project_id'], baseline['project_id'])
        mapping = service.mapping_for(conn, body.mapping_id, current, baseline)
        if any(r.kind != 'one_to_one' or r.target_id is not None for r in body.draft):
            reject('Draft rules must be one-to-one path pairs.', 'invalid_mapping')
        draft = [r.model_dump(mode='json', exclude={'id'}) for r in body.draft]
        key = digest(['mapping_proposal', actor.user_id, body.request_id])
        advisory_lock(conn, key)
        old = existing_dedupe(service.db, conn, 'mapping_proposal', key)
        if old:
            service.authorize(conn, old, write=True)
            if (old['upload_id'] != current['id'] or old['baseline_upload_id'] != baseline['id']
                    or old['payload']['mapping_id'] != (str(body.mapping_id) if mapping else None)
                    or old['payload']['draft'] != draft):
                reject('The request inputs have changed.', 'request_conflict', 409)
            return {'id': old['id']}
        settings = TaskDefaults(host.db).current()
        pin = capture_context(service.access, tasks=('AREA_TRIAGE',), defaults=settings['defaults'], vwp_only=True)
        if pin['AREA_TRIAGE'].get('error'):
            reject('Project comparison model is unavailable.', pin['AREA_TRIAGE']['error'], 409)
        scope = service.scope(current)
        payload = {'mapping_id': body.mapping_id, 'draft': draft, 'ai_context': pin, 'retry_limit': settings['area_retry_limit']}
        run = service.db.insert_record(conn, 'mapping_proposal', payload, actor.user_id, state='queued',
                                       upload_id=current['id'], baseline_upload_id=baseline['id'], dedupe_key=key, **scope)
        service.db.enqueue(conn, 'judge', actor=actor.user_id, parent_id=run['id'], payload={}, **scope)
        return {'id': run['id']}


class Approval(Input):
    selected: list[StrictInt] = Field(min_length=1)


def approve(service, identifier, body, actor):
    with service.db.connect() as conn:
        run = service.db.record(conn, identifier, kind='mapping_proposal', lock=True)
        service.authorize(conn, run, write=True)
        if run['created_by'] != actor:
            reject('Only the requester can approve this proposal.', 'forbidden', 403)
        payload = run['payload']
        if run['state'] != 'completed':
            reject('The mapping proposal is not ready.', 'proposal_not_ready', 409)
        choices = payload['result']['suggestions']
        selected = sorted(set(body.selected))
        if selected[0] < 0 or selected[-1] >= len(choices):
            reject('Unknown proposal choice.', 'invalid_mapping')
        if payload.get('approval'):
            if selected != payload['approval']['selected']:
                reject('This proposal was already approved.', 'proposal_already_approved', 409)
            return {'id': payload['approval']['mapping_id']}
        current = service.ready_upload(conn, run['upload_id'])
        baseline = service.ready_upload(conn, run['baseline_upload_id'])
        base = service.mapping_for(conn, payload['mapping_id'], current, baseline)
        rules = list(base['payload']['rules'] if base else []) + payload['draft']
        for index in selected:
            row = choices[index]
            rules.append({'kind': 'one_to_one', 'scope': row['scope'], 'baseline_paths': [row['baseline']],
                          'current_paths': [row['current']]})
        scope_keys = ('project_id', 'block_id', 'version_id')
        sides = {f'{side}_{key}': upload[key] for side, upload in [('baseline', baseline), ('current', current)] for key in scope_keys}
        scope = {key: current[key] for key in scope_keys}
        # Extend the selected mapping only when it belongs to this exact scope.
        # A rule inherited from another version/interval becomes a pair-specific mapping.
        revision = {}
        if (base and all(base.get(k) == v for k, v in scope.items())
                and not base.get('snapshot_id') and not base['payload'].get('valid_from_version_id')
                and not base['payload'].get('valid_through_version_id')):
            revision = {'entity_id': base['entity_id'], 'expected_row_version': base['row_version']}
        config = ConfigInput(record_type='mapping', payload={**sides, 'rules': rules}, **scope, **revision)
        saved = service.config(config, actor, conn=conn)
        service.db.update_record(conn, identifier, actor, payload={**payload, 'approval': {'mapping_id': saved['id'], 'selected': selected}})
        return {'id': saved['id']}

def history(service, current_id, baseline_id):
    with service.db.connect() as conn:
        service.db.upload(conn, current_id)
        service.db.upload(conn, baseline_id)
        rows = conn.execute(sql.SQL(
            "SELECT id, state, created_at FROM {} WHERE record_type='mapping_proposal' AND upload_id=%s "
            "AND baseline_upload_id=%s AND created_by=%s ORDER BY created_at DESC, id DESC"
        ).format(service.db.table('di_area_power_tracking_record')),
            (current_id, baseline_id, service.access.actor.user_id)).fetchall()
        return {'runs': rows}
