"""Receive V2 source pages through PostgreSQL; publish only complete collections."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import re
import sys
from urllib.parse import urlencode
import uuid

from common.sanity_common import ReviewError, canonical_json, digest, unpack_json
from common.sanity_db import Database, Queue, json_value, lock_key
from common.sanity_parsers import valid_json_pointer, validate_builtin_evaluation
from common.sanity_privacy import SecretGuard
from common.sanity_submission import (HEX, PART_BYTES, PART_RECORDS, PROTOCOL, SOURCE_TYPES, check,
    message_id, scope_digest, source_execution, source_unit, source_version, validate_packet, validate_plan)
from common.sanity_submission_store import require_schema
from oa.sanity_workspace import WorkspaceStore, record_id
from oa.sanity_production_workspace import ProductionWorkspaceStore


def validate_record(record):
    check(isinstance(record,dict) and {'id','fields','check_status','raw','line_start','line_end',
        'reasons','evidence_id','source_raw_sha256','redacted'} <= set(record)
        and set(record) <= {'id','fields','check_status','raw','line_start','line_end','reasons',
                           'evidence_id','source_raw_sha256','redacted','byte_start','byte_end','json_pointer'},'INVALID_SOURCE_RECORD')
    check(isinstance(record['id'],str) and record['id'].startswith('r-') and HEX.fullmatch(record['id'][2:])
          and record['evidence_id']==record['id'] and isinstance(record['fields'],dict)
          and isinstance(record['raw'],str) and record['check_status'] in ('PASS','FAIL','UNKNOWN')
          and type(record['redacted']) is bool and isinstance(record['source_raw_sha256'],str)
          and HEX.fullmatch(record['source_raw_sha256']),'INVALID_SOURCE_RECORD')
    check(type(record['line_start']) is int and type(record['line_end']) is int
          and 1 <= record['line_start'] <= record['line_end']
          and isinstance(record['reasons'],list) and all(isinstance(r,str) for r in record['reasons']),'INVALID_SOURCE_RECORD')
    if 'byte_start' in record or 'byte_end' in record:
        check(type(record.get('byte_start')) is int and type(record.get('byte_end')) is int
              and 0 <= record['byte_start'] <= record['byte_end'],'INVALID_SOURCE_RECORD')
    if 'json_pointer' in record:
        check(valid_json_pointer(record['json_pointer']),'INVALID_SOURCE_RECORD')
    if not record['redacted']:
        check(hashlib.sha256(record['raw'].encode()).hexdigest()==record['source_raw_sha256'],'INVALID_SOURCE_RECORD')


class SourceReceiver:
    def __init__(self, db, identity, *, workspace=None):
        check(db.side=='oa','FORBIDDEN')
        require_schema(db)
        self.db, self.ws, self.identity = db, WorkspaceStore(db) if workspace is None else workspace, identity
        check(self.ws.db is db,'AUTHORIZATION_CONFIG')

    @contextmanager
    def transaction(self):
        with self.db.transaction():
            if isinstance(self.ws, ProductionWorkspaceStore):
                from oa.sanity_access import AccessStore, access_lock
                self.db.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
                access_lock(self.db)
                # A receiver lives across many polls; its permission snapshot
                # must not outlive the transaction that protects that snapshot.
                self.ws = ProductionWorkspaceStore(self.db,
                    AccessStore(self.db,self.ws.authorization.administrator_id))
            yield

    def existing(self, kind, key):
        return self.db.execute('SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s',(kind,key)).fetchone()

    def update(self, row, state):
        self.db.execute('UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() WHERE record_id=%s',
                        (json_value(state),row['record_id']))

    def authorize(self, packet):
        actor, scope = packet['actor'], packet['scope']
        local_id = self.identity.resolve(actor)
        check(local_id==actor['user_id'],'SOURCE_IDENTITY_MISMATCH')
        block, project = self.ws.access(local_id,scope['block_key'],write=True)
        check(block['payload']['project_id']==scope['project_key'] and project['workspace_key']==scope['project_key'],
              'SOURCE_SCOPE_MISMATCH')
        return local_id

    def submission(self, packet):
        key, value = packet['submission_id'], {k:v for k,v in packet.items() if k!='body'}
        row = self.existing('WS_SUBMISSION',key)
        if row:
            check(row['payload']==value,'SUBMISSION_CONFLICT')
            return row
        self.ws.put('WS_SUBMISSION',key,value,project=packet['scope']['project_key'],block=packet['scope']['block_key'],
                    state={'delivery_state':'IMPORTING','seen_parts':[]})
        return self.existing('WS_SUBMISSION',key)

    def part(self, packet):
        value = packet['body']
        check(set(value)=={'id','body'} and isinstance(value['body'],dict),'INVALID_SOURCE_PART')
        body = value['body']
        check(set(body)=={'report_id','part_index','records'} and value['id']=='sp-'+digest(body)
              and isinstance(body['report_id'],str) and body['report_id'].startswith('sr-')
              and type(body['part_index']) is int and body['part_index']>=0
              and isinstance(body['records'],list) and 1<=len(body['records'])<=PART_RECORDS,'INVALID_SOURCE_PART')
        for record in body['records']:
            validate_record(record)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('source-part-'+value['id']),))
        old = self.existing('WS_SOURCE_PART',value['id'])
        if old:
            check(old['payload']==body and old['workspace_block_ref']==record_id('WS_BLOCK',packet['scope']['block_key'])
                  and old['workspace_project_ref']==record_id('WS_PROJECT',packet['scope']['project_key']),'SOURCE_PART_CONFLICT')
        else:
            self.ws.put('WS_SOURCE_PART',value['id'],body,project=packet['scope']['project_key'],block=packet['scope']['block_key'])
        return value['id']

    def header(self, output, unit, value):
        c, s, result = output['plan']['collection'], unit['source'], value['result']
        check(value['version_id']==source_version(c,s) and value['unit_key']==source_unit(c,unit)
              and value['execution_id']==source_execution(c,unit) and value['source']==s,'SOURCE_SCOPE_MISMATCH')
        for field in ('item_id','family','parser','rule_version'):
            check(value[field]==unit[field],'SOURCE_SCOPE_MISMATCH')
        check(str(uuid.UUID(value['analysis_id']))==value['analysis_id'],'INVALID_SOURCE_MANIFEST')
        created = datetime.fromisoformat(value['analysis_created_at'])
        check(created.utcoffset() is not None and created.astimezone(timezone.utc).isoformat(timespec='microseconds')
              == value['analysis_created_at'],'INVALID_SOURCE_MANIFEST')
        check(value['raw_sha256'] is None or isinstance(value['raw_sha256'],str) and HEX.fullmatch(value['raw_sha256']),
              'INVALID_SOURCE_MANIFEST')
        check(value['report_id']=='sr-'+digest([value['execution_id'],value['raw_sha256'],value['analysis_id']]),
              'INVALID_SOURCE_MANIFEST')
        check(isinstance(value['parts'],list) and isinstance(result,dict)
              and type(result['record_count']) is int and result['record_count']>=0
              and result['parse_status'] in ('COMPLETE','PARTIAL','MISSING','UNSUPPORTED','NOT_APPLICABLE')
              and result['check_status'] in ('PASS','FAIL','UNKNOWN','NOT_APPLICABLE')
              and result['condition']==unit['condition'] and result['execution_status']==unit['execution_status']
              and result['applicable']==unit['applicable'],'INVALID_SOURCE_MANIFEST')
        check(isinstance(result['status_counts'],dict) and set(result['status_counts'])=={'PASS','FAIL','UNKNOWN'}
              and all(type(n) is int and n>=0 for n in result['status_counts'].values())
              and sum(result['status_counts'].values())==result['record_count'],'SOURCE_COVERAGE_MISMATCH')
        check(isinstance(result['blockers'],list) and len(result['blockers'])<=100
              and all(isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',code) for code in result['blockers']),
              'INVALID_SOURCE_MANIFEST')
        check(type(value['record_bytes']) is int and value['record_bytes']>=0
              and type(value['raw_line_count']) is int and value['raw_line_count']>=0,'INVALID_SOURCE_MANIFEST')
        ids = []
        for part in value['parts']:
            check(set(part)=={'id','sha256','count'} and part['id']=='sp-'+part['sha256']
                  and isinstance(part['sha256'],str) and HEX.fullmatch(part['sha256'])
                  and type(part['count']) is int and 1<=part['count']<=PART_RECORDS,'INVALID_SOURCE_MANIFEST')
            ids.append(part['id'])
        check(len(ids)==len(set(ids)) and sum(p['count'] for p in value['parts'])==result['record_count'],
              'SOURCE_COVERAGE_MISMATCH')

    def validate_manifest(self, packet):
        output = packet['body']
        check(set(output)=={'plan','inventory_confirmed','global_blockers','reports','producer'},'INVALID_SOURCE_MANIFEST')
        validate_plan(output['plan'])
        check(output['plan']['collection']==packet['scope'] and type(output['inventory_confirmed']) is bool
              and isinstance(output['global_blockers'],list)
              and set(output['global_blockers'])<={'INVENTORY_UNCONFIRMED','UNEXPECTED_REPORTS'},'SOURCE_SCOPE_MISMATCH')
        if output['inventory_confirmed']:
            approval = output['plan']['approval']
            check(output['plan']['inventory']['confirmed'] and approval and approval['scope_sha256']==scope_digest(output['plan']),
                  'SOURCE_APPROVAL_MISMATCH')
        else:
            check('INVENTORY_UNCONFIRMED' in output['global_blockers'],'SOURCE_APPROVAL_MISMATCH')
        units = output['plan']['inventory']['units']
        check(isinstance(output['reports'],list) and len(output['reports'])==len(units),'SOURCE_COVERAGE_MISMATCH')
        for unit,value in zip(units,output['reports']):
            if unit['applicable']:
                validate_builtin_evaluation(unit.get('parser'),unit)
            self.header(output,unit,value)
        check(packet['collection_id']=='sc-'+digest([packet['content_sha256'],output['inventory_confirmed'],
              output['producer'],[r['parser_contract'] for r in output['reports']]]),'SOURCE_MANIFEST_CONFLICT')
        return output

    def apply(self, packet, row):
        state = row['state']
        if state['delivery_state'] in ('OA_STORED','REJECTED'):
            return state
        if not state.get('manifest') or not state.get('commit'):
            return state
        # Staging may predate this receiver release. A valid content hash does
        # not prove that cached data satisfies today's normalization rules.
        guard = SecretGuard()
        guard.ensure_public(state['manifest'])
        output = self.validate_manifest({**packet,'body':state['manifest']})
        expected = [p['id'] for r in output['reports'] for p in r['parts']]
        check(len(expected)==len(set(expected)),'SOURCE_PART_CONFLICT')
        check(state['commit']=={'manifest_sha256':digest(output),'parts':len(expected)},'SOURCE_COMMIT_CONFLICT')
        check(set(state['seen_parts'])<=set(expected),'SOURCE_PART_CONFLICT')
        if set(expected)!=set(state['seen_parts']):
            return state
        self.authorize(packet)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('source-block-'+packet['scope']['block_key']),))
        report_ids, blockers = [], set(output['global_blockers'])
        for value in output['reports']:
            counts, index, record_bytes, line_count = Counter(), 0, 0, 0
            fid = 'f-'+digest([value['execution_id'],value['raw_sha256']])
            for part_index, ref in enumerate(value['parts']):
                part_row = self.existing('WS_SOURCE_PART',ref['id'])
                check(part_row,'SOURCE_PART_MISSING')
                part = part_row['payload']
                check(digest(part)==ref['sha256'] and part['report_id']==value['report_id']
                      and part['part_index']==part_index and len(part['records'])==ref['count'], 'SOURCE_PART_CONFLICT')
                guard.ensure_public(part)
                record_bytes += len(canonical_json(part['records']))
                for record in part['records']:
                    validate_record(record)
                    index += 1
                    check(record['id']=='r-'+digest([value['execution_id'],value['unit_key'],fid,index]),'SOURCE_RECORD_ID_MISMATCH')
                    counts[record['check_status']] += 1
                    line_count = max(line_count,record['line_end'])
            result = value['result']
            check(index==result['record_count'] and record_bytes==value['record_bytes'] and line_count<=value['raw_line_count']
                  and all(counts[k]==n for k,n in result['status_counts'].items()),'SOURCE_COVERAGE_MISMATCH')
            existing = self.db.execute("SELECT payload->>'raw_sha256' AS raw,payload->>'execution_id' AS execution_id,"
                "(payload->'source'->>'execution_order')::bigint AS ordinal FROM {oa} WHERE record_type='WS_REPORT' "
                "AND payload->>'source_unit_key'=%s",(value['unit_key'],)).fetchall()
            for old in existing:
                if old['execution_id']==value['execution_id']:
                    check(not old['raw'] or not value['raw_sha256'] or old['raw']==value['raw_sha256'],'SOURCE_IDENTITY_CONFLICT')
                else:
                    check(old['ordinal']!=value['source']['execution_order'],'SOURCE_ORDER_CONFLICT')
            blockers.update(result['blockers'])
            if result['parse_status'] not in ('COMPLETE','NOT_APPLICABLE'):
                blockers.add(result['parse_status'])
            report_ids.append(value['report_id'])
        check(len(blockers)<=256,'SOURCE_BLOCKER_LIMIT')
        # PRE must exist before a POST points to it. Version metadata never changes.
        for value in sorted(output['reports'],key=lambda r:r['source']['layout']!='PRE'):
            self.save_version(packet['scope'],value)
        links = []
        for value in output['reports']:
            self.save_report(packet['scope'],value,output['inventory_confirmed'])
            if value['source']['layout']==packet['scope']['layout'] and value['source']['eco']==packet['scope']['target_eco']:
                params = {'project':packet['scope']['project_key'],'block':packet['scope']['block_key'],
                    'version':value['version_id'],'tab':'reports'}
                if value['source']['eco'] is not None:
                    params['eco'] = value['source']['eco']
                link = ('/?'+urlencode({'page':'workbench',**params})
                        if isinstance(self.ws,ProductionWorkspaceStore) else '/workspace?'+urlencode(params))
                if link not in links:
                    links.append(link)
        receipt = self.receipt(packet,'OA_STORED',sorted(blockers),links)
        return {**state,'delivery_state':'OA_STORED','receipt':receipt,'reports':report_ids,
                'manifest':None,'commit':None,'seen_parts':state['seen_parts'],
                'collection_blockers':output['global_blockers']}

    def save_version(self, collection, value):
        s, vid = value['source'], value['version_id']
        pre = None
        if s['layout']=='POST' and collection['pre_revision'] is not None:
            pre = source_version(collection,{'net_ver':collection['net_ver'],'revision':collection['pre_revision'],'layout':'PRE'})
        payload = {'id':vid,'project_id':collection['project_key'],'block_id':collection['block_key'],
            'net_ver':s['net_ver'],'revision':s['revision'],'layout':s['layout'],
            'ecos':[None] if s['layout']=='PRE' else [],'source_pre_id':pre,'source_namespace':collection['source_namespace']}
        old = self.existing('WS_VERSION',vid)
        if old:
            check(old['payload']==payload,'SOURCE_VERSION_CONFLICT')
        else:
            self.ws.put('WS_VERSION',vid,payload,project=collection['project_key'],block=collection['block_key'])

    def save_report(self, collection, value, confirmed):
        result = value['result']
        global_blockers = [] if confirmed else ['INVENTORY_UNCONFIRMED']
        payload = {'id':value['report_id'],'project_id':collection['project_key'],'block_id':collection['block_key'],
            'version_id':value['version_id'],'version_record_id':str(record_id('WS_VERSION',value['version_id'])),
            'eco':value['source']['eco'],'item_id':value['item_id'],'family':value['family'],'rule':value['item_id'],
            'parser':value['parser'],'rule_version':value['rule_version'],'check_status':result['check_status'],
            'parse_status':result['parse_status'],'record_count':result['record_count'],
            'blockers':sorted(set(global_blockers+result['blockers'])),
            'analysis_ready':not global_blockers and not result['blockers'] and result['parse_status']=='COMPLETE',
            'source_storage':'PARTS_V2','source_complete':True,'data_origin':'VWP_SUBMISSION',
            'source_unit_key':value['unit_key'],'execution_id':value['execution_id'],'source':value['source'],
            'analysis_id':value['analysis_id'],'raw_sha256':value['raw_sha256'],'source_parts':value['parts'],
            'analysis_created_at':value['analysis_created_at'],
            'source_bytes':value['record_bytes'],'raw_line_count':value['raw_line_count']}
        old = self.existing('WS_REPORT',payload['id'])
        if old:
            check(old['payload']==payload,'SOURCE_REPORT_CONFLICT')
        else:
            self.ws.put('WS_REPORT',payload['id'],payload,project=collection['project_key'],block=collection['block_key'])

    def receipt(self, packet, delivery, blockers, links):
        value = {k:packet[k] for k in ('schema_version','protocol','submission_id','content_sha256','collection_id')}
        value.update(actor_key=digest([packet['actor']['issuer'],packet['actor']['subject']]),delivery_state=delivery,
            review_readiness='BLOCKED' if blockers or delivery!='OA_STORED' else 'READY',blockers=blockers,workspace_links=links)
        Queue(self.db,'oa_to_vwp').enqueue({'message_id':message_id('SOURCE_RECEIPT',value),'message_type':'SOURCE_RECEIPT',
            'project':packet['scope']['project_key'],'run_id':None,'bundle_id':None,'payload':value})
        return value

    def receive_one(self, message_key=None):
        queue = Queue(self.db,'vwp_to_oa')
        message = queue.claim(SOURCE_TYPES,message_key)
        if not message:
            return {'status':'NO_MESSAGE'}
        packet, trusted = None, False
        try:
            packet = validate_packet(unpack_json(message,max_bytes=PART_BYTES))
            check(message['message_id']==message_id(message['message_type'],packet)
                  and message['project']==packet['scope']['project_key'] and message['correlation_id']==packet['submission_id']
                  and message['run_id'] is None and message['bundle_id'] is None,'SOURCE_SCOPE_MISMATCH')
            check(message['payload']=={k:packet[k] for k in ('schema_version','protocol','submission_id','content_sha256','collection_id')},
                  'SOURCE_SCOPE_MISMATCH')
            SecretGuard().ensure_public(packet)
            trusted = True
            with self.transaction():
                self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('oa-submission-'+packet['submission_id']),))
                self.authorize(packet)
                row = self.submission(packet)
                if row['state']['delivery_state'] in ('OA_STORED','REJECTED'):
                    queue.finish(message)
                    return {'status':row['state']['delivery_state'],'submission_id':packet['submission_id']}
                state = dict(row['state'])
                if message['message_type']=='SOURCE_MANIFEST':
                    output = self.validate_manifest(packet)
                    check(not state.get('manifest') or state['manifest']==output,'SOURCE_MANIFEST_CONFLICT')
                    state['manifest'] = output
                elif message['message_type']=='SOURCE_PART':
                    part_id = self.part(packet)
                    state['seen_parts'] = sorted(set(state['seen_parts'])|{part_id})
                else:
                    check(set(packet['body'])=={'manifest_sha256','parts'} and isinstance(packet['body']['manifest_sha256'],str)
                          and HEX.fullmatch(packet['body']['manifest_sha256']) and type(packet['body']['parts']) is int
                          and packet['body']['parts']>=0,'INVALID_SOURCE_COMMIT')
                    check(not state.get('commit') or state['commit']==packet['body'],'SOURCE_COMMIT_CONFLICT')
                    state['commit'] = packet['body']
                row['state'] = state
                state = self.apply(packet,row)
                self.update(row,state)
                queue.finish(message)
            return {'status':state['delivery_state'],'submission_id':packet['submission_id']}
        except (ReviewError,KeyError,TypeError,ValueError) as error:
            code = error.code if isinstance(error,ReviewError) else 'INVALID_SOURCE_MESSAGE'
            if code=='LEASE_LOST':
                raise
            with self.transaction():
                if trusted:
                    row = self.existing('WS_SUBMISSION',packet['submission_id'])
                    if not row:
                        self.receipt(packet,'REJECTED',[code],[])
                    elif row['payload']=={k:v for k,v in packet.items() if k!='body'} and row['state']['delivery_state']=='IMPORTING':
                        receipt = self.receipt(packet,'REJECTED',[code],[])
                        self.update(row,{**row['state'],'delivery_state':'REJECTED','receipt':receipt})
                queue.finish(message,'REJECTED',code)
            raise ReviewError(code,'Source submission was rejected') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=2)
    args = parser.parse_args(argv)
    try:
        from oa.integration.corporate_submission import CorporateSourceIdentity
        from common.sanity_workers import poll
        from common.sanity_config import load_environment
        from oa.sanity_access import AccessStore, administrator_id
        with Database('oa') as db:
            access = AccessStore(db,administrator_id(load_environment()))
            receiver = SourceReceiver(db,CorporateSourceIdentity(),workspace=ProductionWorkspaceStore(db,access))
            print(poll(receiver.receive_one,args.once,args.interval))
        return 0
    except ReviewError as exc:
        print('code='+exc.code,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
