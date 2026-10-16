#!/usr/bin/env python3
"""Add two multi-record fixtures only to the dedicated local 8002 QA workspace."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_workspace import database_from_config


SEED = """
import json,os,sys
from pathlib import Path
from psycopg2.extensions import parse_dsn
config=json.loads(Path(sys.argv[1]).read_text())
params=parse_dsn(config['settings']['SANITY_OA_DSN'])
if (params.get('host')!=config['root'] or params.get('port')!='55439'
    or params.get('dbname')!='sanity_test_mock' or params.get('user')!='sanity_mock_oa'):
    raise RuntimeError('Only the restricted local synthetic fixture login is allowed')
os.environ.update(config['settings'])
from common.sanity_db import Database
from oa.sanity_workspace import WorkspaceStore, record_id
with Database('oa') as db,db.transaction():
    ws=WorkspaceStore(db); ws.check_workspace()
    for name in ('qa-cluster-source','qa-cluster-target'):
        records=[{'id':name+'-e-'+str(index),'check_status':'PASS' if index==4 else 'FAIL',
            'fields':{'object':obj,'mode':'M1','check_point':'P1','code':'CLK_QA','severity':'ERROR'},
            'raw':obj+' mode=M1 check=P1','line_start':index,'line_end':index,'reasons':[]}
            for index,obj in enumerate(('gpu/u_a','gpu/u_a','gpu/u_b','gpu/u_c'),1)]
        payload={'id':name,'project_id':'project-3','block_id':'project-3-gpu',
            'version_id':'project-3-gpu-post-1','version_record_id':str(record_id('WS_VERSION','project-3-gpu-post-1')),
            'eco':'chip_finish','item_id':'gca_constraints','family':'GCA','rule':'CLK_QA',
            'rule_version':'pipeline-qa-v1','parser':'pipeline_qa_fixture','record_count':4,'records':records,
            'raw_report':chr(10).join(record['raw'] for record in records),'check_status':'FAIL',
            'parse_status':'COMPLETE','analysis_ready':True,'blockers':[]}
        existing=db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",(name,)).fetchone()
        if existing:
            if existing['payload']!=payload:
                raise RuntimeError('Existing QA source differs; never overwrite it')
        else:
            ws.put('WS_REPORT',name,payload,project='project-3',block='project-3-gpu')
print(json.dumps({'status':'QA_PIPELINE_READY','reports':2,'data_retained':True}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/tmp/sanity-workspace-e2e/runtime.json'))
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if (config.get('environment') != 'SYNTHETIC_WORKSPACE' or config['port'] != 8002
            or not Path(config['root']).resolve().is_relative_to('/tmp/sanity-workspace-e2e/db')):
        raise SystemExit('Only the dedicated synthetic QA workspace is allowed')
    database = database_from_config(config)
    subprocess.run([sys.executable, '-c', SEED, str(args.config.resolve())], cwd=database.root / 'source',
                   env=database.env, check=True)


if __name__ == '__main__':
    main()
