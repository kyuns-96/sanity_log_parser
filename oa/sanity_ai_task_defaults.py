"""Administrator defaults for classification and group judgment models.

The selection only supplies a UI default. Each submitted job still pins its
explicit connection/model and validates its owner's own credential.
"""
from common.sanity_common import ReviewError
from common.sanity_db import lock_key, uuid_value
from common.sanity_clustering import check
from oa.sanity_ai_model import _model_id
from oa.sanity_workspace import WorkspaceStore, record_id

PREFIX = 'ai.task-defaults.'
TASKS = ('CLUSTER', 'REVIEW')


def _defaults(value, *, code):
    check(isinstance(value,dict) and set(value)==set(TASKS), code)
    for selection in value.values():
        check(selection is None or isinstance(selection,dict) and set(selection)=={'connection_id','model_id'}, code)
        if selection is None:
            continue
        try:
            connection_id = str(uuid_value(selection['connection_id']))
        except ReviewError:
            raise ReviewError(code, 'Invalid task model selection') from None
        check(connection_id == selection['connection_id'] and _model_id(selection['model_id']), code)
    return value


class TaskDefaults:
    def __init__(self, db):
        self.db = db

    def current(self):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                              "ORDER BY workspace_key DESC LIMIT 1", (PREFIX+'%',)).fetchone()
        if row is None:
            return {'revision':0,'defaults':{task:None for task in TASKS}}
        value = row['payload']
        check(isinstance(value,dict) and type(value.get('revision')) is int
              and 1<=value['revision']<=9999999999
              and row['workspace_key']==PREFIX+f"{value['revision']:010d}"
              and row['record_id']==record_id('WS_CONFIG',row['workspace_key']), 'AI_TASK_DEFAULTS_CORRUPT')
        return {'revision':value['revision'],
                'defaults':_defaults(value.get('defaults'),code='AI_TASK_DEFAULTS_CORRUPT')}

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
        value={'revision':revision,'actor_id':actor.user_id,'defaults':defaults}
        WorkspaceStore(self.db).put('WS_CONFIG',PREFIX+f'{revision:010d}',value)
        return {'revision':revision,'defaults':defaults}
