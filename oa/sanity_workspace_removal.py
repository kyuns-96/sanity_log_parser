"""Owner-authorized physical report/version removal on an existing shared login.

All four tables change in one transaction. Shared bytes stay until their last
reference is removed; only small replay fences remain for removed identities.
No privileges, schema, or filesystem contents are changed by this service.
"""
from types import SimpleNamespace

from common.sanity_common import digest
from common.sanity_db import json_value, lock_key, uuid_value, stable_uuid
from common.sanity_submission import check, message_id, source_unit, source_version, source_execution
from common.sanity_removal import mark, marker, PRIVATE_REMOVED
from oa.sanity_workspace import record_id

REQUEST_PREFIX = 'ws.removal.request.'
KINDS = ('WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL','WS_CLUSTER','WS_CLUSTER_EVENT',
         'WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')


def available(db):
    if getattr(db,'side',None)!='oa' or getattr(db,'privilege_mode',None)!='single_account':return False
    row=db.execute("SELECT bool_and(has_table_privilege(current_user,quote_ident(%s)||'.'||name,'SELECT') "
        "AND has_table_privilege(current_user,quote_ident(%s)||'.'||name,'DELETE')) AS allowed "
        "FROM unnest(%s::text[]) name",(db.schema,db.schema,
        ['sanity_oa_data','sanity_vwp_data','sanity_vwp_to_oa','sanity_oa_to_vwp'])).fetchone()
    return bool(row and row['allowed'])


class Removal:
    def __init__(self,ws):self.ws,self.db=ws,ws.db

    def _targets(self,user_id,block_id,version_id,report_id=None):
        block,_=self.ws.access(user_id,block_id,write=True)
        version=self.ws.version(user_id,block_id,version_id)
        query="SELECT record_id,payload-'records'-'raw_report' AS payload,version,updated_at "
        query+="FROM {oa} WHERE record_type='WS_REPORT' AND workspace_version_ref=%s"
        params=[version['record_id']]
        if report_id is not None:query+=' AND workspace_key=%s';params.append(report_id)
        reports=self.db.execute(query+' ORDER BY workspace_key',params).fetchall()
        check(report_id is None or len(reports)==1,'NOT_FOUND')
        return block,version,reports

    def _preview(self,user_id,block_id,version_id,report_id=None):
        block,version,reports=self._targets(user_id,block_id,version_id,report_id)
        refs=[r['record_id'] for r in reports]
        heads=self.db.execute("SELECT record_id,version,updated_at FROM {oa} WHERE record_type IN "
            "('WS_REVIEW','WS_CLUSTER','WS_GROUP_REVIEW') AND (workspace_review_report_ref=ANY(%s) "
            "OR workspace_pipeline_report_ref=ANY(%s)) ORDER BY record_id",(refs,refs)).fetchall()
        metadata=lambda r:[str(r['record_id']),r['version'],r['updated_at'].isoformat()]
        snapshot=digest([metadata(version),[metadata(r) for r in reports],[metadata(r) for r in heads]])
        blockers=[]
        if report_id is None:
            current=self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_POR' AND workspace_version_ref=%s",
                (version['record_id'],)).fetchone()
            if current:blockers.append('POR_VERSION_IN_USE')
            dependent=self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_VERSION' AND "
                "payload->>'source_pre_id'=%s LIMIT 1",(version_id,)).fetchone()
            if dependent:blockers.append('VERSION_REFERENCED')
        ready=available(self.db)
        if not ready:blockers.append('DATA_DELETE_NOT_CONFIGURED')
        return {'kind':'REPORT' if report_id else 'VERSION','id':report_id or version_id,
            'report_count':len(reports),'record_count':sum(r['payload'].get('record_count',0) for r in reports),
            'review_count':len(heads),'snapshot_sha256':snapshot,'can_delete':not blockers,'blockers':blockers},reports

    def preview(self,*args):return self._preview(*args)[0]

    def delete(self,*args,**kwargs):
        with self.db.transaction():return self._delete(*args,**kwargs)

    def _delete(self,user_id,block_id,version_id,*,request_id,snapshot_sha256,report_id=None):
        self.ws.access(user_id,block_id,write=True)
        identity=str(uuid_value(request_id))
        command={'user_id':user_id,'block_id':block_id,'version_id':version_id,'report_id':report_id,
                 'snapshot_sha256':snapshot_sha256}
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(REQUEST_PREFIX+identity),))
        prior=self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                              (REQUEST_PREFIX+identity,)).fetchone()
        if prior:
            check(prior['payload'].get('namespace')=='workspace-removal-request-v1'
                  and prior['payload'].get('command_sha256')==digest(command),'REQUEST_CONFLICT')
            return prior['payload']['result']
        from oa.sanity_ai_jobs import REVIEW_LOCK
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(REVIEW_LOCK,))
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('source-block-'+block_id),))
        preview,reports=self._preview(user_id,block_id,version_id,report_id)
        check(preview['can_delete'],preview['blockers'][0] if preview['blockers'] else 'FORBIDDEN')
        check(preview['snapshot_sha256']==snapshot_sha256,'REMOVAL_CHANGED')
        report_ids=[r['payload']['id'] for r in reports];report_refs=[r['record_id'] for r in reports]
        submissions=self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SUBMISSION' "
            "AND workspace_block_ref=%s AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(state->'reports') x "
            "WHERE x=ANY(%s)) ORDER BY workspace_key",(record_id('WS_BLOCK',block_id),report_ids)).fetchall()
        if report_id is None:
            submissions=self._pending(submissions,block_id,version_id)
        from common.sanity_raw_sources import RAW_PROTOCOL
        for submission in submissions:
            sid=submission['workspace_key']
            for key in ('submission-v2-'+sid,RAW_PROTOCOL+':'+sid,'oa-submission-'+sid):
                locked=self.db.execute('SELECT pg_try_advisory_xact_lock(%s) AS allowed',(lock_key(key),)).fetchone()
                check(locked['allowed'],'SUBMISSION_BUSY')
        # Fence every related job, including a different current OWNER's job.
        # Target authorization above is separate from the job-card owner rule.
        jobs=self.db.execute("SELECT DISTINCT payload->'data'->'command'->>'request_id' AS id,"
            "payload->'data'->'command'->>'user_id' AS actor FROM {oa} WHERE record_type='WS_CONFIG' "
            "AND payload->>'namespace' IN ('oa-ai-v1','oa-ai-submission-v1') "
            "AND payload->'data'->'command'->>'report_id'=ANY(%s)",(report_ids,)).fetchall()
        service=SimpleNamespace(db=self.db,ws=self.ws)
        from oa.sanity_ai_deletion import purge_locked
        for job in sorted(jobs,key=lambda j:j['id']):purge_locked(service,job['id'],job['actor'])
        self.db.execute("DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND payload->>'namespace'=%s "
                        "AND payload->>'report_id'=ANY(%s)",('ai-evidence-choice-v1',report_ids))
        remaining=self.db.execute("SELECT payload-'records'-'raw_report' AS payload FROM {oa} WHERE "
            "record_type='WS_REPORT' AND workspace_block_ref=%s AND NOT(workspace_key=ANY(%s))",
            (record_id('WS_BLOCK',block_id),report_ids)).fetchall()
        parts=lambda h:{p['id'] for p in h.get('source_parts',[])} | {
            p['id'] for f in h.get('preserved_sources',[]) for p in f.get('extraction',{}).get('parts',[])}
        retained_parts=set().union(*(parts(r['payload']) for r in remaining)) if remaining else set()
        removed_parts=(set().union(*(parts(r['payload']) for r in reports)) if reports else set())-retained_parts
        kept_ids={r['payload']['id'] for r in remaining}
        for submission in submissions:
            if not set(submission['state'].get('reports',[])) & kept_ids:
                removed_parts.update(set(submission['state'].get('seen_parts',[]))-retained_parts)
        part_rows=self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART' AND workspace_key=ANY(%s)",
                                  (list(removed_parts),)).fetchall()
        self._private(reports,remaining,submissions,part_rows,block_id,user_id)
        # One statement removes self-referencing heads/events before FK checks.
        self.db.execute("DELETE FROM {oa} WHERE (record_type='WS_REPORT' AND record_id=ANY(%s)) OR "
            "(record_type=ANY(%s) AND (workspace_review_report_ref=ANY(%s) OR workspace_pipeline_report_ref=ANY(%s))) "
            "OR (record_type='WS_SOURCE_PART' AND workspace_key=ANY(%s))",
            (report_refs,list(KINDS),report_refs,report_refs,list(removed_parts)))
        for report in reports:mark(self.ws,'REPORT',report['payload']['id'],block_id,user_id)
        for submission in submissions:mark(self.ws,'SUBMISSION',submission['workspace_key'],block_id,user_id)
        self._packets(submissions,part_rows)
        if report_id is None:
            ref=record_id('WS_VERSION',version_id)
            self.db.execute("DELETE FROM {oa} WHERE (record_type='WS_POR_EVENT' AND workspace_version_ref=%s) "
                "OR (record_type='WS_VERSION' AND record_id=%s)",(ref,ref))
            mark(self.ws,'VERSION',version_id,block_id,user_id)
        result={'deleted':True,'kind':preview['kind'],'id':preview['id'],'report_count':len(reports)}
        self.ws.put('WS_CONFIG',REQUEST_PREFIX+identity,{'namespace':'workspace-removal-request-v1',
            'command_sha256':digest(command),'result':result})
        return result

    def _pending(self,submissions,block_id,version_id):
        known={s['workspace_key'] for s in submissions};result=list(submissions)
        candidates=self.db.execute("SELECT payload,state,status FROM {vwp} WHERE record_type='SUBMISSION' "
            "AND payload->'plan'->'collection'->>'block_key'=%s AND status IN ('VWP_ACCEPTED','PARSING','DB_QUEUED')",
            (block_id,)).fetchall()
        raw=self.db.execute("SELECT r.payload,r.created_at FROM {vwp} r WHERE r.record_type='RAW_SUBMISSION' "
            "AND r.payload#>>'{{request,submission,plan,collection,block_key}}'=%s AND NOT EXISTS "
            "(SELECT 1 FROM {vwp} s WHERE s.record_type='SUBMISSION' "
            "AND s.entity_key=r.payload#>>'{{request,submission,submission_id}}')",(block_id,)).fetchall()
        for row in raw:
            value=row['payload'];submission=value['request']['submission']
            candidates.append({'payload':{**submission,'node_id':value['node_id']},'state':{},'status':'VWP_ACCEPTED'})
        for candidate in candidates:
            value=candidate['payload'];sid=value['submission_id'];plan=value['plan']
            if sid in known or not any(source_version(plan['collection'],u['source'])==version_id
                                       for u in plan['inventory']['units']):continue
            row=self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",(sid,)).fetchone()
            result.append(row or {'workspace_key':sid,'payload':{'scope':plan['collection']},
                                 'state':{'reports':[],'seen_parts':[]},'_private':candidate})
            known.add(sid)
        return sorted(result,key=lambda s:s['workspace_key'])

    def _private(self,reports,remaining,submissions,part_rows,block_id,actor):
        removed=[r['payload'] for r in reports];kept=[r['payload'] for r in remaining]
        analyses={r['analysis_id'] for r in removed if r.get('analysis_id')}-{r.get('analysis_id') for r in kept}
        executions={r['execution_id'] for r in removed if r.get('execution_id')}-{r.get('execution_id') for r in kept}
        chunks=set()
        from common.sanity_preserved_sources import declarations,anchors
        for submission in submissions:
            sid=submission['workspace_key']
            row=self.db.execute("SELECT * FROM {vwp} WHERE record_type='SUBMISSION' AND entity_key=%s FOR UPDATE",
                                (sid,)).fetchone()
            candidate=row or submission.get('_private')
            if candidate is None:continue
            value=candidate['payload']
            plan=value['plan'];scope=plan['collection']
            check(scope['block_key']==block_id and scope['project_key']==submission['payload']['scope']['project_key'],
                  'SOURCE_SCOPE_MISMATCH')
            submission_reports=set(submission['state'].get('reports',[]))
            kept_units={r['source_unit_key'] for r in kept if r['id'] in submission_reports and r.get('source_unit_key')}
            full=not any(r['id'] in submission_reports for r in kept)
            if full:
                run_sid=None if candidate['state'].get('analysis_run_mode')=='legacy' else sid
                executions.update(source_execution(scope,u,run_sid) for u in plan['inventory']['units'])
            units=plan['inventory']['units']
            deleted_units={source_unit(scope,u) for u in units}-kept_units
            target_paths={u['report'] for u in units if source_unit(scope,u) in deleted_units}
            kept_paths={u['report'] for u in units if source_unit(scope,u) not in deleted_units}
            for file in declarations(plan):
                keys={source_unit(scope,u) for u in anchors(plan,file)}
                (target_paths if keys<=deleted_units else kept_paths).add(file['path'])
            raw_files=self.db.execute("SELECT record_id,payload FROM {vwp} WHERE record_type='RAW_FILE' "
                "AND payload->>'submission_id'=%s",(sid,)).fetchall()
            delete_files=[r for r in raw_files if full or r['payload']['file']['path'] in target_paths-kept_paths]
            for file in delete_files:chunks.update(file['payload']['chunks'])
            self.db.execute("DELETE FROM {vwp} WHERE record_id=ANY(%s)",([r['record_id'] for r in delete_files],))
            if full:
                self.db.execute("DELETE FROM {vwp} WHERE record_type='RAW_SUBMISSION' AND entity_key=%s",('raws.'+sid,))
            tombstone={'namespace':PRIVATE_REMOVED,'submission_id':sid,'actor':value['actor'],'node_id':value['node_id'],
                       'project_key':scope['project_key'],'content_sha256':value['content_sha256']}
            if not full:tombstone['plan']=plan
            state={'blockers':['SOURCE_REMOVED'],**{k:candidate['state'][k] for k in
                ('collection_id','analysis_run_mode') if k in candidate['state']}}
            if row:
                self.db.execute("UPDATE {vwp} SET payload=%s,state=%s,status='REJECTED',version=version+1,"
                    "updated_at=clock_timestamp() WHERE record_id=%s",
                    (json_value(tombstone),json_value(state),row['record_id']))
            else:
                self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,state,status) "
                    "VALUES (%s,'SUBMISSION',NULL,%s,%s,%s,'REJECTED')",
                    (stable_uuid('submission-v2','SUBMISSION',sid),sid,json_value(tombstone),json_value(state)))
        executions-={r.get('execution_id') for r in kept}
        rows=self.db.execute("SELECT record_id FROM {vwp} WHERE record_type='RUN' AND payload->'run'->>'run_id'=ANY(%s)",
                             (list(executions),)).fetchall()
        analyses.update(str(r['record_id']) for r in rows)
        analyses-={r.get('analysis_id') for r in kept}
        self.db.execute("DELETE FROM {vwp} WHERE analysis_id=ANY(%s) OR "
            "(record_type='SOURCE_RUN' AND entity_key=ANY(%s))",([uuid_value(v) for v in analyses],list(executions)))
        self.db.execute("DELETE FROM {vwp} c WHERE c.record_type='RAW_CHUNK' AND c.entity_key=ANY(%s) "
            "AND NOT EXISTS(SELECT 1 FROM {vwp} f WHERE f.record_type='RAW_FILE' "
            "AND f.payload->'chunks' ? (c.payload->>'sha256'))",(['rawc.'+sha for sha in chunks],))

    def _packets(self,submissions,part_rows):
        from common.sanity_shared_sources import SharedSourcePackets
        shared=SharedSourcePackets(self.db)
        messages=set()
        for row in part_rows:
            body=shared.restore_part(row)['payload']
            refs=self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_SUBMISSION' "
                "AND workspace_block_ref=%s AND state->'seen_parts' ? %s",
                (row['workspace_block_ref'],row['workspace_key'])).fetchall()
            for parent in refs:
                packet={**parent['payload'],'body':{'id':row['workspace_key'],'body':body}}
                messages.add(message_id('SOURCE_PART',packet))
        for submission in submissions:
            sid=submission['workspace_key']
            retained=self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_REPORT' "
                "AND workspace_key IN (SELECT jsonb_array_elements_text(%s::jsonb)) LIMIT 1",
                (json_value(submission['state'].get('reports',[])),)).fetchone()
            if not retained:
                rows=self.db.execute("SELECT message_id FROM {vwp_to_oa} WHERE correlation_id=%s "
                    "AND message_type IN ('SOURCE_MANIFEST','SOURCE_PART','SOURCE_COMMIT')",(sid,)).fetchall()
                messages.update(r['message_id'] for r in rows)
                self.db.execute("DELETE FROM {vwp} WHERE record_type='SOURCE_OUTBOX' AND payload->>'submission_id'=%s",(sid,))
                self.db.execute("DELETE FROM {oa_to_vwp} WHERE message_type='SOURCE_RECEIPT' AND payload->>'submission_id'=%s",(sid,))
                self.db.execute("DELETE FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",(sid,))
        self.db.execute("DELETE FROM {vwp} WHERE record_type='SOURCE_OUTBOX' AND payload->>'message_id'=ANY(%s)",
                        (list(messages),))
        # Schema 013 can retain a packet for another report/outbox. Never erase
        # a shared body until both private-reference sets release it.
        has_shared=shared.enabled
        sql="DELETE FROM {vwp_to_oa} q WHERE q.message_id=ANY(%s)"
        if has_shared:sql+=" AND NOT EXISTS(SELECT 1 FROM {oa} o WHERE o.source_packet_ref=q.message_id) "
        if has_shared:sql+="AND NOT EXISTS(SELECT 1 FROM {vwp} v WHERE v.source_packet_ref=q.message_id)"
        self.db.execute(sql,(list(messages),))
