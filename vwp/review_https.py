"""Human review transport to the VWP bridge, with no OA connection or DB login."""
import http.client
import ssl
from urllib.parse import urlsplit

from common.sanity_cli_review import API, ERRORS, MAX_RESPONSE, request_key
from common.sanity_common import ReviewError, _decode_json, canonical_json, digest
from common.sanity_submission import check
from common.sanity_submission_https import validate_https_config
from common.sanity_submission_intake import tokenless


class ReviewTransport:
    def __init__(self, config):
        self.config = validate_https_config(config)
        check(tokenless(config), 'CLI_REVIEW_CONFIG')
        self.url = urlsplit(config['endpoint'])
        self.context = ssl.create_default_context(cafile=config['ca_file'])
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def request(self, envelope, *, poll=False):
        request_key(envelope['request_id'])
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        body = None if poll else canonical_json(envelope)
        if poll:
            headers['X-Sanity-Review-Signature'] = envelope['signature']
        else:
            headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        connection = http.client.HTTPSConnection(self.url.hostname, self.url.port,
            context=self.context, timeout=min(30, self.config['timeout_seconds']))
        try:
            connection.request('GET' if poll else 'POST', API+'/'+envelope['request_id'], body=body, headers=headers)
            response = connection.getresponse()
            check(response.getheader('Content-Encoding', 'identity').lower() == 'identity'
                  and response.getheader('Content-Type', '').split(';')[0].strip() == 'application/json',
                  'CLI_REVIEW_INVALID_RESPONSE')
            raw = response.read(MAX_RESPONSE+1)
            check(len(raw) <= MAX_RESPONSE, 'CLI_REVIEW_LIMIT')
            value = _decode_json(raw)
            if response.status != 200:
                code = value.get('code') if isinstance(value, dict) else None
                raise ReviewError(code if isinstance(code, str) and code in ERRORS else 'CLI_REVIEW_HTTP_FAILED',
                                  'Review request failed')
            check(isinstance(value, dict) and value.get('request_id') == envelope['request_id']
                  and value.get('state') in ('PENDING', 'DONE', 'ERROR'), 'CLI_REVIEW_INVALID_RESPONSE')
            if value['state'] != 'PENDING':
                check(value.get('request_sha256') == digest(envelope)
                      and value.get('session_id') == envelope['session_id'], 'CLI_REVIEW_INVALID_RESPONSE')
            return value
        except (OSError, ValueError, http.client.HTTPException):
            raise ReviewError('CLI_REVIEW_CONNECTION_FAILED', 'Use resume to check the saved request') from None
        finally:
            connection.close()
