"""Opaque signed requests and bounded replies; the bridge cannot assign a reviewer."""
from __future__ import annotations

from datetime import datetime, timezone
import hmac

from common.sanity_cli_review import (PROJECT, SESSION_SECONDS, alive, request_key,
    response_key, validate_envelope)
from common.sanity_common import digest
from common.sanity_cli_review_db import require_schema
from common.sanity_db import Queue
from common.sanity_submission import check


class ReviewRelay:
    def __init__(self, db):
        check(db.side == 'vwp', 'FORBIDDEN')
        require_schema(db)
        self.requests, self.responses = Queue(db, 'vwp_to_oa'), Queue(db, 'oa_to_vwp')

    def enqueue(self, value):
        validate_envelope(value)
        alive(value['expires_at'])
        check(value['expires_at'] <= datetime.now(timezone.utc).timestamp()+SESSION_SECONDS+60, 'INVALID_REQUEST')
        self.requests.enqueue({'message_id': request_key(value['request_id']),
            'message_type': 'CLI_REVIEW_REQUEST', 'project': PROJECT, 'run_id': None,
            'bundle_id': None, 'correlation_id': value['session_id'], 'payload': value})
        return self.poll(value['request_id'], value['signature'])

    def poll(self, request_id, proof):
        row = self.requests.get(request_key(request_id))
        check(row is not None and isinstance(proof, str)
              and hmac.compare_digest(row['payload'].get('signature', ''), proof), 'NOT_FOUND')
        value = validate_envelope(row['payload'])
        alive(value['expires_at'])
        response = self.responses.get(response_key(request_id))
        if response is None:
            return {'request_id': request_id, 'state': 'PENDING'}
        result = response['payload']
        check(result['request_sha256'] == digest(value) and result['session_id'] == value['session_id'], 'STATE_CORRUPT')
        return result
