"""Explicit synthetic-only providers for submission tests, never auto-loaded."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from pathlib import Path

from common.sanity_common import atomic_text
from common.sanity_submission import (SourceIdentityResolver, SubmissionAuthority, SubmissionInput, SubmissionInputProvider,
    VerifiedSubmitter, check, scope_digest)
from common.sanity_submission_files import write_json_file


class FixtureAuthority(SubmissionAuthority):
    def __init__(self, root, actor):
        self.root, self.actor, self.allowed = Path(root), actor, True

    def current(self):
        return self.actor

    def inbox_owner(self, directory):
        check(directory == self.root/'spool/inbox'/self.actor.key,'FORBIDDEN')
        return self.actor

    def authorize(self, actor, collection):
        check(self.allowed and actor==self.actor and collection['source_namespace']=='synthetic-submission-v2'
              and collection['project_key']=='project-1' and collection['block_key']=='project-1-cpu','FORBIDDEN')

    def approval_valid(self, plan):
        approval = plan['approval']
        return bool(approval and approval['reference']=='SYNTHETIC-APPROVAL'
                    and approval['actor']==self.actor.actor and approval['scope_sha256']==scope_digest(plan))


class FixtureInput(SubmissionInputProvider):
    def __init__(self, value):
        self.value = value

    def prepare(self, target):
        return SubmissionInput(copy.deepcopy(self.value.plan),dict(self.value.reports))


class FixtureIdentity(SourceIdentityResolver):
    def resolve(self, actor):
        check(actor=={'issuer':'synthetic-submission-fixture','subject':'fixture-owner','user_id':'user-04'},'SOURCE_IDENTITY_MISMATCH')
        return 'user-04'


def fixture(root, *, count=4, padding=0, pre=True):
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True)
    actor = VerifiedSubmitter('synthetic-submission-fixture','fixture-owner','user-04',os.geteuid())
    for path in ('spool/inbox','spool/receipts','archive','state','input-pre','input-post'):
        (root/path).mkdir(parents=True,mode=0o700,exist_ok=True)
    for parent in ('inbox','receipts'):
        (root/'spool'/parent/actor.key).mkdir(mode=0o750)
    write_json_file(root/'spool/storage.json',{'schema_version':1,'storage_id':'synthetic-submission-storage'})
    client = {'schema_version':1,'spool_root':str(root/'spool'),'storage_id':'synthetic-submission-storage',
              'service_uid':os.geteuid(),'default_profile':'synthetic-top'}
    bridge = {**client,'archive_root':str(root/'archive'),'node_id':'synthetic-node'}
    collection = {'source_namespace':'synthetic-submission-v2','project_key':'project-1','block_key':'project-1-cpu',
        'net_ver':'NET_001','submission_revision':'preA_postB-BE','layout':'POST','target_eco':'eco01',
        'pre_revision':'preA' if pre else None,'stage':'sanity','profile_id':'synthetic-top','profile_version':'1'}
    units, originals = [], {}
    for layout,rows in ([('PRE',4)] if pre else [])+[('POST',count)]:
        relative = 'reports/'+layout.lower()+'/sample.rpt'
        unit = {'review_unit_id':'unit-'+layout.lower(),'item_id':'gca_constraints','family':'GCA',
            'report':relative,'parser':'numeric_csv_v1','rule_version':'1','condition':{'mode':'M1'},
            'identity_fields':['object','check_point','mode'],'execution_status':'COMPLETED','applicable':True,'exclusion':None,
            'evaluation':{'operator':'le','limit':10,'unit':'ns'},
            'source':{'net_ver':'NET_001','revision':'preA' if layout=='PRE' else 'preA_postB-BE','layout':layout,
                'eco':None if layout=='PRE' else 'eco01','mode':'M1','execution_ref':'synthetic-'+layout.lower()+'-run-1',
                'execution_order':1,'source_stage':'place' if layout=='PRE' else 'run','tool':'synthetic','tool_version':'1'}}
        path = root/('input-'+layout.lower())/'sample.rpt'
        with path.open('w') as stream:
            stream.write('object,check_point,mode,code,severity,observed,unit\n')
            for index in range(rows):
                stream.write(f"top/{'x'*padding}cell{index%4},P1,M1,CHECK-101,WARNING,{8 if index%4==0 else 12},ns\n")
            stream.write(f'# END rows={rows}\n')
        originals[relative] = path
        units.append(unit)
    plan = {'schema_version':2,'collection':collection,
            'inventory':{'confirmed':True,'source':'synthetic-declared-scope','version':'1','units':units},'approval':None}
    plan['approval'] = {'scope_sha256':scope_digest(plan),'reference':'SYNTHETIC-APPROVAL','actor':actor.actor,
                        'approved_at':datetime.now(timezone.utc).isoformat()}
    prepared = SubmissionInput(plan,originals)
    target = {'project':'project-1','block':'project-1-cpu','net_ver':'NET_001','revision':'preA_postB-BE',
              'layout':'POST','eco':'eco01','profile':'synthetic-top'}
    return {'client_config':client,'bridge_config':bridge,'actor':actor,'authority':FixtureAuthority(root,actor),
            'provider':FixtureInput(prepared),'prepared':prepared,'target':target}
