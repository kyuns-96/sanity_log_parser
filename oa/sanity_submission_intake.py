"""SSO-owner confirmation of complete, unverified source collections.

Uses the existing WS_SUBMISSION state and SOURCE_RECEIPT contract. The caller
owns the same authorized transaction used by workbench writes. No VWP token,
identity lookup, private-table read, model call or schema change is involved.
"""
from datetime import datetime, timezone

from common.sanity_common import digest
from common.sanity_db import lock_key
from common.sanity_submission import check, scope_digest, submission_uuid, text
from common.sanity_submission_intake import unverified
from oa.sanity_workspace import record_id
from oa.submission_receiver import SourceReceiver


class SubmissionIntake:
    def __init__(self, workspace):
        self.ws, self.db = workspace,workspace.db

    def row(self, user_id, block_id, sid, *, write=False):
        block,project = self.ws.access(user_id,block_id,write=write)
        row = self.ws.get('WS_SUBMISSION',submission_uuid(sid))
        check(row['workspace_block_ref']==block['record_id']
              and row['workspace_project_ref']==project['record_id'],'NOT_FOUND')
        check(unverified(row['payload']['actor']),'NOT_FOUND')
        return row,block

    def list(self, user_id, block_id, *, offset=0, limit=25):
        block,_ = self.ws.access(user_id,block_id)
        check(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=100,'INVALID_REQUEST')
        from common.sanity_submission_intake import UNVERIFIED_ISSUER
        args=(record_id('WS_BLOCK',block_id),UNVERIFIED_ISSUER)
        where="record_type='WS_SUBMISSION' AND workspace_block_ref=%s AND payload->'actor'->>'issuer'=%s"
        total=self.db.execute('SELECT count(*) AS n FROM {oa} WHERE '+where,args).fetchone()['n']
        rows=self.db.execute('SELECT * FROM {oa} WHERE '+where+
            ' ORDER BY created_at DESC,workspace_key LIMIT %s OFFSET %s',(*args,limit,offset)).fetchall()
        return {'items':[self.summary(r) for r in rows],'total':total,'offset':offset,'limit':limit,
                'can_confirm':self.ws.can_write(user_id,block)}

    @staticmethod
    def summary(row):
        return {'id':row['workspace_key'],'revision':row['version'],'scope':row['payload']['scope'],
                'reported_by':row['payload']['actor']['subject'],'submitter_verified':False,
                'delivery_state':row['state']['delivery_state'],'created_at':row['created_at'].isoformat(),
                'decision':row['state'].get('intake_decision'),
                'blockers':row['state'].get('receipt',{}).get('blockers',[])}

    def detail(self, user_id, block_id, sid, *, offset=0, limit=50):
        row,block=self.row(user_id,block_id,sid)
        check(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=100,'INVALID_REQUEST')
        manifest=row['state'].get('manifest')
        result={**self.summary(row),'can_confirm':self.ws.can_write(user_id,block),
                'content_sha256':row['payload']['content_sha256'],'offset':offset,'limit':limit}
        if not manifest:
            return {**result,'scope_sha256':None,'manifest_sha256':None,'units':[],'total_units':0}
        reports=manifest['reports']; plan=manifest['plan']
        units=[{**unit,'parse_status':report['result']['parse_status'],
                'record_count':report['result']['record_count'],'raw_sha256':report['raw_sha256'],
                'blockers':report['result']['blockers']}
               for unit,report in zip(plan['inventory']['units'][offset:offset+limit],reports[offset:offset+limit])]
        return {**result,'scope_sha256':scope_digest(plan),'manifest_sha256':digest(manifest),
                'inventory_source':plan['inventory']['source'],'inventory_version':plan['inventory']['version'],
                'global_blockers':manifest['global_blockers'],'units':units,'total_units':len(reports),
                'preserved_files':manifest.get('preserved_files', [])}

    def decide(self, user_id, block_id, sid, *, decision, expected_revision, request_id, reason,
               content_sha256, manifest_sha256, scope_sha256, confirmed_scope):
        sid=submission_uuid(sid); request_id=submission_uuid(str(request_id))
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('oa-submission-'+sid),))
        row,_=self.row(user_id,block_id,sid,write=True)
        check(decision in ('CONFIRM','REJECT') and type(expected_revision) is int and expected_revision>=0
              and text(reason,2000) and type(confirmed_scope) is bool,'INVALID_REQUEST')
        check(decision!='CONFIRM' or confirmed_scope,'SOURCE_SCOPE_CONFIRMATION_REQUIRED')
        command=dict(decision=decision,expected_revision=expected_revision,request_id=request_id,reason=reason,
                     content_sha256=content_sha256,manifest_sha256=manifest_sha256,scope_sha256=scope_sha256,
                     confirmed_scope=confirmed_scope)
        old=row['state'].get('intake_decision')
        if old:
            check(old['user_id']==user_id and old['command_sha256']==digest(command),'REQUEST_CONFLICT')
            return self.summary(row)
        check(row['version']==expected_revision,'REVISION_CONFLICT')
        check(row['state']['delivery_state']=='AWAITING_CONFIRMATION','SOURCE_INTAKE_NOT_READY')
        manifest=row['state']['manifest']
        check(content_sha256==row['payload']['content_sha256'] and manifest_sha256==digest(manifest)
              and scope_sha256==scope_digest(manifest['plan']),'REVISION_CONFLICT')
        event={**command,'user_id':user_id,'confirmed_at':datetime.now(timezone.utc).isoformat(),
               'command_sha256':digest(command)}
        receiver=SourceReceiver(self.db,None,workspace=self.ws)
        row['state']={**row['state'],'intake_decision':event}
        if decision=='REJECT':
            receipt=receiver.receipt(row['payload'],'REJECTED',['OA_OWNER_REJECTED'],[])
            state={**row['state'],'delivery_state':'REJECTED','receipt':receipt}
        else:
            state=receiver.apply(row['payload'],row)
            check(state['delivery_state']=='OA_STORED','SOURCE_INTAKE_NOT_READY')
        receiver.update(row,state)
        return self.summary(self.ws.get('WS_SUBMISSION',sid))
