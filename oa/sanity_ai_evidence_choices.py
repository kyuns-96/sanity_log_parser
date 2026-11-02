"""Explicit human opt-in for shared AI knowledge, separate from review history."""
import re

from common.sanity_clustering import check
from common.sanity_common import digest
from common.sanity_db import lock_key, uuid_value
from oa.sanity_workspace import WorkspaceStore, record_id
import common.sanity_workspace_rationale as rationale

NAMESPACE = 'ai-evidence-choice-v1'
PREFIX = 'ai.evidence.'
REQUEST = 'ai.evidence.request.'


def prefix(stage, report_id):
    check(stage in ('CLUSTER','REVIEW'), 'INVALID_REQUEST')
    # Two 128-bit components keep the immutable key within the existing
    # workspace_key varchar(100), without changing any DB definition.
    return PREFIX + digest([stage,report_id])[:32] + '.'


def binding(event, stage, group_id):
    if stage == 'REVIEW':
        return digest([stage,event['source_sha256'],event['id']])
    group = next((g for g in event['groups'] if g['id']==group_id),None)
    check(group is not None, 'NOT_FOUND')
    return digest([stage,event['source_sha256'],group,event.get('rationales',{}).get(group_id,{}).get('content')])


def selected_case(event, stage, choices):
    groups={g['id'] for g in event.get('groups',[])} if stage=='CLUSTER' else {event.get('group_id')}
    selected = [row for row in choices if row['enabled'] and row['group_id'] in groups and
                row['binding_sha256']==binding(event,stage,row['group_id'])]
    if not selected:return None
    result = dict(event)
    if stage == 'CLUSTER':
        ids={row['group_id'] for row in selected}
        result['groups']=[g for g in event['groups'] if g['id'] in ids]
        result['rationales']={key:row for key,row in event.get('rationales',{}).items() if key in ids}
        # Whole-report notes can describe groups the user did not select.
        result['reason']='사용자가 AI 참고 근거로 등록한 그룹의 분류 기준입니다.'
        result['criteria']=''
        refs={ref['case_id'] for row in result['rationales'].values()
              for ref in row['content']['reference_groups']}
        result['references']=[ref for ref in event.get('references',[]) if ref['id'] in refs]
    else:
        # A judgment's saved snapshot also contains its classification reason.
        # Judgment opt-in must not implicitly publish that separate criterion.
        result.pop('clustering_rationale',None)
    result['evidence_selection_sha256']=digest(sorted(row['sha256'] for row in selected))
    return result


class EvidenceChoices:
    def __init__(self,db,ws=None):
        self.db,self.ws=db,ws if ws is not None else WorkspaceStore(db)

    def rows(self,stage,report_id):
        return self.for_reports(stage,[report_id])[report_id]

    def for_reports(self,stage,report_ids):
        stems={report_id:prefix(stage,report_id) for report_id in report_ids}
        if not stems:return {}
        conditions=' OR '.join('workspace_key LIKE %s' for _ in stems)
        rows=self.db.execute("SELECT DISTINCT ON (payload->>'report_id',payload->>'group_id') * FROM {oa} WHERE "
            "record_type='WS_CONFIG' AND ("+conditions+") "
            "ORDER BY payload->>'report_id',payload->>'group_id',workspace_key DESC",
            tuple(stem+'%' for stem in stems.values())).fetchall()
        result={report_id:[] for report_id in stems}
        for row in rows:
            value=row['payload']
            check(isinstance(value,dict) and isinstance(value.get('report_id'),str)
                  and value['report_id'] in stems,'AI_EVIDENCE_CORRUPT')
            report_id=value['report_id'];stem=stems[report_id]
            keys={'namespace','stage','report_id','group_id','event_id','binding_sha256','enabled',
                  'revision','actor_id','at','sha256'}
            check(isinstance(value,dict) and set(value)==keys and value['namespace']==NAMESPACE
                and value['stage']==stage and value['report_id']==report_id
                and (value['group_id'] is None or isinstance(value['group_id'],str))
                and isinstance(value['event_id'],str) and isinstance(value['actor_id'],str)
                and isinstance(value['at'],str) and type(value['enabled']) is bool
                and isinstance(value['binding_sha256'],str) and re.fullmatch('[0-9a-f]{64}',value['binding_sha256'])
                and type(value['revision']) is int and 1<=value['revision']<=9999999999
                and row['workspace_key']==stem+digest(value['group_id'])[:32]+f".{value['revision']:010d}"
                and row['record_id']==record_id('WS_CONFIG',row['workspace_key'])
                and row['state']=={} and row['version']==0 and row['status']=='RECORDED'
                and row['created_at']==row['updated_at']
                and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}),'AI_EVIDENCE_CORRUPT')
            result[report_id].append(value)
        return result

    def _target(self,user_id,block_id,version_id,report_id,group_id,stage,*,write=False):
        check(stage in ('CLUSTER','REVIEW'),'INVALID_REQUEST')
        from oa.sanity_workspace_review import WorkspaceReviewStore
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore,ai_job_active
        context=WorkspaceReviewStore(self.db,self.ws).context(user_id,block_id,version_id,report_id,
                                                             write=write,hydrate_source=False)
        cluster_store=WorkspaceClusteringStore(self.db,self.ws)
        cluster,_=cluster_store.current(report_id) if group_id is not None else (None,0)
        if group_id is not None:
            check(cluster and any(g['id']==group_id for g in cluster['groups']),'NOT_FOUND')
            check(cluster['source_sha256']==context['source_sha256'],'STATE_CORRUPT')
        if stage=='CLUSTER':
            check(group_id is not None,'INVALID_REQUEST')
            event=cluster
            group=next(g for g in cluster['groups'] if g['id']==group_id)
            reviewed=rationale.state(cluster,group)=='CONFIRMED'
            head=cluster_store.reviews.row('WS_CLUSTER',report_id)
            can_enable=bool(context['ready'] and reviewed and not ai_job_active(head)
                            and not cluster_store.stale(cluster,context))
            eligible=bool(can_enable and cluster['confirmed'] and rationale.complete(cluster))
        else:
            if group_id is not None:
                from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
                event,_=WorkspaceGroupReviewStore(self.db,group_id,ws=self.ws).current(report_id)
            else:event,_=WorkspaceReviewStore(self.db,self.ws).current(report_id)
            check(event is not None,'AI_EVIDENCE_NOT_READY')
            check(event['source_sha256']==context['source_sha256'],'STATE_CORRUPT')
            eligible=bool(context['ready'] and event['conclusion'] in ('PASS','FAIL'))
            if group_id is not None:
                eligible=bool(eligible and cluster['confirmed'] and rationale.complete(cluster)
                    and event['cluster_event_id']==cluster['id'] and event['membership_sha256']==cluster['membership_sha256']
                    and not cluster_store.stale(cluster,context))
            can_enable=eligible
        return context,event,can_enable,eligible

    def view(self,user_id,block_id,version_id,report_id,group_id,stage):
        context,event,can_enable,eligible=self._target(user_id,block_id,version_id,report_id,group_id,stage)
        return self._view(context,event,can_enable,eligible,report_id,group_id,stage)

    def _view(self,context,event,can_enable,eligible,report_id,group_id,stage):
        fingerprint=binding(event,stage,group_id)
        choice=next((r for r in self.rows(stage,report_id) if r['group_id']==group_id),None)
        enabled=bool(choice and choice['enabled'] and choice['binding_sha256']==fingerprint)
        return {'stage':stage,'group_id':group_id,'revision':choice['revision'] if choice else 0,
            'binding_sha256':fingerprint,'enabled':enabled,'eligible':eligible,
            'can_manage':context['can_review'],'can_enable':can_enable,'active':enabled and eligible}

    def save(self,user_id,block_id,version_id,report_id,group_id,stage,*,enabled,expected_revision,
             binding_sha256,request_id):
        check(type(enabled) is bool and type(expected_revision) is int and 0<=expected_revision<9999999999,
              'INVALID_REQUEST')
        identity=str(uuid_value(request_id))
        command={'actor_id':user_id,'block_id':block_id,'version_id':version_id,'report_id':report_id,
                 'group_id':group_id,'stage':stage,'enabled':enabled,'revision':expected_revision,'binding':binding_sha256}
        self.ws.access(user_id,block_id,write=True)
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key('workspace-review-write'),))
        context,event,can_enable,eligible=self._target(user_id,block_id,version_id,report_id,group_id,stage,write=True)
        old=self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                            (REQUEST+identity,)).fetchone()
        if old:
            check(old['payload'].get('namespace')==NAMESPACE and old['payload'].get('command_sha256')==digest(command),
                  'REQUEST_CONFLICT')
            return self._view(context,event,can_enable,eligible,report_id,group_id,stage)
        current=self._view(context,event,can_enable,eligible,report_id,group_id,stage)
        check(current['revision']==expected_revision,'REVISION_CONFLICT')
        check(current['binding_sha256']==binding_sha256,'AI_EVIDENCE_CHANGED')
        check(not enabled or can_enable,'AI_EVIDENCE_NOT_READY')
        at=self.db.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()
        value={'namespace':NAMESPACE,'stage':stage,'report_id':report_id,'group_id':group_id,'event_id':event['id'],
               'binding_sha256':binding_sha256,'enabled':enabled,'revision':expected_revision+1,'actor_id':user_id,'at':at}
        value['sha256']=digest(value)
        self.ws.put('WS_CONFIG',prefix(stage,report_id)+digest(group_id)[:32]+f".{value['revision']:010d}",value)
        self.ws.put('WS_CONFIG',REQUEST+identity,{'namespace':NAMESPACE,'report_id':report_id,
            'command_sha256':digest(command)})
        return self._view(context,event,can_enable,eligible,report_id,group_id,stage)
