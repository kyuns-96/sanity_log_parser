"""Immutable bounded waves, independent call reservations and ordered results."""
import re

from common.sanity_common import ReviewError, digest
from common.sanity_submission import check
from oa.sanity_ai_batch_jobs import Ledger, attempts
from oa.sanity_ai_retry import valid_receipt


def parallel(event):
    return event['data'].get('batch', {}).get('execution') == 'parallel-v1'


class ParallelLedger(Ledger):
    def step(self, previous, previous_ref, status, state, **extra):
        if 'unknown_resolution' not in extra and 'resume' not in extra:
            return super().step(previous, previous_ref, status, state, **extra)
        # The parent transition is appended immediately afterward in this
        # transaction; publishing its old status with the new step would
        # expose a checkpoint not yet bound to its authorization event.
        index = previous['sequence'] + 1 if previous else 1
        body = {'sequence':index, 'previous_sha256':previous_ref['sha256'] if previous_ref else None,
                'status':status, 'state':state, **extra}
        return body, self.put('step.' + str(index).zfill(8), body)

    def last(self):
        try:
            return self._last()
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise ReviewError('AI_BATCH_CORRUPT','Malformed parallel checkpoint') from None

    def _last(self):
        step, ref = super().last()
        if step is None:
            return step, ref
        check(step.get('status') == 'READY' and isinstance(step.get('state'), dict), 'AI_BATCH_CORRUPT')
        state = step['state']; wave = state.get('wave', [])
        attempts(state)
        check(type(state.get('round')) is int and 0 <= state['round'] <= 999
              and type(state.get('round_total')) is int and 0 < state['round_total'] <= 1000000
              and isinstance(wave, list) and len(wave) <= self.parent['data']['batch']['max_concurrency']
              and all(isinstance(slot, dict) and type(slot.get('index')) is int
                      and 0 <= slot['index'] < state['round_total'] for slot in wave)
              and len({slot['index'] for slot in wave}) == len(wave), 'AI_BATCH_CORRUPT')
        for slot in wave:
            check(slot.get('status') in ('RESERVED','DONE','RETRY','FAILED','UNKNOWN'), 'AI_BATCH_CORRUPT')
            if 'reservation' not in slot:
                check(slot['status'] in ('RETRY','DONE') and slot.get('attempt', 0) == 0
                      and 'validation_failure' not in slot, 'AI_BATCH_CORRUPT')
                if slot['status'] == 'DONE':
                    result = slot.get('result')
                    check(isinstance(result,dict) and set(result)=={'key','sha256'}, 'AI_BATCH_CORRUPT')
                    self.get(result['key'],result)
                continue
            reservation = slot['reservation']
            check(isinstance(reservation,dict) and set(reservation)=={'key','sha256'}
                  and isinstance(reservation['key'],str) and isinstance(reservation['sha256'],str)
                  and re.fullmatch(r'[0-9a-f]{64}',reservation['sha256'])
                  and type(slot.get('attempt')) is int and 1 <= slot['attempt'] <= 100000
                  and reservation['key'] == f"call.{state['round']:03d}.{slot['index']:06d}.{slot['attempt']:03d}"
                  and isinstance(slot.get('call_token'),str) and re.fullmatch(r'[0-9a-f-]{36}',slot['call_token'])
                  and isinstance(slot.get('input_sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',slot['input_sha256'])
                  and type(slot.get('input_bytes')) is int and 0 < slot['input_bytes'] <= 67108864
                  and isinstance(slot.get('prompt_version'),str) and bool(slot['prompt_version'])
                  and isinstance(slot.get('deadline'),str), 'AI_BATCH_CORRUPT')
            from oa.sanity_ai_jobs import _timestamp
            _timestamp(slot['deadline'])
            call, call_ref = self.get(reservation['key'], reservation)
            check(call.get('kind') == 'RESERVATION' and call['call_token'] == slot.get('call_token')
                  and call['round'] == state['round'] and call['index'] == slot['index']
                  and call['input_sha256'] == slot.get('input_sha256')
                  and call['input_bytes'] == slot.get('input_bytes')
                  and call['prompt_version'] == slot.get('prompt_version')
                  and call['deadline'] == slot.get('deadline')
                  and call['attempt'] == slot.get('attempt'), 'AI_BATCH_CORRUPT')
            if slot['status'] != 'RESERVED':
                outcome, _ = self.get(call_ref['key'] + '.outcome')
                check(outcome.get('kind') == 'OUTCOME' and outcome.get('reservation') == call_ref
                      and outcome.get('call_token') == call['call_token'], 'AI_BATCH_CORRUPT')
                if slot['status'] == 'DONE':
                    check(outcome.get('status') == 'SUCCESS' and outcome.get('result') == slot.get('result'), 'AI_BATCH_CORRUPT')
                if slot['status'] == 'UNKNOWN':
                    check(outcome.get('status') == 'UNKNOWN', 'AI_BATCH_CORRUPT')
                if slot['status'] == 'FAILED':
                    check(outcome.get('status') in ('FAILURE','REJECTED'), 'AI_BATCH_CORRUPT')
                if slot['status'] == 'RETRY':
                    check(outcome.get('status') in ('FAILURE','UNKNOWN'), 'AI_BATCH_CORRUPT')
                    if outcome.get('status') == 'UNKNOWN':
                        self._require_unknown_ack(slot['call_token'])
                if 'receipt' in slot:
                    check(valid_receipt(slot['receipt']) and outcome.get('receipt') == slot['receipt'], 'AI_BATCH_CORRUPT')
                if 'validation_failure' in slot:
                    from common.sanity_validation_diagnostics import sanitize_validation_failure
                    diagnostic=slot['validation_failure']
                    check(slot['status'] in ('RETRY','FAILED') and slot.get('receipt',{}).get('error')=='INVALID_RATIONALE'
                          and diagnostic is not None and sanitize_validation_failure(diagnostic)==diagnostic
                          and outcome.get('validation_failure')==diagnostic,'AI_BATCH_CORRUPT')
                elif slot['status'] in ('RETRY','FAILED') and 'receipt' in slot:
                    check('validation_failure' not in outcome,'AI_BATCH_CORRUPT')
        return step, ref

    def _require_unknown_ack(self, token):
        # A later ordinary resume can replace resume_checkpoint before a
        # previously acknowledged child is dispatched. Inspect the bounded
        # parent resume history rather than only the latest checkpoint.
        from oa.sanity_ai_jobs import PREFIX
        verified = self.service._load(self.parent['job_id'])
        check(verified['sequence'] >= self.parent['sequence'], 'AI_BATCH_CORRUPT')
        rows = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                               "ORDER BY workspace_key", (PREFIX+self.parent['job_id']+'.%',)).fetchall()
        events = [row['payload'] for row in rows if row['payload']['sequence'] <= self.parent['sequence']]
        for before, event in zip(events, events[1:]):
            if before['status'] != 'UNKNOWN' or event['status'] != 'RUNNING':
                continue
            checkpoint = event['data'].get('resume_checkpoint')
            if not isinstance(checkpoint,dict):
                continue
            ack, ack_ref = self.get(checkpoint['key'],checkpoint)
            resolution = ack.get('unknown_resolution')
            if not isinstance(resolution,dict):
                continue
            if (resolution.get('actor_id') == self.parent['data']['command']['user_id']
                    and resolution.get('acknowledge_duplicate') is True
                    and resolution.get('event_sha256') == before['sha256']
                    and event['previous_sha256'] == before['sha256']
                    and ack_ref == checkpoint and isinstance(resolution.get('call_tokens'),list)
                    and token in resolution['call_tokens']):
                return
        check(False, 'AI_BATCH_CORRUPT', 'Unknown call has not been acknowledged')

    def audit(self, *, allow_unresolved=False):
        try:
            return self._audit(allow_unresolved=allow_unresolved)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise ReviewError('AI_BATCH_CORRUPT','Malformed parallel call history') from None

    def _audit(self, *, allow_unresolved):
        previous = None
        sequence = 0
        approved = set()
        previous_counts = (0, 0, 0)
        for step, ref in self.rows('step.'):
            check(step['sequence'] == sequence + 1 and step['previous_sha256'] == previous
                  and step['status'] == 'READY', 'AI_BATCH_CORRUPT')
            state = step['state']
            counts = (state['calls'], state.get('failed_calls', 0), state.get('unknown_calls', 0))
            attempts(state)
            check(all(a >= b for a, b in zip(counts, previous_counts)), 'AI_BATCH_CORRUPT')
            wave = state.get('wave', [])
            check(isinstance(wave, list) and len(wave) <= self.parent['data']['batch']['max_concurrency']
                  and len({slot['index'] for slot in wave}) == len(wave), 'AI_BATCH_CORRUPT')
            for slot in wave:
                check(slot['status'] in ('RESERVED','DONE','RETRY','FAILED','UNKNOWN'), 'AI_BATCH_CORRUPT')
                if 'reservation' in slot:
                    reservation, _ = self.get(slot['reservation']['key'], slot['reservation'])
                    check(reservation['call_token'] == slot['call_token']
                          and reservation['index'] == slot['index']
                          and reservation['round'] == state['round']
                          and reservation['input_sha256'] == slot['input_sha256'], 'AI_BATCH_CORRUPT')
                if slot['status'] == 'DONE' and slot.get('result'):
                    self.get(slot['result']['key'], slot['result'])
            resolution = step.get('unknown_resolution')
            if resolution:
                tokens = resolution.get('call_tokens')
                check(resolution.get('actor_id') == self.parent['data']['command']['user_id']
                      and resolution.get('acknowledge_duplicate') is True
                      and resolution.get('step_sha256') == step['previous_sha256']
                      and isinstance(tokens, list) and tokens and len(tokens) == len(set(tokens))
                      and not approved.intersection(tokens), 'AI_BATCH_CORRUPT')
                approved.update(tokens)
            sequence += 1
            previous = ref['sha256']
            previous_counts = counts
        check(previous is not None, 'AI_BATCH_CORRUPT')
        calls = {ref['key']:(body, ref) for body, ref in self.rows('call.')}
        success = failed = unknown = 0
        unknown_tokens = set()
        result_keys = set()
        for key, (call, call_ref) in calls.items():
            if call.get('kind') == 'OUTCOME':
                check(key.endswith('.outcome') and key[:-8] in calls, 'AI_BATCH_CORRUPT')
                continue
            check(call.get('kind') == 'RESERVATION', 'AI_BATCH_CORRUPT')
            manifest = self.round(call['round'])
            descriptor = (manifest['descriptors'][call['index']] if 'descriptors' in manifest
                          else self.get(manifest['plans'][call['index']]['key'], manifest['plans'][call['index']])[0])
            check(call['descriptor_sha256'] == digest(descriptor), 'AI_BATCH_CORRUPT')
            if key + '.outcome' not in calls:
                check(allow_unresolved and any(slot.get('call_token')==call['call_token']
                      and slot['status']=='RESERVED' for slot in self.last()[0]['state'].get('wave',[])), 'AI_BATCH_CORRUPT')
                continue
            outcome = calls[key + '.outcome'][0]
            check(outcome['kind'] == 'OUTCOME' and outcome['reservation'] == call_ref
                  and outcome['call_token'] == call['call_token'], 'AI_BATCH_CORRUPT')
            if outcome['status'] == 'SUCCESS':
                result = self.get(outcome['result']['key'], outcome['result'])[0]
                check(result['metadata']['input_sha256'] == call['input_sha256']
                      and outcome['result']['key'] == f"result.{call['round']:03d}.{call['index']:06d}"
                      and outcome['result']['key'] not in result_keys, 'AI_BATCH_CORRUPT')
                result_keys.add(outcome['result']['key']); success += 1
            elif outcome['status'] == 'FAILURE':
                check(valid_receipt(outcome.get('receipt')), 'AI_BATCH_CORRUPT')
                if 'validation_failure' in outcome:
                    from common.sanity_validation_diagnostics import sanitize_validation_failure
                    diagnostic=outcome['validation_failure']
                    check(diagnostic is not None and outcome['receipt']['error']=='INVALID_RATIONALE'
                          and sanitize_validation_failure(diagnostic)==diagnostic,'AI_BATCH_CORRUPT')
                failed += 1
            elif outcome['status'] == 'UNKNOWN':
                unknown_tokens.add(call['call_token']); unknown += 1
            else:
                check(False, 'AI_BATCH_CORRUPT')
        unresolved = {slot['call_token'] for slot in self.last()[0]['state'].get('wave',[]) if slot['status']=='UNKNOWN'}
        check((success, failed, unknown) == previous_counts
              and approved <= unknown_tokens
              and unknown_tokens-approved == (unresolved if allow_unresolved else set()), 'AI_BATCH_CORRUPT')
        # A round reader consumes every result row. Each one must either be
        # linked to a successful reservation or be a verified singleton bypass.
        all_results = self.rows('result.')
        for result, result_ref in all_results:
            key = result_ref['key']
            match = re.fullmatch(r'result\.(\d{3})\.(\d{6})',key)
            check(match is not None, 'AI_BATCH_CORRUPT')
            round_number, index = map(int,match.groups())
            manifest = self.round(round_number)
            check(index < manifest['count'], 'AI_BATCH_CORRUPT')
            if key in result_keys:
                continue
            descriptor = (manifest['descriptors'][index] if 'descriptors' in manifest
                          else self.get(manifest['plans'][index]['key'],manifest['plans'][index])[0])
            refs = descriptor.get('node_refs')
            check(isinstance(refs,list) and len(refs)==1 and isinstance(result,dict)
                  and set(result)=={'nodes','metadata'} and result['metadata'] is None
                  and result['nodes']==self.nodes(refs), 'AI_BATCH_CORRUPT')
            result_keys.add(key)
        check(result_keys == {ref['key'] for _,ref in all_results}, 'AI_BATCH_CORRUPT')
        return previous
