"""Minimal removal fences. They contain no report bodies or human judgments."""
from datetime import datetime

from common.sanity_common import digest
from common.sanity_db import stable_uuid
from common.sanity_submission import check, source_version

PREFIX = 'ws.removed.'
NAMESPACE = 'workspace-removal-v1'
PRIVATE_REMOVED = 'vwp-submission-removed-v1'


def stem(kind,identity):
    check(kind in ('REPORT','VERSION','SUBMISSION'),'INVALID_REQUEST')
    return PREFIX+digest([kind,identity])+'.'


def marker(db,kind,identity):
    prefix=stem(kind,identity)
    row=db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                   "ORDER BY workspace_key DESC LIMIT 1",(prefix+'%',)).fetchone()
    if row is None:return None
    value=row['payload']
    check(isinstance(value,dict) and set(value)=={'namespace','kind','id','block_id','actor_id','at','revision','sha256'}
          and value['namespace']==NAMESPACE and value['kind']==kind and value['id']==identity
          and type(value['revision']) is int and value['revision']>=1
          and row['workspace_key']==prefix+f"{value['revision']:010d}"
          and row['record_id']==stable_uuid('workspace-v1','WS_CONFIG',row['workspace_key'])
          and row['state']=={} and row['version']==0 and row['status']=='RECORDED'
          and row['created_at']==row['updated_at']
          and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}),'REMOVAL_CORRUPT')
    check(datetime.fromisoformat(value['at']).utcoffset() is not None,'REMOVAL_CORRUPT')
    return value


def mark(ws,kind,identity,block_id,actor_id):
    old=marker(ws.db,kind,identity)
    at=ws.db.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()
    value={'namespace':NAMESPACE,'kind':kind,'id':identity,'block_id':block_id,'actor_id':actor_id,
           'at':at,'revision':old['revision']+1 if old else 1}
    value['sha256']=digest(value)
    ws.put('WS_CONFIG',stem(kind,identity)+f"{value['revision']:010d}",value)
    return value


def check_packet(db,packet):
    check(marker(db,'SUBMISSION',packet['submission_id']) is None,'SOURCE_REMOVED')
    scope=packet['scope'];sources=[{'net_ver':scope['net_ver'],'revision':scope['submission_revision'],
                                  'layout':scope['layout']}]
    if scope['pre_revision'] is not None:
        sources.append({'net_ver':scope['net_ver'],'revision':scope['pre_revision'],'layout':'PRE'})
    for source in sources:
        removed=marker(db,'VERSION',source_version(scope,source))
        if removed:
            submitted=packet.get('submitted_at')
            check(submitted is not None and datetime.fromisoformat(submitted)>datetime.fromisoformat(removed['at']),
                  'SOURCE_REMOVED')


def check_private_submission(row):
    check(row is None or row['payload'].get('namespace')!=PRIVATE_REMOVED,'SOURCE_REMOVED')
