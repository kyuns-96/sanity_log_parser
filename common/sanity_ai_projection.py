"""Explicit item input selection. Full source records remain server-side.

An absent declaration is the legacy wire contract. A declared policy selects
fields, never records, and is versioned with the immutable source/job pins.
"""
from copy import deepcopy
import re

from common.sanity_common import ReviewError, digest

VERSION = 'ai-input-fields-v1'
KEYS = ('ai_fields', 'include_raw', 'include_details')
_SERVER_KEYS = frozenset(('evidence_id', 'record_id', 'source_raw_sha256',
                          'raw_sha256', 'source_sha256', 'sha256'))


def require(condition, code='AI_INPUT_POLICY'):
    if not condition:
        raise ReviewError(code, 'Invalid AI input selection or evidence alias')


def declaration(value, *, code='AI_INPUT_POLICY'):
    require(isinstance(value, dict), code)
    if not any(key in value for key in KEYS):
        return None
    fields = value.get('ai_fields')
    if 'ai_fields' in value:
        require(isinstance(fields, list) and 1 <= len(fields) <= 256, code)
        require(all(isinstance(key, str) and 1 <= len(key) <= 100
                    and not re.search(r'[\s\x00-\x1f\x7f]', key) for key in fields), code)
        require(len(set(fields)) == len(fields), code)
        fields = sorted(fields)
    for key in ('include_raw', 'include_details'):
        require(key not in value or type(value[key]) is bool, code)
    return {'version': VERSION, 'ai_fields': fields,
            'include_raw': value.get('include_raw', True),
            'include_details': value.get('include_details', True)}


def policy_for(context):
    return declaration(context['report'])


def source_policy_pin(context):
    policy = policy_for(context)
    return {'ai_input_policy_sha256': digest(policy)} if policy else {}


def project_rows(rows, policy):
    if policy is None:
        return rows
    require(isinstance(policy, dict) and policy.get('version') == VERSION)
    selected = policy['ai_fields']
    result = []
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('fields'), dict)
                and isinstance(row.get('id'), str))
        fields = {key: value for key, value in row['fields'].items()
                  if selected is None or key in selected}
        # Missing keys remain missing; null/false/empty values remain explicit.
        require(bool(fields) or policy['include_raw'] and bool(row.get('raw')))
        projected = ({key: value for key, value in row.items()
                      if key not in _SERVER_KEYS and key not in ('fields', 'raw')}
                     if policy['include_details'] else {'id': row['id']})
        projected['fields'] = fields
        if policy['include_raw'] and 'raw' in row:
            projected['raw'] = row['raw']
        result.append(projected)
    return result


def instruction(policy, prompt, version):
    if policy is None:
        return prompt, version
    return (prompt + '\n'
            'ai_input_policy는 이 item에 선언된 AI 입력 선택 정책입니다. '
            '모든 레코드의 제공된 필드를 검토하되, 생략된 필드·raw·부가정보를 읽었다고 주장하지 마세요. '
            '짧은 r0/r1 ID는 이번 요청의 원본 식별자이며 서버가 실제 원본에 연결합니다. '
            '누락된 정보나 동일성 속성의 값을 추측하지 마세요. 분류에는 미확인 조건을 설명하고, '
            '판정에 필요한 근거가 부족하면 DEFER와 필요한 정보를 명시하세요. '
            'clustering_rationale_evidence_scope가 있으면 분류 근거 ID 목록은 현재 요청 범위로 제한된 것입니다. '
            '빠진 ID의 원문을 이번 요청에서 읽었다거나 그 원문이 존재하지 않는다고 추정하지 마세요.\n',
            version + '.fields-v1')


def alias_rows(rows):
    ids = [row['id'] for row in rows]
    require(len(set(ids)) == len(ids), 'INVALID_ADVICE')
    aliases = {'r' + str(index): key for index, key in enumerate(ids)}
    return ([{**row, 'id': alias} for alias, row in zip(aliases, rows)], aliases)


def restore_ids(ids, aliases):
    require(isinstance(ids, list) and all(isinstance(key, str) and key in aliases for key in ids)
            and len(set(ids)) == len(ids), 'INVALID_ADVICE')
    return [aliases[key] for key in ids]


def restore_clustering(value, aliases):
    result = deepcopy(value)
    for group in result['groups']:
        group['record_ids'] = restore_ids(group['record_ids'], aliases)
    for rationale in result.get('group_rationales', []):
        rationale['evidence_ids'] = restore_ids(rationale['evidence_ids'], aliases)
    return result


def restore_review(value, aliases):
    return {**value, 'evidence_ids': restore_ids(value['evidence_ids'], aliases)}
