"""Signed, report-scoped human review commands. Standard library only on VWP."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from uuid import UUID

from common.sanity_common import canonical_json
from common.sanity_submission import check

API = '/v1/reviews'
PROTOCOL = 'human-cli-review-v1'
PROJECT = '__cli_review__'
MAX_REQUEST = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
SESSION_SECONDS = 8 * 3600
ACTIONS = {'STATUS', 'GROUPS', 'RECORDS', 'RECORD', 'GROUP', 'HISTORY', 'EDIT',
           'RATIONALE', 'CONFIRM', 'JUDGE', 'EVIDENCE', 'CLOSE'}
ERRORS = {'CLI_REVIEW_AUTH_REQUIRED', 'CLI_REVIEW_EXPIRED', 'CLI_REVIEW_REVOKED',
          'CLI_REVIEW_SCHEMA_REQUIRED', 'CLI_REVIEW_PENDING', 'CLI_REVIEW_LIMIT',
          'CLI_REVIEW_SOURCE_CHANGED', 'INVALID_REQUEST', 'REQUEST_CONFLICT',
          'MESSAGE_CONFLICT', 'REVISION_CONFLICT', 'FORBIDDEN', 'RESOURCE_NOT_FOUND',
          'NOT_FOUND', 'ACCOUNT_DISABLED', 'ACCOUNT_NOT_LINKED', 'AUTHORIZATION_NOT_IMPLEMENTED',
          'CLUSTER_NOT_READY', 'CLUSTER_NOT_CONFIRMED', 'CLUSTER_BLOCKED', 'CLUSTER_TASK_ACTIVE',
          'REVIEW_BLOCKED', 'PROPOSAL_STALE', 'RATIONALE_REQUIRED', 'RATIONALE_STALE',
          'INVALID_RATIONALE', 'INVALID_PARTITION', 'INVALID_EXCEPTION', 'INVALID_CRITERIA',
          'INVALID_RULE_GROUP', 'STATE_CORRUPT', 'DATABASE_ERROR', 'CLI_REVIEW_FAILED'}
ERRORS |= {'AI_EVIDENCE_NOT_READY', 'AI_EVIDENCE_STALE', 'AI_EVIDENCE_CONFLICT'}
ERRORS |= {'AI_EVIDENCE_CHANGED', 'CLI_REVIEW_SESSION_LIMIT', 'CASE_LIMIT', 'CLUSTER_LIMIT',
           'REPORT_LIMIT', 'SOURCE_CONTEXT_LIMIT', 'INVALID_CLUSTER'}


def identifier(value):
    try:
        check(isinstance(value, str) and str(UUID(value)) == value, 'INVALID_REQUEST')
    except (ValueError, AttributeError):
        check(False, 'INVALID_REQUEST')
    return value


def signing_key(secret):
    check(isinstance(secret, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', secret) is not None,
          'CLI_REVIEW_AUTH_REQUIRED')
    return hashlib.sha256(secret.encode('ascii')).hexdigest()


def signature(key, value):
    return hmac.new(bytes.fromhex(key), canonical_json({k: v for k, v in value.items() if k != 'signature'}),
                    hashlib.sha256).hexdigest()


def validate_envelope(value):
    check(isinstance(value, dict) and set(value) == {'schema_version', 'protocol', 'session_id',
          'request_id', 'expires_at', 'command', 'signature'}, 'INVALID_REQUEST')
    check(type(value['schema_version']) is int and value['schema_version'] == 1
          and value['protocol'] == PROTOCOL, 'INVALID_REQUEST')
    identifier(value['session_id'])
    identifier(value['request_id'])
    check(type(value['expires_at']) is int and value['expires_at'] > 0, 'INVALID_REQUEST')
    command = value['command']
    check(isinstance(command, dict) and set(command) == {'action', 'args'}
          and isinstance(command['action'], str) and command['action'] in ACTIONS
          and isinstance(command['args'], dict), 'INVALID_REQUEST')
    check(isinstance(value['signature'], str) and re.fullmatch(r'[0-9a-f]{64}', value['signature']) is not None,
          'CLI_REVIEW_AUTH_REQUIRED')
    check(len(canonical_json(value)) <= MAX_REQUEST, 'CLI_REVIEW_LIMIT')
    return value


def alive(expires_at):
    check(time.time() < expires_at, 'CLI_REVIEW_EXPIRED')


def request_key(request_id):
    return 'cli-request-' + identifier(request_id)


def response_key(request_id):
    return 'cli-response-' + identifier(request_id)
