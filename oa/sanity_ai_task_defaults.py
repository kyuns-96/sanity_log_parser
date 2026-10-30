"""Administrator defaults for classification and group judgment models.

The selection only supplies a UI default. Each submitted job still pins its
explicit connection/model and validates its owner's own credential.
"""
from copy import deepcopy

from common.sanity_common import ReviewError, digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_clustering import check
from oa.sanity_ai_model import _model_id
from oa.sanity_workspace import WorkspaceStore, record_id

PREFIX = 'ai.task-defaults.'
NAMESPACE = 'oa-ai-task-defaults-v1'
TASKS = ('CLUSTER', 'REVIEW')


def _selection(value, *, code):
    check(value is None or isinstance(value,dict) and set(value)=={'connection_id','model_id'}, code)
    if value is None:
        return None
    # A persisted selection uses one canonical identity, not a loose UUID form.
    try:
        connection_id = str(uuid_value(value['connection_id']))
    except ReviewError:
        raise ReviewError(code, 'Invalid task model selection') from None
    check(connection_id == value['connection_id'] and _model_id(value['model_id']), code)
    return {'connection_id':connection_id,'model_id':value['model_id']}


def _defaults(value, *, code):
    check(isinstance(value,dict) and set(value)==set(TASKS), code)
    return {task:_selection(value[task],code=code) for task in TASKS}


class TaskDefaults:
    def __init__(self, db):
        self.db = db

    def current(self):
        from oa.sanity_ai_jobs import _NULL_COLUMNS
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                               "ORDER BY workspace_key", (PREFIX+'%',)).fetchall()
        previous = None
        for revision, row in enumerate(rows,1):
            value = row['payload']
            check(isinstance(value,dict) and set(value)=={'namespace','revision','actor_id','defaults','previous_sha256','sha256'}
                  and value['namespace']==NAMESPACE and type(value['revision']) is int and value['revision']==revision
                  and isinstance(value['actor_id'],str) and 1<=len(value['actor_id'])<=200
                  and value['previous_sha256']==(previous['sha256'] if previous else None)
                  and row['workspace_key']==PREFIX+f'{revision:010d}'
                  and row['record_id']==record_id('WS_CONFIG',row['workspace_key'])
                  and row['state']=={} and row['version']==0 and row['status']=='RECORDED'
                  and row['updated_at']==row['created_at'] and all(row.get(k) is None for k in _NULL_COLUMNS)
                  and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}), 'AI_TASK_DEFAULTS_CORRUPT')
            check(_defaults(value['defaults'],code='AI_TASK_DEFAULTS_CORRUPT')==value['defaults'], 'AI_TASK_DEFAULTS_CORRUPT')
            previous=value
        if previous is None:
            return {'revision':0,'defaults':{task:None for task in TASKS}}
        return {'revision':previous['revision'],'defaults':deepcopy(previous['defaults'])}

    @staticmethod
    def _admin(actor, db):
        check(getattr(actor,'auth_method',None)=='sso' and getattr(actor,'access',None) is not None
              and actor.access.db is db, 'FORBIDDEN')
        actor.access._admin(actor)

    def read(self, actor):
        self._admin(actor,self.db)
        return self.current()

    def save(self, actor, defaults, expected_revision):
        from oa.sanity_ai_connections import ConnectionStore, PREFIX as CONNECTION_PREFIX
        self._admin(actor,self.db)
        check(type(expected_revision) is int and 0<=expected_revision<9999999999,'INVALID_REQUEST')
        defaults=_defaults(defaults,code='INVALID_REQUEST')
        # Match connection mutation's catalog -> connection ordering, and pin
        # selected current models until the new default is committed.
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(CONNECTION_PREFIX+'catalog'),))
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(PREFIX),))
        current=self.current()
        check(current['revision']==expected_revision,'REVISION_CONFLICT')
        store=ConnectionStore(self.db)
        for connection_id in sorted({value['connection_id'] for value in defaults.values() if value}):
            connection=store.get(connection_id)
            for value in defaults.values():
                if value and value['connection_id']==connection_id:
                    check(value['model_id'] in connection['config']['models'],'AI_MODEL_NOT_ALLOWED')
        revision=expected_revision+1
        if expected_revision:
            row=self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                                (PREFIX+f'{expected_revision:010d}',)).fetchone()
            check(row is not None,'AI_TASK_DEFAULTS_CORRUPT')
            previous_sha256=row['payload']['sha256']
        else:
            previous_sha256=None
        value={'namespace':NAMESPACE,'revision':revision,'actor_id':actor.user_id,'defaults':defaults,
               'previous_sha256':previous_sha256}
        value['sha256']=digest(value)
        WorkspaceStore(self.db).put('WS_CONFIG',PREFIX+f'{revision:010d}',value)
        return {'revision':revision,'defaults':deepcopy(defaults)}
