"""Immutable batch-job ledger and round progression helpers."""
from __future__ import annotations

import base64
import re

from common.sanity_ai_projection import policy_for, source_policy_pin
from common.sanity_ai_retry import valid_receipt
from common.sanity_common import ReviewError, digest, pack_json, unpack_json
from common.sanity_preserved_ai import available, model_metadata, receipt
from common.sanity_submission import check
from common.sanity_validation_diagnostics import sanitize_validation_failure
from oa.sanity_ai_batch_model import BatchModel, VERSION
from oa.sanity_ai_evidence_batch import EvidenceBatchModel
from oa.sanity_ai_live import capture as live_capture
from oa.sanity_ai_store import NULL_COLUMNS
from oa.sanity_workspace_core import record_id

NAMESPACE = 'oa-ai-batch-v1'
MAX_BYTES = 64 * 1024 * 1024


def attempts(state):
    values = [state.get('calls'), state.get('failed_calls', 0), state.get('unknown_calls', 0)]
    check(all(type(value) is int and 0 <= value <= 100000 for value in values), 'AI_BATCH_CORRUPT')
    return sum(values)


class Ledger:
    def __init__(self, service, parent):
        self.service, self.db, self.parent = service, service.db, parent
        self.prefix = 'ai2.' + parent['job_id'] + '.'
        self.binding = digest([parent['job_id'], parent['data']['command'], parent['data']['pins']])

    def put(self, key, body):
        packed = pack_json(body, max_bytes=MAX_BYTES)
        packed['payload_gzip'] = base64.b64encode(packed['payload_gzip']).decode('ascii')
        value = {'namespace': NAMESPACE, 'version': 1, 'job_id': self.parent['job_id'],
                 'binding': self.binding, 'key': key, 'packed': packed}
        value['sha256'] = digest(value)
        self.service.ws.put('WS_CONFIG', self.prefix + key, value)
        return {'key': key, 'sha256': value['sha256']}

    def decode(self, row):
        value = row['payload']
        check(isinstance(value, dict) and set(value) == {'namespace', 'version', 'job_id', 'binding', 'key', 'packed', 'sha256'}
              and value['namespace'] == NAMESPACE and type(value['version']) is int and value['version'] == 1
              and value['job_id'] == self.parent['job_id'] and value['binding'] == self.binding
              and isinstance(value['key'], str) and row['workspace_key'] == self.prefix + value['key']
              and row['record_id'] == record_id('WS_CONFIG', row['workspace_key'])
              and row['state'] == {} and row['version'] == 0 and row['status'] == 'RECORDED'
              and row['updated_at'] == row['created_at'] and all(row.get(k) is None for k in NULL_COLUMNS)
              and value['sha256'] == digest({k: v for k, v in value.items() if k != 'sha256'}), 'AI_BATCH_CORRUPT')
        try:
            body = unpack_json({**value['packed'], 'payload_gzip': base64.b64decode(value['packed']['payload_gzip'], validate=True)}, max_bytes=MAX_BYTES)
        except (ReviewError, KeyError, TypeError, ValueError):
            raise ReviewError('AI_BATCH_CORRUPT', 'Invalid immutable batch data') from None
        return body, {'key': value['key'], 'sha256': value['sha256']}

    def get(self, key, expected=None):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (self.prefix + key,)).fetchone()
        check(row is not None, 'AI_BATCH_CORRUPT')
        body, ref = self.decode(row)
        check(expected is None or ref == expected, 'AI_BATCH_CORRUPT')
        return body, ref

    def rows(self, prefix):
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key",
                               (self.prefix + prefix + '%',)).fetchall()
        return [self.decode(r) for r in rows]

    def last(self):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key DESC LIMIT 1",
                              (self.prefix + 'step.%',)).fetchone()
        return self.decode(row) if row else (None, None)

    def step(self, previous, previous_ref, status, state, **extra):
        index = previous['sequence'] + 1 if previous else 1
        body = {'sequence': index, 'previous_sha256': previous_ref['sha256'] if previous_ref else None,
                'status': status, 'state': state, **extra}
        ref = self.put('step.' + str(index).zfill(8), body)
        live_capture(self.service, self.parent)
        return body, ref

    def plans(self, round_number, descriptors, input_count):
        # One compressed immutable round retains every descriptor. Separate
        # rows repeat the parent binding, envelope and DB indexes for each leaf.
        # Reservations still hash the exact descriptor and actual HTTP body.
        return self.put(f'round.{round_number:03d}', {'plan_format': 2,
            'count': len(descriptors), 'input_nodes': input_count, 'descriptors': descriptors})

    def round(self, round_number):
        expected = self.parent['data']['batch']['plan'] if round_number == 0 else None
        manifest, _ = self.get(f'round.{round_number:03d}', expected)
        check(isinstance(manifest, dict), 'AI_BATCH_CORRUPT')
        if 'plan_format' in manifest:
            check(set(manifest) == {'plan_format', 'count', 'input_nodes', 'descriptors'}
                  and type(manifest['plan_format']) is int and manifest['plan_format'] == 2,
                  'AI_BATCH_CORRUPT')
            entries = manifest['descriptors']
        else:
            # Checkpoint jobs retain their original rows and receipt hashes.
            check(set(manifest) == {'count', 'input_nodes', 'plans'}, 'AI_BATCH_CORRUPT')
            entries = manifest['plans']
        check(type(manifest['count']) is int and manifest['count'] > 0
              and type(manifest['input_nodes']) is int and manifest['input_nodes'] > 0
              and isinstance(entries, list) and len(entries) == manifest['count']
              and all(isinstance(entry, dict) for entry in entries), 'AI_BATCH_CORRUPT')
        return manifest

    def descriptor(self, state):
        manifest = self.round(state['round'])
        index = state['index']
        check(type(index) is int and 0 <= index < manifest['count'], 'AI_BATCH_CORRUPT')
        if 'descriptors' in manifest:
            return manifest['descriptors'][index]
        ref = manifest['plans'][index]
        return self.get(ref['key'], ref)[0]

    def nodes(self, refs):
        results = {}
        values = []
        for ref in refs:
            key = ref['result']['key']
            if key not in results: results[key] = self.get(key, ref['result'])[0]
            rows = results[key]['nodes']; index = ref['index']
            check(type(index) is int and 0 <= index < len(rows) and rows[index]['id'] == ref['id'], 'AI_BATCH_CORRUPT')
            values.append(rows[index])
        return values

    def completed_round(self, round_number):
        nodes, refs = [], []
        for result, result_ref in self.rows(f'result.{round_number:03d}.'):
            for i, node in enumerate(result['nodes']):
                nodes.append(node); refs.append({'result': result_ref, 'index': i, 'id': node['id']})
        check(len({n['id'] for n in nodes}) == len(nodes), 'AI_BATCH_CORRUPT')
        return nodes, refs

    def audit(self):
        previous = None; sequence = 0; pending = None
        for step, ref in self.rows('step.'):
            check(step['sequence'] == sequence + 1 and step['previous_sha256'] == previous
                  and step['status'] in ('READY', 'RESERVED'), 'AI_BATCH_CORRUPT')
            previous = ref['sha256']; sequence += 1
            if step['status'] == 'RESERVED':
                check(pending is None and isinstance(step.get('input_sha256'), str), 'AI_BATCH_CORRUPT')
                pending = step
            if step['status'] == 'READY' and step.get('result'):
                result = self.get(step['result']['key'], step['result'])[0]
                if result['metadata'] is not None:
                    check(pending is not None and result['metadata']['input_sha256'] == pending['input_sha256'], 'AI_BATCH_CORRUPT')
                    pending = None
                else: check(pending is None, 'AI_BATCH_CORRUPT')
            if step['status'] == 'READY' and 'unsent' in step:
                check(pending is not None and step['unsent'] == {'call_token': pending['call_token'],
                      'input_sha256': pending['input_sha256'], 'error': 'AI_CONNECT_FAILED'}, 'AI_BATCH_CORRUPT')
                pending = None
            if step['status'] == 'READY' and 'failure' in step:
                failure = step['failure']
                check(pending is not None and isinstance(failure, dict)
                    and {'call_token', 'input_sha256', 'receipt'} <= set(failure) <= {'call_token', 'input_sha256', 'receipt', 'validation_failure'}
                    and failure['call_token'] == pending['call_token'] and failure['input_sha256'] == pending['input_sha256']
                    and valid_receipt(failure['receipt']), 'AI_BATCH_CORRUPT')
                if 'validation_failure' in failure:
                    diagnostic = failure['validation_failure']
                    check(diagnostic is not None and failure['receipt']['error'] == 'INVALID_RATIONALE'
                          and sanitize_validation_failure(diagnostic) == diagnostic, 'AI_BATCH_CORRUPT')
                pending = None
            if step['status'] == 'READY' and 'unknown_resolution' in step:
                resolution = step['unknown_resolution']
                check(pending is not None and isinstance(resolution, dict)
                    and set(resolution) == {'actor_id', 'request_id', 'event_sha256', 'step_sha256', 'call_token', 'input_sha256', 'acknowledge_duplicate'}
                    and resolution['actor_id'] == self.parent['data']['command']['user_id']
                    and isinstance(resolution['request_id'], str) and re.fullmatch(r'[0-9a-f-]{36}', resolution['request_id'])
                    and isinstance(resolution['event_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', resolution['event_sha256'])
                    and resolution['step_sha256'] == step['previous_sha256']
                    and resolution['call_token'] == pending['call_token'] and resolution['input_sha256'] == pending['input_sha256']
                    and resolution['acknowledge_duplicate'] is True, 'AI_BATCH_CORRUPT')
                pending = None
        check(pending is None, 'AI_BATCH_CORRUPT')
        return previous


def build_batch_model(advisor, event, context, cases, criteria):
    if event['data']['batch']['version'] == 2:
        return EvidenceBatchModel(advisor, context, cases, event['data']['pins']['identity_fields'], criteria)
    return BatchModel(advisor, context, cases, event['data']['pins']['identity_fields'])


def record_plan(model):
    kind = 'REVIEW_LEAF' if getattr(model, 'review', False) else 'LEAF'
    return [{'kind': kind, **r} for r in model.leaf_plan()]


def bind_preserved(model, ledger, state):
    if state.get('preserved_ref'):
        model.bind_preserved(ledger.nodes([state['preserved_ref']])[0])


def _text_round(ledger, model, step, ref, nodes, node_refs):
    state = step['state']; number = state['round'] + 1
    if len(nodes) == 1:
        model.bind_preserved(nodes[0])
        descriptors = record_plan(model)
        ledger.plans(number, descriptors, len(model.records))
        ledger.step(step, ref, 'READY', {**state, 'round': number, 'index': 0, 'nodes': 0, 'stage_round': 0,
            'round_total': len(descriptors), 'round_seconds': 0, 'round_samples': 0,
            'phase': 'REVIEW' if model.review else 'CLASSIFY', 'preserved_ref': node_refs[0],
            'current_groups': 1 if model.review else 0})
    else:
        check(state['stage_round'] < model.settings.max_rounds, 'AI_BATCH_LIMIT')
        groups = model.bounded_groups(nodes, text=True); by_id = {r['id']: r for r in node_refs}
        descriptors = [{'kind': 'TEXT_MERGE', 'nodes': g, 'node_refs': [by_id[k] for k in g]} for g in groups]
        ledger.plans(number, descriptors, len(nodes))
        ledger.step(step, ref, 'READY', {**state, 'round': number, 'index': 0, 'nodes': 0,
                                    'round_total': len(descriptors), 'round_seconds': 0, 'round_samples': 0,
                                    'stage_round': state['stage_round'] + 1, 'phase': 'TEXT_MERGE'})


def finish_round(service, event, ledger, model, step, ref):
    state = step['state']; round_number = state['round']
    nodes, node_refs = ledger.completed_round(round_number)
    manifest = ledger.round(round_number)
    check(nodes and len(nodes) == state['nodes'], 'AI_BATCH_CORRUPT')
    if state.get('phase') in ('TEXT', 'TEXT_MERGE'):
        _text_round(ledger, model, step, ref, nodes, node_refs)
        return event
    stage_round = state.get('stage_round', round_number)
    stable = state['stable'] + 1 if stage_round > 0 and len(nodes) == manifest['input_nodes'] else 0
    review = getattr(model, 'review', False)
    converged = len(nodes) == 1 or (not review and (stable >= 2 or model.rules_complete(nodes)))
    capped = not review and stage_round >= model.settings.max_rounds
    if converged or capped:
        check(state['records'] == len(model.records), 'INVALID_PARTITION')
        advice, citations = model.advice(nodes)  # Existing full-original partition and rationales.
        chain = ledger.audit()
        metadata = {**model.advisor.describe(), 'model': state['actual_model'], 'requested_model': model.advisor.settings.model,
            'prompt_version': getattr(model, 'version', VERSION), 'model_called': True, 'input_sha256': digest([event['data']['batch']['plan'], chain]),
            'input_hash_kind': 'BATCH_MANIFEST', 'output_sha256': digest(advice), 'batch_receipt': {
                'version': 1, 'root': chain, 'calls': state['calls'], 'records': state['records'],
                'plan': event['data']['batch']['plan']}}
        if capped and not converged: metadata['completion_reason'] = 'MAX_ITERATIONS'
        if not review: metadata['prompt_version'] += '.rule-code-v1'
        if policy_for(model.context):
            metadata['prompt_version'] += '.fields-v1'
            metadata.update(source_policy_pin(model.context))
        usage = {}; routing = {'primary_calls': 0, 'fallback_calls': 0, 'connect_failures': 0}
        for result, _ in ledger.rows('result.'):
            for key, value in (result['metadata'] or {}).get('usage', {}).items(): usage[key] = usage.get(key, 0) + value
            route = (result['metadata'] or {}).get('routing')
            if route:
                routing[route['endpoint'] + '_calls'] += 1
                routing['connect_failures'] += len(route['attempted_endpoints']) - 1
        metadata['usage'] = usage
        if model.advisor.settings.fallback_endpoint: metadata['routing_summary'] = routing
        metadata = model_metadata(metadata, citations if available(model.context) else None, advice, model.context)
        if event['data']['batch']['credential_revision'] is not None:
            metadata['credential_revision'] = event['data']['batch']['credential_revision']
        receipt(metadata, advice, model.context)
        # Recover by immutable node references, not a second full ID assignment
        # snapshot; apply_ai owns the sole published clustering event.
        final = ledger.put('final', {'nodes': node_refs, 'metadata': metadata})
        event = service._append(event, 'RESULT_READY', {**event['data'], 'result_ref': final, 'model_called': True, 'model_attempted': True,
            **({'completion_reason': 'MAX_ITERATIONS'} if capped and not converged else {})})
        return event
    check(stage_round < model.settings.max_rounds, 'AI_BATCH_LIMIT', 'Reduction has not stabilized within its call budget')
    groups = model.merge_plan(nodes, stage_round + 1)
    by_id = {r['id']: r for r in node_refs}
    descriptors = [{'kind': 'REVIEW_MERGE' if review else 'MERGE', 'nodes': g, 'node_refs': [by_id[k] for k in g]} for g in groups]
    ledger.plans(round_number + 1, descriptors, len(nodes))
    extra = {'stage_round': stage_round + 1, 'phase': 'REVIEW_MERGE' if review else 'MERGE'} if 'phase' in state else {}
    ledger.step(step, ref, 'READY', {**state, 'round': round_number + 1, 'index': 0, 'nodes': 0, 'stable': stable,
                                'round_total': len(descriptors), 'round_seconds': 0, 'round_samples': 0,
                                'current_groups': 1 if review else len(nodes), **extra})
    return event
