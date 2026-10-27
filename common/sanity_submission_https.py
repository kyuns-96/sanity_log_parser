"""HTTPS transport configuration and bounded receipts; standard library only."""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlsplit

from common.sanity_submission import HEX, actor_value, check, submission_uuid, text

API = '/v1/submissions'
MAX_RESPONSE = 1024 * 1024
HTTP_STATES = {'UPLOADING', 'VWP_UPLOADED', 'VWP_ACCEPTED', 'PARSING', 'DB_QUEUED', 'OA_STORED', 'REJECTED'}


def validate_https_config(value, *, bridge=False):
    database = bridge and isinstance(value,dict) and value.get('schema_version') == 3
    keys = ({'service_uid','storage','node_id','allowed_hosts','temp_root'} if database else
            {'service_uid', 'archive_root', 'upload_root', 'node_id', 'allowed_hosts'} if bridge else
            {'endpoint', 'ca_file', 'timeout_seconds', 'default_profile'})
    check(isinstance(value,dict) and set(value) == keys | {'schema_version','transport'}
          and type(value['schema_version']) is int and value['schema_version'] == (3 if database else 2)
          and value['transport'] == 'https', 'SUBMISSION_CONFIG')
    if bridge:
        check(type(value['service_uid']) is int and value['service_uid'] >= 0, 'SUBMISSION_CONFIG')
        if database:
            check(value['storage'] == 'postgresql' and (value['temp_root'] is None or
                  text(value['temp_root']) and Path(value['temp_root']).is_absolute()),'SUBMISSION_CONFIG')
        else:
            for key in ('archive_root','upload_root'):
                check(text(value[key]) and Path(value[key]).is_absolute(), 'SUBMISSION_CONFIG')
        check(text(value['node_id'],100), 'SUBMISSION_CONFIG')
        hosts = value['allowed_hosts']
        check(isinstance(hosts,list) and hosts and len(hosts) <= 16, 'SUBMISSION_CONFIG')
        check(all(isinstance(h,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',h)
                  and '*' not in h for h in hosts), 'SUBMISSION_CONFIG')
    else:
        check(text(value['endpoint']), 'SUBMISSION_CONFIG')
        try:
            url = urlsplit(value['endpoint'])
            port = url.port
        except ValueError:
            check(False, 'SUBMISSION_CONFIG')
        check(url.scheme == 'https' and url.hostname and url.path in ('','/')
              and not url.username and not url.password and not url.query and not url.fragment
              and not any(c in value['endpoint'] for c in ('\\','%'))
              and (port is None or 1 <= port <= 65535), 'SUBMISSION_CONFIG')
        check(value['ca_file'] is None or text(value['ca_file']) and Path(value['ca_file']).is_absolute(), 'SUBMISSION_CONFIG')
        check(type(value['timeout_seconds']) is int and 1 <= value['timeout_seconds'] <= 3600, 'SUBMISSION_CONFIG')
        check(text(value['default_profile'],100), 'SUBMISSION_CONFIG')
    return value


def bearer(value):
    # Opaque credential; the company verifier authenticates it, not this parser.
    check(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,16384}',value), 'SUBMISSION_AUTH_REQUIRED')
    return value


def validate_http_receipt(value, sid, actor, *, content_sha256=None):
    check(isinstance(value,dict), 'INVALID_SUBMISSION_RECEIPT')
    required = {'schema_version','submission_id','actor_key','content_sha256','delivery_state',
                'verified_oa_state','review_readiness','blockers','workspace_links','received_files','total_files'}
    check(required <= set(value) <= required | {'state_revision','updated_at'}, 'INVALID_SUBMISSION_RECEIPT')
    check(value['schema_version'] == 1 and value['submission_id'] == submission_uuid(sid)
          and value['actor_key'] == actor.key and isinstance(value['content_sha256'],str)
          and HEX.fullmatch(value['content_sha256'])
          and (content_sha256 is None or value['content_sha256'] == content_sha256), 'INVALID_SUBMISSION_RECEIPT')
    check(value['delivery_state'] in HTTP_STATES and type(value['verified_oa_state']) is bool
          and value['review_readiness'] in ('READY','BLOCKED') and isinstance(value['blockers'],list)
          and all(text(b,100) for b in value['blockers']) and isinstance(value['workspace_links'],list),
          'INVALID_SUBMISSION_RECEIPT')
    check(type(value['total_files']) is int and 0 <= value['total_files'] <= 10000
          and isinstance(value['received_files'],list)
          and all(type(i) is int and 0 <= i < value['total_files'] for i in value['received_files'])
          and len(set(value['received_files'])) == len(value['received_files']), 'INVALID_SUBMISSION_RECEIPT')
    check(value['delivery_state'] != 'OA_STORED' or value['verified_oa_state'], 'INVALID_SUBMISSION_RECEIPT')
    actor_value(actor.actor)
    return value
