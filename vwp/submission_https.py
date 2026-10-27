"""VWP HTTPS transport. No DB, proxy discovery, redirects or automatic retries."""
from __future__ import annotations

import http.client
import os
import ssl
from urllib.parse import urlsplit

from common.sanity_common import ReviewError, _decode_json, canonical_json
from common.sanity_submission import check, submission_uuid
from common.sanity_submission_files import regular, verify_bundle
from common.sanity_submission_https import API, MAX_RESPONSE, bearer, validate_http_receipt, validate_https_config
from common.sanity_submission_intake import tokenless


class HttpsTransport:
    def __init__(self, config):
        self.config = validate_https_config(config)
        self.url = urlsplit(config['endpoint'])
        self.context = ssl.create_default_context(cafile=config['ca_file'])
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def request(self, method, path, body=None, *, length=None, content_type='application/json'):
        headers = {'Accept':'application/json','Accept-Encoding':'identity'}
        if not tokenless(self.config):
            headers['Authorization'] = 'Bearer '+bearer(os.environ.get('SANITY_VWP_SUBMIT_TOKEN',''))
        connection = http.client.HTTPSConnection(self.url.hostname,self.url.port,
            timeout=self.config['timeout_seconds'],context=self.context)
        try:
            if body is not None:
                headers.update({'Content-Type':content_type,'Content-Length':str(length if length is not None else len(body))})
            connection.request(method,path,body=body,headers=headers)
            response = connection.getresponse()
            check(response.getheader('Content-Encoding','identity').lower() == 'identity','INVALID_SUBMISSION_RECEIPT')
            data = response.read(MAX_RESPONSE+1)
            check(len(data) <= MAX_RESPONSE,'INVALID_SUBMISSION_RECEIPT')
            if response.status not in (200,201,202):
                # Display only our protocol's fixed code; never a server's raw diagnostic.
                code = {401:'SUBMISSION_AUTH_REQUIRED',403:'FORBIDDEN',404:'NOT_FOUND',409:'SUBMISSION_CONFLICT',
                        413:'SUBMISSION_LIMIT'}.get(response.status,'SUBMISSION_HTTP_FAILED')
                try:
                    failure = _decode_json(data)
                except ReviewError:
                    failure = None
                if isinstance(failure,dict) and set(failure) == {'code'} and isinstance(failure['code'],str) and failure['code'] in {
                        'SUBMISSION_AUTH_REQUIRED','FORBIDDEN','NOT_FOUND','SUBMISSION_CONFLICT','SUBMISSION_BUSY',
                        'SUBMISSION_LIMIT','SUBMISSION_AUTH_NOT_CONFIGURED','AUTHORIZATION_NOT_IMPLEMENTED',
                        'SUBMISSION_APPROVAL_NOT_CONFIGURED','FILE_INTEGRITY','INVALID_SUBMISSION',
                        'SUBMISSION_HTTPS_REQUIRED','INVALID_SUBMISSION_REQUEST','SUBMISSION_TIMEOUT',
                        'SUBMISSION_IDENTITY_MISMATCH','SUBMISSION_ADAPTER_FAILED','RAW_SOURCE_SCHEMA_REQUIRED',
                        'RAW_SOURCE_INCOMPLETE','RAW_SOURCE_CORRUPT','SUBMISSION_TEMP_LIMIT','DATABASE_UNAVAILABLE'}:
                    code = failure['code']
                raise ReviewError(code,'HTTPS submission request failed')
            check(response.getheader('Content-Type','').split(';')[0].strip() == 'application/json',
                  'INVALID_SUBMISSION_RECEIPT')
            return _decode_json(data)
        except ReviewError:
            raise
        except (OSError,ValueError,http.client.HTTPException):
            raise ReviewError('SUBMISSION_CONNECTION_FAILED','HTTPS request failed; check status before retrying') from None
        finally:
            connection.close()

    def status(self, sid, actor):
        value = self.request('GET',API+'/'+submission_uuid(sid))
        return validate_http_receipt(value,sid,actor)

    def enqueue(self, local, sid, actor):
        submission, manifest = verify_bundle(local)
        path = API+'/'+submission_uuid(sid)
        value = self.request('POST',path,canonical_json({'submission':submission,'manifest':manifest}))
        value = validate_http_receipt(value,sid,actor,content_sha256=submission['content_sha256'])
        if value['delivery_state'] != 'UPLOADING':
            return value
        check(value['total_files'] == len(manifest['files']),'INVALID_SUBMISSION_RECEIPT')
        for index, record in enumerate(manifest['files']):
            if index in value['received_files']:
                continue
            stream, info = regular(local/'snapshot'/record['path'],record['size_bytes'])
            with stream:
                check(info.st_size == record['size_bytes'],'FILE_INTEGRITY')
                result = self.request('PUT',path+'/files/'+str(index),stream,
                    length=record['size_bytes'],content_type='application/octet-stream')
            validate_http_receipt(result,sid,actor,content_sha256=submission['content_sha256'])
        result = self.request('POST',path+'/commit',b'')
        return validate_http_receipt(result,sid,actor,content_sha256=submission['content_sha256'])
