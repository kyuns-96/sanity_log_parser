"""Human CLI sessions delegate to the same authorized stores as the workbench."""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import secrets
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from common.sanity_cli_review import (ERRORS, MAX_RESPONSE, PROTOCOL, PROJECT, SESSION_SECONDS,
    alive, identifier, request_key, response_key, signature, signing_key, validate_envelope)
from common.sanity_cli_review_db import require_schema
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import Queue, lock_key
from common.sanity_submission import check
from oa.sanity_workspace import record_id, utcnow
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.sanity_workspace_pipeline import WorkspacePipelineStore, compact_review, event_brief
from oa.sanity_workspace_review import WorkspaceReviewStore
from oa.workspace_api import ID, ClusterEdit, GroupReviewChange, RationaleChange

PREFIX = 'cli-review.session.'
REVOKED = 'cli-review.revoked.'


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Page(Empty):
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 25


class Pinned(Empty):
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class Groups(Page, Pinned):
    search: str = Field(default='', max_length=100)
    status: Literal['', 'UNREVIEWED', 'PENDING', 'PASS', 'FAIL', 'DEFER', 'STALE'] = ''
    rationale_status: Literal['', 'UNRESOLVED', 'MISSING', 'DRAFT', 'CONFIRMED', 'STALE'] = ''


class Records(Page, Pinned):
    search: str = Field(default='', max_length=100)
    group_id: ID | None = None


class Group(Pinned):
    group_id: ID


class Record(Pinned):
    record_id: str = Field(min_length=1, max_length=128, strict=True)
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=32768)] = 16384


class Reason(Pinned):
    reason: str = Field(min_length=1, max_length=2000)


def parsed(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise ReviewError('INVALID_REQUEST', 'Invalid CLI command') from None


class CliReviews:
    def __init__(self, ws):
        self.ws, self.db = ws, ws.db

    def context(self, user_id, block_id, version_id, report_id):
        return WorkspaceReviewStore(self.db, ws=self.ws).context(
            user_id, block_id, version_id, report_id, write=True, hydrate_source=False)

    def create(self, user_id, block_id, version_id, report_id):
        require_schema(self.db)
        context = self.context(user_id, block_id, version_id, report_id)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(PREFIX+user_id+':'+report_id),))
        check(len(self.sessions(user_id, block_id, version_id, report_id)['items']) < 8, 'CLI_REVIEW_SESSION_LIMIT')
        sid, secret = str(uuid4()), secrets.token_urlsafe(32)
        expires = int(utcnow().timestamp()) + SESSION_SECONDS
        value = {'namespace': PROTOCOL, 'schema_version': 1, 'id': sid, 'user_id': user_id,
                 'block_id': block_id, 'version_id': version_id, 'report_id': report_id,
                 'source_sha256': context['source_sha256'], 'signing_key': signing_key(secret),
                 'created_at': utcnow().isoformat(), 'expires_at': expires}
        self.ws.put('WS_CONFIG', PREFIX+sid, value)
        return {**self.public(value), 'secret': secret}

    @staticmethod
    def public(value):
        return {k: v for k, v in value.items() if k not in ('signing_key', 'namespace', 'schema_version')}

    def sessions(self, user_id, block_id, version_id, report_id):
        self.context(user_id, block_id, version_id, report_id)
        rows = self.db.execute("SELECT s.payload FROM {oa} s WHERE s.record_type='WS_CONFIG' "
            "AND s.payload->>'namespace'=%s AND s.payload->>'user_id'=%s AND s.payload->>'report_id'=%s "
            "AND s.payload->'expires_at'>to_jsonb(extract(epoch FROM clock_timestamp())) "
            "AND NOT EXISTS (SELECT 1 FROM {oa} r WHERE r.record_type='WS_CONFIG' "
            "AND r.workspace_key=%s||(s.payload->>'id')) ORDER BY s.created_at DESC",
            (PROTOCOL, user_id, report_id, REVOKED)).fetchall()
        return {'items': [{**self.public(row['payload']), 'revoked': False}
                          for row in rows]}

    def revoked(self, sid):
        return self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                               (REVOKED+sid,)).fetchone() is not None

    def revoke(self, user_id, block_id, version_id, report_id, sid):
        identifier(sid)
        self.context(user_id, block_id, version_id, report_id)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(PREFIX+sid),))
        value = self.ws.get('WS_CONFIG', PREFIX+sid)['payload']
        check((value['user_id'], value['block_id'], value['version_id'], value['report_id']) ==
              (user_id, block_id, version_id, report_id), 'NOT_FOUND')
        if not self.revoked(sid):
            self.ws.put('WS_CONFIG', REVOKED+sid, {'namespace': PROTOCOL+'.revocation',
                'session_id': sid, 'actor_id': user_id, 'at': utcnow().isoformat()})
        return {'id': sid, 'revoked': True}

    def execute(self, envelope):
        value = validate_envelope(envelope)
        sid = value['session_id']
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(PREFIX+sid),))
        row = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                              (PREFIX+sid,)).fetchone()
        check(row is not None, 'CLI_REVIEW_AUTH_REQUIRED')
        session = row['payload']
        check(session['namespace'] == PROTOCOL and hmac.compare_digest(
              signature(session['signing_key'], value), value['signature']), 'CLI_REVIEW_AUTH_REQUIRED')
        check(session['expires_at'] == value['expires_at'], 'CLI_REVIEW_AUTH_REQUIRED')
        alive(session['expires_at'])
        check(not self.revoked(sid), 'CLI_REVIEW_REVOKED')
        target = tuple(session[k] for k in ('user_id', 'block_id', 'version_id', 'report_id'))
        context = self.context(*target)
        check(context['source_sha256'] == session['source_sha256'], 'CLI_REVIEW_SOURCE_CHANGED')
        if value['command']['action'] == 'CLOSE':
            parsed(Empty, value['command']['args'])
            return self.revoke(*target, sid)
        # Coherent pages and edits use the workbench's existing write lock.
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key('workspace-review-write'),))
        return self.dispatch(target, value['request_id'], value['command'])

    def dispatch(self, target, rid, command):
        action, args = command['action'], command['args']
        pipeline = WorkspacePipelineStore(self.db, ws=self.ws)
        if action == 'STATUS':
            parsed(Empty, args)
            return pipeline.view_paged(*target)
        if action in ('GROUPS', 'RECORDS', 'HISTORY'):
            model, method = {'GROUPS': (Groups, pipeline.groups_page), 'RECORDS': (Records, pipeline.records_page),
                             'HISTORY': (Page, pipeline.history_page)}[action]
            return method(*target, **parsed(model, args).model_dump())
        if action == 'RECORD':
            body = parsed(Record, args)
            context = pipeline.context(*target)
            pipeline.selected_event(context, body.expected_revision)
            record = next((r for r in context['report']['records'] if r['id'] == body.record_id), None)
            check(record is not None, 'NOT_FOUND')
            raw = record.get('raw', '')
            return {'record': {k: v for k, v in record.items() if k != 'raw'},
                    'raw': raw[body.offset:body.offset+body.limit], 'offset': body.offset,
                    'total': len(raw), 'limit': body.limit, 'offset_unit': 'unicode_characters',
                    'source_sha256': context['source_sha256'], 'cluster_revision': body.expected_revision}
        if action == 'GROUP':
            body = parsed(Group, args)
            context = pipeline.context(*target)
            pipeline.selected_event(context, body.expected_revision)
            review = WorkspaceGroupReviewStore(self.db, body.group_id, ws=self.ws).view(*target)
            from oa.sanity_ai_evidence_choices import EvidenceChoices
            choices = {}
            for stage in ('CLUSTER', 'REVIEW'):
                try:
                    choices[stage] = EvidenceChoices(self.db, self.ws).view(*target, body.group_id, stage)
                except ReviewError as error:
                    if error.code != 'AI_EVIDENCE_NOT_READY':
                        raise
                    choices[stage] = None
            return {'group_id': body.group_id, 'cluster_revision': body.expected_revision,
                'review': compact_review({k: v for k, v in review.items() if k not in ('cases', 'history')}),
                'rationale': pipeline.rationale_detail(*target, body.group_id, expected_revision=body.expected_revision),
                'evidence': choices}
        check('request_id' not in args, 'INVALID_REQUEST')
        if action == 'EDIT':
            body = parsed(ClusterEdit, {**args, 'request_id': rid})
            return event_brief(pipeline.change(*target, action='EDIT',
                operation=body.operation.model_dump(exclude_defaults=True, exclude_none=True),
                **body.model_dump(exclude={'operation'})))
        if action == 'RATIONALE':
            check(isinstance(args.get('group_id'), str), 'INVALID_REQUEST')
            group_id = args['group_id']
            body = parsed(RationaleChange, {**{k: v for k, v in args.items() if k != 'group_id'}, 'request_id': rid})
            operation = {'kind': body.action, 'group_id': group_id}
            if body.action == 'SAVE':
                operation.update(content=body.content.model_dump(mode='json', exclude_none=True) if body.content else None,
                                 reviewed=body.reviewed)
            else:
                check(body.content is None and not body.reviewed, 'INVALID_REQUEST')
            return event_brief(pipeline.change(*target, action='EDIT', expected_revision=body.expected_revision,
                request_id=rid, reason=body.reason, rationale_operation=operation))
        if action == 'CONFIRM':
            body = parsed(Reason, args)
            return event_brief(pipeline.change(*target, action='CONFIRM', request_id=rid, **body.model_dump()))
        if action == 'JUDGE':
            group_id = args.get('group_id')
            parsed(Group, {'group_id': group_id, 'expected_revision': args.get('expected_cluster_revision')})
            body = parsed(GroupReviewChange, {**{k: v for k, v in args.items() if k != 'group_id'}, 'request_id': rid})
            fields = body.model_dump(exclude={'expected_cluster_revision'})
            return compact_review(WorkspaceGroupReviewStore(self.db, group_id, body.expected_cluster_revision,
                ws=self.ws).save(*target, **fields))
        if action == 'EVIDENCE':
            from oa.ai_workbench_api import EvidenceChoice
            from oa.sanity_ai_evidence_choices import EvidenceChoices
            group_id, stage = args.get('group_id'), args.get('stage')
            check(isinstance(group_id, str) and stage in ('CLUSTER', 'REVIEW'), 'INVALID_REQUEST')
            body = parsed(EvidenceChoice, {**{k: v for k, v in args.items() if k not in ('group_id', 'stage')}, 'request_id': rid})
            return EvidenceChoices(self.db, self.ws).save(*target, group_id, stage, **body.model_dump())
        check(False, 'INVALID_REQUEST')


def receive_one(db, workspace_factory):
    require_schema(db)
    requests, responses = Queue(db, 'vwp_to_oa'), Queue(db, 'oa_to_vwp')
    message = requests.claim(['CLI_REVIEW_REQUEST'])
    if message is None:
        return False
    envelope = message['payload']
    with db.transaction():
        db.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        from oa.sanity_access import access_lock
        access_lock(db)
        response = {'schema_version': 1, 'protocol': PROTOCOL, 'request_id': envelope['request_id'],
                    'session_id': envelope['session_id'], 'request_sha256': digest(envelope)}
        try:
            with db.transaction():
                result = CliReviews(workspace_factory(db)).execute(envelope)
                response.update(state='DONE', result=result)
                check(len(canonical_json(response)) <= MAX_RESPONSE, 'CLI_REVIEW_LIMIT')
        except ReviewError as error:
            response = {k: v for k, v in response.items() if k not in ('state', 'result')}
            response.update(state='ERROR', code=error.code if error.code in ERRORS else 'CLI_REVIEW_FAILED')
        responses.enqueue({'message_id': response_key(envelope['request_id']), 'message_type': 'CLI_REVIEW_RESPONSE',
            'project': PROJECT, 'run_id': None, 'bundle_id': None, 'payload': response,
            'expires_at': datetime.fromtimestamp(envelope['expires_at'], timezone.utc)})
        requests.finish(message)
    return True
