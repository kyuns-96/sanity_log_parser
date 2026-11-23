"""Read-only helpers for explicit human opt-in to shared AI knowledge."""
import re

from common.sanity_clustering import check
from common.sanity_common import digest
from oa.sanity_workspace_core import record_id

NAMESPACE = 'ai-evidence-choice-v1'
PREFIX = 'ai.evidence.'
REQUEST = 'ai.evidence.request.'


def prefix(stage, report_id):
    check(stage in ('CLUSTER', 'REVIEW'), 'INVALID_REQUEST')
    # Two 128-bit components keep the immutable key within the existing
    # workspace_key varchar(100), without changing any DB definition.
    return PREFIX + digest([stage, report_id])[:32] + '.'


def binding(event, stage, group_id):
    if stage == 'REVIEW':
        return digest([stage, event['source_sha256'], event['id']])
    group = next((g for g in event['groups'] if g['id'] == group_id), None)
    check(group is not None, 'NOT_FOUND')
    return digest([stage, event['source_sha256'], group, event.get('rationales', {}).get(group_id, {}).get('content')])


def selected_case(event, stage, choices):
    groups = {g['id'] for g in event.get('groups', [])} if stage == 'CLUSTER' else {event.get('group_id')}
    selected = [row for row in choices if row['enabled'] and row['group_id'] in groups and
                row['binding_sha256'] == binding(event, stage, row['group_id'])]
    if not selected:
        return None
    result = dict(event)
    if stage == 'CLUSTER':
        ids = {row['group_id'] for row in selected}
        result['groups'] = [g for g in event['groups'] if g['id'] in ids]
        result['rationales'] = {key: row for key, row in event.get('rationales', {}).items() if key in ids}
        # Whole-report notes can describe groups the user did not select.
        result['reason'] = '사용자가 AI 참고 근거로 등록한 그룹의 분류 기준입니다.'
        result['criteria'] = ''
        refs = {ref['case_id'] for row in result['rationales'].values()
                for ref in row['content']['reference_groups']}
        result['references'] = [ref for ref in event.get('references', []) if ref['id'] in refs]
    else:
        # A judgment's saved snapshot also contains its classification reason.
        # Judgment opt-in must not implicitly publish that separate criterion.
        result.pop('clustering_rationale', None)
    result['evidence_selection_sha256'] = digest(sorted(row['sha256'] for row in selected))
    return result


def choices_for_reports(db, stage, report_ids):
    stems = {report_id: prefix(stage, report_id) for report_id in report_ids}
    if not stems:
        return {}
    conditions = ' OR '.join('workspace_key LIKE %s' for _ in stems)
    rows = db.execute("SELECT DISTINCT ON (payload->>'report_id',payload->>'group_id') * FROM {oa} WHERE "
        "record_type='WS_CONFIG' AND (" + conditions + ") "
        "ORDER BY payload->>'report_id',payload->>'group_id',workspace_key DESC",
        tuple(stem + '%' for stem in stems.values())).fetchall()
    result = {report_id: [] for report_id in stems}
    for row in rows:
        value = row['payload']
        check(isinstance(value, dict) and isinstance(value.get('report_id'), str)
              and value['report_id'] in stems, 'AI_EVIDENCE_CORRUPT')
        report_id = value['report_id']; stem = stems[report_id]
        keys = {'namespace', 'stage', 'report_id', 'group_id', 'event_id', 'binding_sha256', 'enabled',
                'revision', 'actor_id', 'at', 'sha256'}
        check(isinstance(value, dict) and set(value) == keys and value['namespace'] == NAMESPACE
            and value['stage'] == stage and value['report_id'] == report_id
            and (value['group_id'] is None or isinstance(value['group_id'], str))
            and isinstance(value['event_id'], str) and isinstance(value['actor_id'], str)
            and isinstance(value['at'], str) and type(value['enabled']) is bool
            and isinstance(value['binding_sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['binding_sha256'])
            and type(value['revision']) is int and 1 <= value['revision'] <= 9999999999
            and row['workspace_key'] == stem + digest(value['group_id'])[:32] + f".{value['revision']:010d}"
            and row['record_id'] == record_id('WS_CONFIG', row['workspace_key'])
            and row['state'] == {} and row['version'] == 0 and row['status'] == 'RECORDED'
            and row['created_at'] == row['updated_at']
            and value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'}), 'AI_EVIDENCE_CORRUPT')
        result[report_id].append(value)
    return result
