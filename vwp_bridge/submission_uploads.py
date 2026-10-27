"""Service-owned HTTPS staging/archive. No user-writable/shared mount or DB."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile

from common.sanity_common import ReviewError
from common.sanity_submission import MAX_METADATA, check, submission_uuid
from common.sanity_submission_files import (directory, fsync_dir, read_json_file, regular,
    validate_submission, verify_bundle, write_json_file)
from common.sanity_submission_https import validate_https_config


class UploadStore:
    def __init__(self, config):
        self.config = validate_https_config(config,bridge=True)
        self.root = directory(config['upload_root'])
        self.archive = directory(config['archive_root'])
        for path in (self.root,self.archive):
            info = path.stat()
            check(os.geteuid() == config['service_uid'] and info.st_uid == config['service_uid']
                  and info.st_mode & 0o077 == 0,'SUBMISSION_STORAGE_NOT_CONFIGURED')
        check(self.root != self.archive and self.root not in self.archive.parents and self.archive not in self.root.parents
              and self.root.stat().st_dev == self.archive.stat().st_dev,'SUBMISSION_STORAGE_NOT_CONFIGURED')
        # st_dev alone cannot detect two different bind mounts of the same disk.
        probe = Path(tempfile.mkdtemp(prefix='.rename-check-',dir=self.root))
        destination = self.archive/probe.name
        try:
            os.rename(probe,destination)
        except OSError:
            check(False,'SUBMISSION_STORAGE_NOT_CONFIGURED')
        finally:
            for path in (probe,destination):
                if path.exists():
                    path.rmdir()
        self.locks = directory(self.root/'.locks',create=True)

    @contextmanager
    def session(self, sid):
        with self.lock(sid):
            yield self

    @contextmanager
    def lock(self, sid):
        sid = submission_uuid(sid)
        descriptor = os.open(self.locks/sid,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info = os.fstat(descriptor)
            check(info.st_uid == self.config['service_uid'] and info.st_nlink == 1
                  and info.st_mode & 0o077 == 0,'UNSAFE_SUBMISSION_FILE')
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReviewError('SUBMISSION_BUSY','Submission is being updated') from None
            yield
        finally:
            os.close(descriptor)

    def load(self, sid, actor):
        path = self.root/submission_uuid(sid)/'request.json'
        check(path.exists(),'NOT_FOUND')
        value = read_json_file(path,2*MAX_METADATA)
        check(isinstance(value,dict) and set(value) == {'submission','manifest'})
        validate_submission(value['submission'],value['manifest'])
        check(value['submission']['submission_id'] == sid,'SUBMISSION_CONFLICT')
        check(value['submission']['actor'] == actor.actor,'NOT_FOUND')
        return value

    def initialize(self, sid, actor, value):
        check(isinstance(value,dict) and set(value) == {'submission','manifest'})
        validate_submission(value['submission'],value['manifest'])
        check(value['submission']['submission_id'] == sid and value['submission']['actor'] == actor.actor,'FORBIDDEN')
        session = self.root/sid
        if (session/'request.json').exists():
            check(self.load(sid,actor) == value,'SUBMISSION_CONFLICT')
        else:
            directory(session,create=True)
            write_json_file(session/'request.json',value)
            fsync_dir(self.root)
        if not (self.archive/sid).exists():
            bundle = directory(session/'bundle',create=True)
            directory(bundle/'snapshot',create=True)
            directory(bundle/'snapshot/reports',create=True)
            write_json_file(bundle/'submission.json',value['submission'])
            write_json_file(bundle/'snapshot/collection.json',value['manifest'])
        return self.status(sid,actor)

    def file(self, sid, actor, index):
        value = self.load(sid,actor)
        files = value['manifest']['files']
        check(type(index) is int and 0 <= index < len(files),'NOT_FOUND')
        committed = (self.archive/sid).exists()
        root = self.archive/sid if committed else self.root/sid/'bundle'
        return root/'snapshot'/files[index]['path'], files[index], committed

    @contextmanager
    def receiving(self, sid, actor, index):
        target, record, committed = self.file(sid,actor,index)
        check(not committed,'SUBMISSION_CONFLICT')
        directory(target.parent,create=True)
        descriptor, name = tempfile.mkstemp(prefix='.upload-',dir=self.root/sid)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                yield stream, record
                stream.flush()
                os.fsync(stream.fileno())
            with open(name,'rb') as stream:
                digest = self.hash_file(stream)
            check(os.stat(name).st_size == record['size_bytes'] and digest == record['sha256'],'FILE_INTEGRITY')
            if target.exists():
                current, _ = regular(target,record['size_bytes'])
                with current:
                    check(self.hash_file(current) == record['sha256'],'SUBMISSION_CONFLICT')
            else:
                os.rename(name,target)
                fsync_dir(target.parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def hash_file(stream):
        result = hashlib.sha256()
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            result.update(chunk)
        return result.hexdigest()

    def commit(self, sid, actor):
        value = self.load(sid,actor)
        output = self.archive/sid
        if output.exists():
            existing, _ = verify_bundle(output)
            check(existing == value['submission'],'SUBMISSION_CONFLICT')
        else:
            bundle = self.root/sid/'bundle'
            existing, _ = verify_bundle(bundle)
            check(existing == value['submission'],'SUBMISSION_CONFLICT')
            # Flush newly created report directory entries before publishing.
            for parent in sorted((p for p in bundle.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                fsync_dir(parent)
            fsync_dir(bundle)
            os.rename(bundle,output)
            fsync_dir(self.archive)
            fsync_dir(self.root/sid)
        return self.status(sid,actor)

    def status(self, sid, actor):
        value = self.load(sid,actor)
        committed = (self.archive/sid).exists()
        root = self.archive/sid if committed else self.root/sid/'bundle'
        received = []
        for index, record in enumerate(value['manifest']['files']):
            path = root/'snapshot'/record['path']
            if path.exists():
                stream, info = regular(path,record['size_bytes'])
                stream.close()
                check(info.st_size == record['size_bytes'],'FILE_INTEGRITY')
                received.append(index)
        result = {'schema_version':1,'submission_id':sid,'actor_key':actor.key,
                  'delivery_state':'VWP_UPLOADED' if committed else 'UPLOADING','verified_oa_state':False,
                  'review_readiness':'BLOCKED','blockers':[],'workspace_links':[]}
        receipt = self.root/sid/'receipt.json'
        if receipt.exists():
            saved = read_json_file(receipt,65536)
            check(saved.get('submission_id') == sid and saved.get('actor_key') == actor.key,'UNTRUSTED_RECEIPT')
            result.update(saved)
        result.update(content_sha256=value['submission']['content_sha256'],received_files=received,
                      total_files=len(value['manifest']['files']))
        return result
