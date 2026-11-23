"""Administrator model choices for log tasks and project comparison analysis.

Log tasks use UI defaults; project comparison uses its explicit administrator
choice. Submitted jobs pin that choice and validate the owner's credential.
"""
from common.sanity_ai_retry import MAX_RETRIES
from common.sanity_clustering import check
from common.sanity_common import ReviewError
from common.sanity_db import lock_key, uuid_value
from oa.sanity_access import require_admin_access
from oa.sanity_ai_connections import ConnectionStore, PREFIX as CONNECTION_PREFIX
from oa.sanity_ai_model import valid_model_id
from oa.sanity_workspace_core import WorkspaceCoreStore, record_id

PREFIX = 'ai.task-defaults.'
DEFAULT_AREA_RETRY_LIMIT = 5
TASKS = ('CLUSTER', 'REVIEW', 'AREA_TRIAGE')


def _defaults(value, *, code, stored=False):
    # Existing persisted revisions predate the project-comparison selection.
    if stored and isinstance(value, dict) and set(value) == {'CLUSTER', 'REVIEW'}:
        value = {**value, 'AREA_TRIAGE': None}
    check(isinstance(value, dict) and set(value) == set(TASKS), code)
    for selection in value.values():
        check(selection is None or isinstance(selection, dict) and set(selection) == {'connection_id', 'model_id'}, code)
        if selection is None:
            continue
        try:
            connection_id = str(uuid_value(selection['connection_id']))
        except ReviewError:
            raise ReviewError(code, 'Invalid task model selection') from None
        check(connection_id == selection['connection_id'] and valid_model_id(selection['model_id']), code)
    return value


class TaskDefaults:
    def __init__(self, db):
        self.db = db

    def current(self):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                              "ORDER BY workspace_key DESC LIMIT 1", (PREFIX + '%',)).fetchone()
        if row is None:
            return {'revision': 0, 'defaults': {task: None for task in TASKS}, 'area_retry_limit': DEFAULT_AREA_RETRY_LIMIT}
        value = row['payload']
        check(isinstance(value, dict) and type(value.get('revision')) is int
              and 1 <= value['revision'] <= 9999999999
              and row['workspace_key'] == PREFIX + f"{value['revision']:010d}"
              and row['record_id'] == record_id('WS_CONFIG', row['workspace_key']), 'AI_TASK_DEFAULTS_CORRUPT')
        retry_limit = value.get('area_retry_limit', DEFAULT_AREA_RETRY_LIMIT)
        check(type(retry_limit) is int and 0 <= retry_limit <= MAX_RETRIES, 'AI_TASK_DEFAULTS_CORRUPT')
        return {'revision': value['revision'], 'area_retry_limit': retry_limit,
                'defaults': _defaults(value.get('defaults'), code='AI_TASK_DEFAULTS_CORRUPT', stored=True)}

    @staticmethod
    def require_admin(actor, db):
        check(getattr(actor, 'auth_method', None) == 'sso' and getattr(actor, 'access', None) is not None
              and actor.access.db is db, 'FORBIDDEN')
        require_admin_access(actor.access, actor)

    def read(self, actor):
        self.require_admin(actor, self.db)
        return self.current()

    def save(self, actor, defaults, expected_revision, *, area_retry_limit=None):
        self.require_admin(actor, self.db)
        check(type(expected_revision) is int and 0 <= expected_revision < 9999999999, 'INVALID_REQUEST')
        defaults = _defaults(defaults, code='INVALID_REQUEST')
        # Match connection mutation's catalog -> connection ordering, and pin
        # selected current models until the new default is committed.
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(CONNECTION_PREFIX + 'catalog'),))
        self.db.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(PREFIX),))
        current = self.current()
        check(current['revision'] == expected_revision, 'REVISION_CONFLICT')
        if area_retry_limit is None:
            area_retry_limit = current['area_retry_limit']
        check(type(area_retry_limit) is int and 0 <= area_retry_limit <= MAX_RETRIES, 'INVALID_REQUEST')
        store = ConnectionStore(self.db)
        for connection_id in sorted({value['connection_id'] for value in defaults.values() if value}):
            connection = store.get(connection_id)
            for value in defaults.values():
                if value and value['connection_id'] == connection_id:
                    check(value['model_id'] in connection['config']['models'], 'AI_MODEL_NOT_ALLOWED')
        revision = expected_revision + 1
        value = {'revision': revision, 'defaults': defaults, 'area_retry_limit': area_retry_limit}
        WorkspaceCoreStore(self.db).put('WS_CONFIG', PREFIX + f'{revision:010d}', {**value, 'actor_id': actor.user_id})
        return value


def task_selection(connections_store, user_id, task, *, defaults=None):
    """The shared log/Area initial connection and model selection."""
    check(task in TASKS, "INVALID_REQUEST")
    connections = connections_store.user_connections(user_id)
    first = connections[0] if connections else {}
    if defaults is None:
        defaults = TaskDefaults(connections_store.db).current()['defaults']
    default = defaults[task]
    if task == 'AREA_TRIAGE' and default is None:
        return connections, {}, {'applied': False, 'reason': 'AI_TASK_MODEL_REQUIRED'}
    task_default = None
    if default is not None:
        selected = next((connection for connection in connections if connection['id'] == default['connection_id']), None)
        if selected is None:
            task_default = {**default, 'applied': False, 'reason': 'AI_CONNECTION_UNAVAILABLE'}
        elif default['model_id'] not in selected['models']:
            task_default = {**default, 'applied': False, 'reason': 'AI_MODEL_NOT_ALLOWED'}
        else:
            selected['model'] = default['model_id']
            first = selected
            task_default = {**default, 'applied': True}
    if task == 'AREA_TRIAGE' and task_default and not task_default.get('applied'):
        return connections, {}, task_default
    return connections, first, task_default
