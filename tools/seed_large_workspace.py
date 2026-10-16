#!/usr/bin/env python3
"""Add average-size or larger single reports to an isolated synthetic workspace."""
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
if (not set(params)<={'host','port','dbname','user','password','sslmode','connect_timeout'}
    or params.get('host')!=config['root'] or params.get('port')!='55439'
    or params.get('dbname')!='sanity_test_mock' or params.get('user')!='sanity_mock_oa'):
    raise RuntimeError('Expected the restricted local synthetic fixture login')
os.environ.update(config['settings'])
from common.sanity_db import Database
from oa.sanity_workspace import WorkspaceStore, record_id
from devtools.sanity_large_samples import large_report
block_id=sys.argv[2]
count=int(sys.argv[3])
families=sys.argv[4].split(',')
with Database('oa') as db,db.transaction():
    ws=WorkspaceStore(db); ws.check_workspace()
    block=ws.get('WS_BLOCK',block_id)['payload']
    version_id=block_id+'-post-1'
    ws.get('WS_VERSION',version_id)
    for family in families:
        report=large_report(family,count=count,design=block_id.rsplit('-',1)[1].upper())
        payload={**report,'project_id':block['project_id'],'block_id':block_id,'version_id':version_id,
                 'version_record_id':str(record_id('WS_VERSION',version_id)),'eco':'chip_finish'}
        existing=db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",(report['id'],)).fetchone()
        if existing:
            if existing['payload']!=payload:raise RuntimeError('Existing source differs; never overwrite reports')
        else:ws.put('WS_REPORT',report['id'],payload,project=block['project_id'],block=block_id)
print(json.dumps({'status':'LARGE_REPORTS_READY','reports':len(families),'records_per_report':count,'block_id':block_id,'existing_data_retained':True}))
"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('artifacts/workspace-runtime.json'))
    parser.add_argument('--block',choices=('project-1-cpu','project-3-gpu'),default='project-1-cpu')
    parser.add_argument('--count',type=int,choices=(10000,100000),default=10000)
    parser.add_argument('--family',choices=('all','GCA','VCLP','SPYGLASS'),default='all')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    if (config.get('environment')!='SYNTHETIC_WORKSPACE' or config.get('port') not in (8001,8002)
            or not Path(config['root']).name.startswith('sanity-mock-pg-')):
        raise SystemExit('Only the isolated synthetic workspace is supported')
    database=database_from_config(config)
    subprocess.run([sys.executable,'-c',SEED,str(args.config.resolve()),args.block,str(args.count),
                    ','.join(('GCA','VCLP','SPYGLASS') if args.family=='all' else (args.family,))],
                   cwd=database.root/'source',env=database.env,check=True)


if __name__=='__main__':main()
