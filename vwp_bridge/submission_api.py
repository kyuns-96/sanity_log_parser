"""Authenticated HTTPS ingress on the bridge; analysis stays in its DB worker."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import ssl
import sys

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from common.sanity_common import ReviewError, _decode_json
from common.sanity_submission import MAX_METADATA, VerifiedRemoteSubmitter, approved, check, submission_uuid
from common.sanity_submission_files import load_config, validate_submission
from common.sanity_submission_https import API, bearer
from common.sanity_submission_intake import (UnverifiedSubmitter, require_internal_peer,
    require_pending_plan, tokenless, unverified)
from vwp_bridge.submission_uploads import UploadStore


ERROR_STATUS = {'SUBMISSION_AUTH_REQUIRED':401,'FORBIDDEN':403,'NOT_FOUND':404,
    'SUBMISSION_CONFLICT':409,'SUBMISSION_BUSY':409,'SUBMISSION_LIMIT':413,
    'SUBMISSION_AUTH_NOT_CONFIGURED':503,'AUTHORIZATION_NOT_IMPLEMENTED':503,
    'SUBMISSION_APPROVAL_NOT_CONFIGURED':503,'FILE_INTEGRITY':422,'INVALID_SUBMISSION':422,
    'SUBMISSION_HTTPS_REQUIRED':400,'INVALID_SUBMISSION_REQUEST':400,'SUBMISSION_TIMEOUT':408,
    'SUBMISSION_IDENTITY_MISMATCH':403,'RAW_SOURCE_SCHEMA_REQUIRED':503,'RAW_SOURCE_INCOMPLETE':409,
    'RAW_SOURCE_CORRUPT':422,'SUBMISSION_TEMP_LIMIT':503,'DATABASE_UNAVAILABLE':503}


def create_app(config, authority):
    if config.get('storage') == 'postgresql':
        from common.sanity_raw_sources import DatabaseUploadStore
        storage = DatabaseUploadStore(config)
    else:
        storage = UploadStore(config)
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.uploads = storage
    internal = tokenless(config)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=config['allowed_hosts'],www_redirect=False)

    @app.middleware('http')
    async def errors(request, call_next):
        try:
            result = await call_next(request)
        except (ClientDisconnect,asyncio.TimeoutError):
            result = JSONResponse({'code':'SUBMISSION_TIMEOUT'},status_code=408)
        except Exception:
            # Company exception messages can contain credentials. Never log/return them.
            result = JSONResponse({'code':'SUBMISSION_ADAPTER_FAILED'},status_code=503)
        result.headers['Cache-Control'] = 'no-store'
        result.headers['X-Content-Type-Options'] = 'nosniff'
        return result

    @app.exception_handler(ReviewError)
    async def rejected(request, exc):
        code = exc.code if exc.code in ERROR_STATUS else 'INVALID_SUBMISSION'
        return JSONResponse({'code':code},status_code=ERROR_STATUS.get(code,422))

    async def authenticate(request):
        check(request.url.scheme == 'https','SUBMISSION_HTTPS_REQUIRED')
        check(not request.url.query and not request.headers.get('origin') and not request.headers.get('cookie'),
              'INVALID_SUBMISSION_REQUEST')
        if internal:
            require_internal_peer(config,request.client.host if request.client else None)
            check(not request.headers.getlist('authorization'),'INVALID_SUBMISSION_REQUEST')
            return None, None
        headers = request.headers.getlist('authorization')
        check(len(headers) == 1 and headers[0].startswith('Bearer '),'SUBMISSION_AUTH_REQUIRED')
        token = bearer(headers[0][7:])
        actor = await run_in_threadpool(authority.authenticate_bearer,token)
        check(isinstance(actor,VerifiedRemoteSubmitter) and not unverified(actor.actor),
              'SUBMISSION_AUTH_NOT_CONFIGURED')
        return actor, token

    def body_size(request, content_type, maximum):
        check(request.headers.get('content-type','').split(';')[0] == content_type,'INVALID_SUBMISSION_REQUEST')
        check(request.headers.get('content-encoding','identity') == 'identity'
              and not request.headers.get('transfer-encoding'),'INVALID_SUBMISSION_REQUEST')
        sizes = request.headers.getlist('content-length')
        check(len(sizes) == 1 and sizes[0].isascii() and sizes[0].isdecimal()
              and len(sizes[0]) <= 20,'INVALID_SUBMISSION_REQUEST')
        length = int(sizes[0])
        check(length <= maximum,'SUBMISSION_LIMIT')
        return length

    async def chunks(request):
        iterator = request.stream().__aiter__()
        while True:
            try:
                yield await asyncio.wait_for(iterator.__anext__(),timeout=60)
            except StopAsyncIteration:
                return

    async def check_rights(actor, value):
        if internal:
            check(isinstance(actor,UnverifiedSubmitter),'INVALID_SUBMISSION')
            require_pending_plan(value['submission']['plan'])
        else:
            await run_in_threadpool(authority.authorize,actor,value['submission']['plan']['collection'])

    async def existing(store, sid, actor):
        value = await run_in_threadpool(store.load,sid,actor)
        if internal:
            # Reserved unverified labels can never address a legacy authenticated submission.
            check(unverified(value['submission']['actor']),'NOT_FOUND')
            actor = UnverifiedSubmitter(**value['submission']['actor'])
        await check_rights(actor,value)
        return value,actor

    @asynccontextmanager
    async def receiving(store, sid, actor, index):
        manager = store.receiving(sid,actor,index)
        result = await run_in_threadpool(manager.__enter__)
        try:
            yield result
        except BaseException:
            await run_in_threadpool(manager.__exit__,*sys.exc_info())
            raise
        else:
            await run_in_threadpool(manager.__exit__,None,None,None)

    @asynccontextmanager
    async def session(sid):
        manager = storage.session(sid)
        store = await run_in_threadpool(manager.__enter__)
        try:
            yield store
        except BaseException:
            await run_in_threadpool(manager.__exit__,*sys.exc_info())
            raise
        else:
            await run_in_threadpool(manager.__exit__,None,None,None)

    @app.post(API+'/{sid}')
    async def initialize(sid: str, request: Request):
        sid = submission_uuid(sid)
        actor, _ = await authenticate(request)
        length = body_size(request,'application/json',2*MAX_METADATA)
        data = bytearray()
        async for chunk in chunks(request):
            check(len(data)+len(chunk) <= length,'SUBMISSION_LIMIT')
            data.extend(chunk)
        check(len(data) == length,'INVALID_SUBMISSION_REQUEST')
        value = _decode_json(bytes(data))
        check(isinstance(value,dict) and set(value) == {'submission','manifest'})
        validate_submission(value['submission'],value['manifest'])
        if internal:
            actor = UnverifiedSubmitter(**value['submission']['actor'])
        check(value['submission']['actor'] == actor.actor,'FORBIDDEN')
        await check_rights(actor,value)
        if not internal:
            await run_in_threadpool(approved,value['submission']['plan'],authority)
        async with session(sid) as store:
            return await run_in_threadpool(store.initialize,sid,actor,value)

    @app.get(API+'/{sid}')
    async def status(sid: str, request: Request):
        sid = submission_uuid(sid)
        actor, _ = await authenticate(request)
        async with session(sid) as store:
            value,actor = await existing(store,sid,actor)
            return await run_in_threadpool(store.status,sid,actor)

    @app.put(API+'/{sid}/files/{index}')
    async def upload(sid: str, index: int, request: Request):
        sid = submission_uuid(sid)
        actor, token = await authenticate(request)
        async with session(sid) as store:
            value,actor = await existing(store,sid,actor)
            _, record, _ = await run_in_threadpool(store.file,sid,actor,index)
            length = body_size(request,'application/octet-stream',record['size_bytes'])
            check(length == record['size_bytes'],'FILE_INTEGRITY')
            async with receiving(store,sid,actor,index) as (stream, _):
                written = 0
                async for chunk in chunks(request):
                    written += len(chunk)
                    check(written <= length,'SUBMISSION_LIMIT')
                    await run_in_threadpool(stream.write,chunk)
                check(written == length,'FILE_INTEGRITY')
                # Long uploads do not outlive a revoked credential or assignment.
                if not internal:
                    current = await run_in_threadpool(authority.authenticate_bearer,token)
                    check(isinstance(current,VerifiedRemoteSubmitter) and current.actor == actor.actor,'FORBIDDEN')
                    await check_rights(current,value)
            return await run_in_threadpool(store.status,sid,actor)

    @app.post(API+'/{sid}/commit')
    async def commit(sid: str, request: Request):
        sid = submission_uuid(sid)
        actor, _ = await authenticate(request)
        check(request.headers.get('content-length') == '0' and not request.headers.get('transfer-encoding'),
              'INVALID_SUBMISSION_REQUEST')
        async with session(sid) as store:
            value,actor = await existing(store,sid,actor)
            if not internal:
                await run_in_threadpool(approved,value['submission']['plan'],authority)
            return await run_in_threadpool(store.commit,sid,actor)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/sanity/vwp-bridge-submit.json')
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=8443)
    parser.add_argument('--tls-cert',required=True)
    parser.add_argument('--tls-key',required=True)
    args = parser.parse_args(argv)
    try:
        import uvicorn
        from vwp_bridge.integration.corporate_submission import CorporateSubmissionAuthority
        check(1 <= args.port <= 65535,'SUBMISSION_CONFIG')
        config = load_config(args.config,bridge=True)
        check(config.get('transport') == 'https','SUBMISSION_CONFIG')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert,args.tls_key)
        app = create_app(config,CorporateSubmissionAuthority())
        uvicorn.run(app,host=args.host,port=args.port,ssl_context_factory=lambda _config,_default:context,
                    proxy_headers=False,access_log=False,server_header=False,workers=1,
                    limit_concurrency=16,timeout_keep_alive=5)
        return 0
    except (ReviewError,OSError,ValueError):
        print('code=SUBMISSION_SERVER_NOT_CONFIGURED',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
