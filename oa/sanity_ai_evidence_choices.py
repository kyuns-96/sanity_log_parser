"""Explicit human opt-in for shared AI knowledge, separate from review history."""
from common.sanity_clustering import check
from common.sanity_common import digest
from common.sanity_db import lock_key, uuid_value
import common.sanity_workspace_rationale as rationale
from oa.sanity_ai_evidence_reader import (
    NAMESPACE, PREFIX, REQUEST, binding, choices_for_reports, prefix, selected_case,
)
from oa.sanity_workspace_clustering import WorkspaceClusteringStore, ai_job_active
from oa.sanity_workspace_core import WorkspaceCoreStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.sanity_workspace_review import WorkspaceReviewStore


class EvidenceChoices:
    def __init__(self, db, ws=None):
        self.db, self.ws = db, ws if ws is not None else WorkspaceCoreStore(db)

    def rows(self, stage, report_id):
        return self.for_reports(stage, [report_id])[report_id]

    def for_reports(self, stage, report_ids):
        return choices_for_reports(self.db, stage, report_ids)

    def _target(self, user_id, block_id, version_id, report_id, group_id, stage, *, write=False):
        check(stage in ('CLUSTER', 'REVIEW'), 'INVALID_REQUEST')
        context = WorkspaceReviewStore(self.db, self.ws).context(user_id, block_id, version_id, report_id,
                                                                 write=write, hydrate_source=False)
        cluster_store = WorkspaceClusteringStore(self.db, self.ws)
        cluster, _ = cluster_store.current(report_id) if group_id is not None else (None, 0)
        if group_id is not None:
            check(cluster and any(g['id'] == group_id for g in cluster['groups']), 'NOT_FOUND')
            check(cluster['source_sha256'] == context['source_sha256'], 'STATE_CORRUPT')
        if stage == 'CLUSTER':
            check(group_id is not None, 'INVALID_REQUEST')
            event = cluster
            group = next(g for g in cluster['groups'] if g['id'] == group_id)
            reviewed = rationale.state(cluster, group) == 'CONFIRMED'
            head = cluster_store.reviews.row('WS_CLUSTER', report_id)
            can_enable = bool(context['ready'] and reviewed and not ai_job_active(head)
                              and not cluster_store.stale(cluster, context))
            eligible = bool(can_enable and cluster['confirmed'] and rationale.complete(cluster))
        else:
            if group_id is not None:
                event, _ = WorkspaceGroupReviewStore(self.db, group_id, ws=self.ws).current(report_id)
            else:
                event, _ = WorkspaceReviewStore(self.db, self.ws).current(report_id)
            check(event is not None, 'AI_EVIDENCE_NOT_READY')
            check(event['source_sha256'] == context['source_sha256'], 'STATE_CORRUPT')
            eligible = bool(context['ready'] and event['conclusion'] in ('PASS', 'FAIL'))
            if group_id is not None:
                eligible = bool(eligible and cluster['confirmed'] and rationale.complete(cluster)
                    and event['cluster_event_id'] == cluster['id'] and event['membership_sha256'] == cluster['membership_sha256']
                    and not cluster_store.stale(cluster, context))
            can_enable = eligible
        return context, event, can_enable, eligible

    def view(self, user_id, block_id, version_id, report_id, group_id, stage):
        context, event, can_enable, eligible = self._target(user_id, block_id, version_id, report_id, group_id, stage)
        return self._view(context, event, can_enable, eligible, report_id, group_id, stage)

    def _view(self, context, event, can_enable, eligible, report_id, group_id, stage):
        fingerprint = binding(event, stage, group_id)
        choice = next((r for r in self.rows(stage, report_id) if r['group_id'] == group_id), None)
        enabled = bool(choice and choice['enabled'] and choice['binding_sha256'] == fingerprint)
        return {'stage': stage, 'group_id': group_id, 'revision': choice['revision'] if choice else 0,
            'binding_sha256': fingerprint, 'enabled': enabled, 'eligible': eligible,
            'can_manage': context['can_review'], 'can_enable': can_enable, 'active': enabled and eligible}

    def save(self, user_id, block_id, version_id, report_id, group_id, stage, *, enabled, expected_revision,
             binding_sha256, request_id):
        check(type(enabled) is bool and type(expected_revision) is int and 0 <= expected_revision < 9999999999,
              'INVALID_REQUEST')
        identity = str(uuid_value(request_id))
        command = {'actor_id': user_id, 'block_id': block_id, 'version_id': version_id, 'report_id': report_id,
                   'group_id': group_id, 'stage': stage, 'enabled': enabled, 'revision': expected_revision, 'binding': binding_sha256}
        self.ws.access(user_id, block_id, write=True)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key('workspace-review-write'),))
        context, event, can_enable, eligible = self._target(user_id, block_id, version_id, report_id, group_id, stage, write=True)
        old = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                              (REQUEST + identity,)).fetchone()
        if old:
            check(old['payload'].get('namespace') == NAMESPACE and old['payload'].get('command_sha256') == digest(command),
                  'REQUEST_CONFLICT')
            return self._view(context, event, can_enable, eligible, report_id, group_id, stage)
        current = self._view(context, event, can_enable, eligible, report_id, group_id, stage)
        check(current['revision'] == expected_revision, 'REVISION_CONFLICT')
        check(current['binding_sha256'] == binding_sha256, 'AI_EVIDENCE_CHANGED')
        check(not enabled or can_enable, 'AI_EVIDENCE_NOT_READY')
        at = self.db.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()
        value = {'namespace': NAMESPACE, 'stage': stage, 'report_id': report_id, 'group_id': group_id, 'event_id': event['id'],
                 'binding_sha256': binding_sha256, 'enabled': enabled, 'revision': expected_revision + 1, 'actor_id': user_id, 'at': at}
        value['sha256'] = digest(value)
        self.ws.put('WS_CONFIG', prefix(stage, report_id) + digest(group_id)[:32] + f".{value['revision']:010d}", value)
        self.ws.put('WS_CONFIG', REQUEST + identity, {'namespace': NAMESPACE, 'report_id': report_id,
            'command_sha256': digest(command)})
        return self._view(context, event, can_enable, eligible, report_id, group_id, stage)
