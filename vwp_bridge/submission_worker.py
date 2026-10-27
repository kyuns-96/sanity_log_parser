"""VWP-only submission spool receiver and PostgreSQL source outbox publisher."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from common.sanity_common import ReviewError, digest, pack_json
from common.sanity_db import Database, Queue, lock_key
from common.sanity_parsers import validate_builtin_evaluation
from common.sanity_privacy import SecretGuard
from common.sanity_submission import (PART_BYTES, PROTOCOL, VerifiedRemoteSubmitter, approved, check, message_id, submission_uuid,
    submission_receipt,
    validate_packet)
from common.sanity_submission_files import (copy_bundle, directory, fsync_dir, load_config, read_json_file,
    remove_own_partial, storage, verify_bundle, verify_snapshot, write_json_file)
from common.sanity_submission_store import SubmissionStore
from vwp_bridge.submission_analysis import build_transfer


class SubmissionWorker:
    def __init__(self, db, config, authority, registry):
        self.db, self.config, self.authority, self.registry = db, config, authority, registry
        self.store = SubmissionStore(db)
        self.https = config.get('transport') == 'https'
        self.database_storage = config.get('storage') == 'postgresql'
        if self.database_storage:
            from common.sanity_raw_sources import RawSourceStore
            self.raw = RawSourceStore(db,config['node_id'],temp_root=config['temp_root'])
            return
        if self.https:
            from vwp_bridge.submission_uploads import UploadStore
            self.uploads = UploadStore(config)
            self.root = self.uploads.root
        else:
            self.root = storage(config)
        self.archive = directory(config['archive_root'])
        check(os.geteuid() == config['service_uid'] and self.archive.stat().st_uid == config['service_uid']
              and self.archive.stat().st_mode & 0o077 == 0, 'SUBMISSION_STORAGE_NOT_CONFIGURED')
        check(self.archive != self.root and self.root not in self.archive.parents,'UNSAFE_SUBMISSION_PATH')

    def write_receipt(self, row):
        submission, state = row['payload'], row['state']
        actor = submission['actor']
        key = digest([actor['issuer'],actor['subject']])
        value = submission_receipt(row)
        if self.database_storage:
            return value
        receipts = directory(self.root/submission['submission_id'] if self.https else self.root/'receipts'/key)
        write_json_file(receipts/('receipt.json' if self.https else submission['submission_id']+'.json'),value,
                        mode=0o600 if self.https else 0o640)
        return value

    def refresh_receipt(self, row):
        if self.database_storage:
            return
        actor = row['payload']['actor']
        key = digest([actor['issuer'],actor['subject']])
        path = (self.root/row['payload']['submission_id']/'receipt.json' if self.https else
                self.root/'receipts'/key/(row['payload']['submission_id']+'.json'))
        if path.exists() or path.is_symlink():
            old = read_json_file(path,65536)
            if (old.get('submission_id')==row['payload']['submission_id'] and old.get('actor_key')==key
                    and old.get('state_revision')==row['version']):
                return
        self.write_receipt(row)

    def inbox_actor(self, inbox):
        directory(inbox)
        actor = self.authority.inbox_owner(inbox)
        check(inbox.name==actor.key and inbox.stat().st_uid==actor.os_uid
              and inbox.stat().st_mode & 0o007==0,'FORBIDDEN')
        return actor

    def stored_actor(self, actor):
        if self.https:
            verified = self.authority.resolve_actor(actor)
            check(isinstance(verified,VerifiedRemoteSubmitter) and verified.actor == actor,'FORBIDDEN')
            return verified
        return self.inbox_actor(self.root/'inbox'/digest([actor['issuer'],actor['subject']]))

    def receive_receipt(self):
        queue = Queue(self.db,'oa_to_vwp')
        message = queue.claim(('SOURCE_RECEIPT',))
        if not message:
            return False
        try:
            value = message['payload']
            check(set(value) == {'schema_version','protocol','submission_id','content_sha256','collection_id',
                'actor_key','delivery_state','review_readiness','blockers','workspace_links'},'INVALID_SOURCE_RECEIPT')
            check(value['schema_version'] == 2 and value['protocol'] == PROTOCOL
                  and value['delivery_state'] in ('OA_STORED','REJECTED')
                  and value['review_readiness'] in ('READY','BLOCKED')
                  and isinstance(value['blockers'],list) and isinstance(value['workspace_links'],list),'INVALID_SOURCE_RECEIPT')
            sid = submission_uuid(value['submission_id'])
            check(message['message_id'] == message_id('SOURCE_RECEIPT',value)
                  and message['run_id'] is None and message['bundle_id'] is None,'INVALID_SOURCE_RECEIPT')
            with self.db.transaction():
                row = self.store.get('SUBMISSION',sid,lock=True)
                check(row and row['payload']['content_sha256'] == value['content_sha256']
                      and row['state'].get('collection_id') == value['collection_id']
                      and row['payload']['plan']['collection']['project_key'] == message['project']
                      and digest([row['payload']['actor']['issuer'],row['payload']['actor']['subject']]) == value['actor_key'],
                      'INVALID_SOURCE_RECEIPT')
                state = {**row['state'],'oa_receipt':value}
                row = self.store.state(sid,value['delivery_state'],state)
                queue.finish(message)
            self.write_receipt(row)
            return True
        except ReviewError as exc:
            if exc.code != 'LEASE_LOST':
                with self.db.transaction():
                    queue.finish(message,'REJECTED',exc.code)
            raise

    def archive_bundle(self, ready, submission):
        sid = submission['submission_id']
        output, partial = self.archive/sid, self.archive/('.'+sid+'.partial')
        if output.exists():
            existing, _ = verify_bundle(output)
            check(existing == submission,'SUBMISSION_CONFLICT')
            return output
        if partial.exists() or partial.is_symlink():
            remove_own_partial(partial,self.config['service_uid'])
        copy_bundle(ready,partial)
        check(not output.exists(),'SUBMISSION_CONFLICT')
        os.rename(partial,output)
        fsync_dir(self.archive)
        return output

    def publication_packet(self, sid, position):
        outbox = self.store.get('SOURCE_OUTBOX',sid+'.'+str(position))
        check(outbox,'SUBMISSION_OUTBOX_INCOMPLETE')
        value = outbox['payload']
        packet = validate_packet(value['packet'])
        check(value['message_id'] == message_id(value['kind'],packet),'SUBMISSION_CONFLICT')
        return value, packet

    def publish(self, sid):
        row = self.store.get('SUBMISSION',sid)
        for unit in row['payload']['plan']['inventory']['units']:
            if unit['applicable']:
                validate_builtin_evaluation(unit.get('parser'),unit)
        positions = range(row['state']['next_publish'],row['state']['message_count'])
        guard = SecretGuard()
        # Preflight all remaining immutable packets, including old outboxes,
        # before publishing any of them. Keep memory bounded to one packet.
        for position in positions:
            _, packet = self.publication_packet(sid,position)
            guard.ensure_public(packet)
        for position in positions:
            value, packet = self.publication_packet(sid,position)
            packed = pack_json(packet,max_bytes=PART_BYTES)
            with self.db.transaction():
                Queue(self.db,'vwp_to_oa').enqueue({'message_id':value['message_id'],'message_type':value['kind'],
                    'project':packet['scope']['project_key'],'run_id':None,'bundle_id':None,
                    'correlation_id':sid,'payload':{k:packet[k] for k in
                        ('schema_version','protocol','submission_id','content_sha256','collection_id')},**packed})
                current = self.store.get('SUBMISSION',sid,lock=True)
                self.store.state(sid,'DB_QUEUED',{**current['state'],'next_publish':position+1})
        return self.store.get('SUBMISSION',sid)

    def process(self, ready, actor, *, submission_id=None):
        sid = submission_uuid(submission_id or ready.name.removesuffix('.ready'))
        token = lock_key('submission-v2-'+sid)
        locked = self.db.conn.execute('SELECT pg_try_advisory_lock(%s) AS locked',(token,)).fetchone()['locked']
        if not locked:
            return False
        try:
            incoming, manifest = verify_bundle(ready)
            check(incoming['submission_id'] == sid and incoming['actor'] == actor.actor,'FORBIDDEN')
            self.authority.authorize(actor,incoming['plan']['collection'])
            confirmed = approved(incoming['plan'],self.authority)
            output = ready if self.database_storage else self.archive_bundle(ready,incoming)
            with self.db.transaction():
                row = self.store.accept(incoming,node_id=self.config['node_id'])
            self.write_receipt(row)
            if row['status'] in ('OA_STORED','REJECTED'):
                return False
            if row['status'] in ('VWP_ACCEPTED','PARSING'):
                with self.db.transaction():
                    row = self.store.state(sid,'PARSING',{})
                self.write_receipt(row)
                build_transfer(self.db,self.store,incoming,manifest,output/'snapshot',confirmed=confirmed,registry=self.registry)
                verify_snapshot(output/'snapshot')
            row = self.publish(sid)
            self.write_receipt(row)
            return True
        except ReviewError as exc:
            permanent = {'SOURCE_IDENTITY_CONFLICT','SOURCE_ORDER_CONFLICT','RECORD_TRANSFER_LIMIT','SOURCE_MESSAGE_LIMIT',
                'SOURCE_COVERAGE_MISMATCH','FILE_INTEGRITY','SOURCE_CHANGED','SOURCE_SCOPE_MISMATCH','SENSITIVE_CONTENT',
                'PARSER_SCOPE_MISMATCH','SUBMISSION_CONTRACT_MISMATCH','INVALID_SUBMISSION'}
            if exc.code in permanent or exc.code.startswith('PARSER_'):
                with self.db.transaction():
                    row = self.store.get('SUBMISSION',sid,lock=True)
                    if row and row['status'] in ('VWP_ACCEPTED','PARSING','DB_QUEUED'):
                        row = self.store.state(sid,'REJECTED',{**row['state'],'blockers':[exc.code]})
                if row:
                    self.write_receipt(row)
            raise
        finally:
            self.db.conn.execute('SELECT pg_advisory_unlock(%s)',(token,))

    def once(self, limit=10):
        check(type(limit) is int and 1 <= limit <= 100)
        receipts = 0
        while receipts < 100 and self.receive_receipt():
            receipts += 1
        if self.database_storage:
            rows = self.db.execute("SELECT r.entity_key,r.payload#>'{{request,submission,actor}}' AS actor "
                "FROM {vwp} r LEFT JOIN {vwp} s ON s.record_type='SUBMISSION' "
                "AND s.entity_key=substring(r.entity_key from 6) WHERE r.record_type='RAW_SUBMISSION' "
                "AND r.status='VWP_UPLOADED' AND r.payload->>'node_id'=%s AND (s.record_id IS NULL "
                "OR s.status IN ('VWP_ACCEPTED','PARSING') OR (s.status='DB_QUEUED' "
                "AND (s.state->>'next_publish')::int < (s.state->>'message_count')::int)) "
                "ORDER BY r.created_at,r.entity_key LIMIT %s",(self.config['node_id'],limit)).fetchall()
            processed = 0
            for row in rows:
                sid = submission_uuid(row['entity_key'][5:])
                actor = self.stored_actor(row['actor'])
                with self.raw.materialize(sid) as bundle:
                    processed += bool(self.process(bundle,actor,submission_id=sid))
            return {'status':'SUBMISSIONS_PROCESSED' if processed or receipts else 'NO_MESSAGE',
                    'processed':processed,'receipts':receipts}
        processed = 0
        # Progress and final receipt repair must survive removal of a user's
        # shared .ready copy. Do not materialize every registered plan here.
        known = set()
        rows = self.db.execute("SELECT entity_key,status,state,version,updated_at,payload->'actor' AS actor "
            "FROM {vwp} WHERE record_type='SUBMISSION' AND payload->>'node_id'=%s ORDER BY created_at,entity_key",
            (self.config['node_id'],)).fetchall()
        for row in rows:
            sid = row['entity_key']
            known.add(sid)
            row['payload'] = {'submission_id':sid,'actor':row.pop('actor')}
            self.refresh_receipt(row)
            pending = (row['status'] in ('VWP_ACCEPTED','PARSING') or row['status']=='DB_QUEUED'
                       and row['state']['next_publish'] < row['state']['message_count'])
            if pending and processed < limit:
                actor = self.stored_actor(row['payload']['actor'])
                check(actor.actor==row['payload']['actor'],'FORBIDDEN')
                processed += bool(self.process(self.archive/sid,actor))
        # A crash between the durable archive rename and DB registration leaves
        # a verified orphan. Recover it from service storage, even without .ready.
        for archived in sorted(self.archive.iterdir()):
            if processed >= limit:
                break
            if archived.name.startswith('.'):
                continue
            sid = submission_uuid(archived.name)
            if sid in known or self.store.get('SUBMISSION',sid):
                continue
            incoming = read_json_file(archived/'submission.json')
            actor = self.stored_actor(incoming['actor'])
            processed += bool(self.process(archived,actor))
            known.add(sid)
        for inbox in ([] if self.https else sorted((self.root/'inbox').iterdir())):
            actor = self.inbox_actor(inbox)
            for ready in sorted(inbox.iterdir()):
                if ready.name.startswith('.') or not ready.name.endswith('.ready'):
                    continue
                if processed >= limit:
                    break
                sid = submission_uuid(ready.name.removesuffix('.ready'))
                old = self.store.get('SUBMISSION',sid)
                if old:
                    check(old['payload']['actor']==actor.actor,'FORBIDDEN')
                    continue
                processed += bool(self.process(ready,actor))
        return {'status':'SUBMISSIONS_PROCESSED' if processed or receipts else 'NO_MESSAGE',
                'processed':processed,'receipts':receipts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/sanity/vwp-bridge-submit.json')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=2)
    args = parser.parse_args(argv)
    try:
        from vwp_bridge.integration.corporate_submission import CorporateSubmissionAuthority
        from vwp_bridge.integration.corporate_parsers import create_parser_registry
        from common.sanity_workers import poll
        config = load_config(args.config,bridge=True)
        with Database('vwp') as db:
            worker = SubmissionWorker(db,config,CorporateSubmissionAuthority(),create_parser_registry())
            print(poll(worker.once,args.once,args.interval))
        return 0
    except ReviewError as exc:
        print('code='+exc.code,file=sys.stderr)
    except (OSError,ValueError,TypeError):
        print('code=SUBMISSION_IO_ERROR',file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
