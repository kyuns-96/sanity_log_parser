"""Area domain prompts over the log parser's selected, user-bound AI provider.

Only nonsecret configuration/credential pins cross the Area DB queue. As in
``oa.ai_worker --site vwp``, execution uses the logical OA Database on VWP.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError, digest
from common.sanity_config import load_environment
from common.sanity_db import Database
from common.sanity_vwp_ai import require
from area_power.common.jsonutil import dumps, loads


def _token_store(store, connection_id, advisor):
    from oa.sanity_ai_tokens import UserTokenStore
    if connection_id is not None:
        return store.tokens(store.get(connection_id))
    return UserTokenStore(store.db, store.settings) if advisor.settings.token_source == 'user' else None


def capture_context(access, *, tasks=('CLUSTER', 'REVIEW')):
    """Capture the same initial choices as log classification and judgment."""
    from oa.sanity_ai_connections import ConnectionStore

    actor = getattr(access, 'actor', None)
    if getattr(actor, 'auth_method', None) != 'sso' or getattr(actor, 'access', None) is None:
        return {task: {'error': 'AI_AUTHENTICATED_REQUEST_REQUIRED'} for task in tasks}
    store = ConnectionStore(actor.access.db)
    result = {}
    for task in tasks:
        try:
            _, selected, task_default = store.task_selection(actor.user_id, task)
            if task_default is not None:
                require(task_default.get('applied'), task_default.get('reason') or 'AI_CONNECTION_UNAVAILABLE')
            if selected:
                require(selected.get('credential_ready'), selected.get('credential_error') or 'AI_USER_TOKEN_REQUIRED')
                connection_id = selected['id']
                store.authorize_user(connection_id, actor.user_id)
                advisor = store.advisor(connection_id, selected['model'])
            else:
                # The existing explicit no-connection log route is legitimate
                # only when no managed choice/default selected a connection.
                require(task_default is None, task_default.get('reason', 'AI_CONNECTION_UNAVAILABLE') if task_default else 'AI_CONNECTION_UNAVAILABLE')
                connection_id, advisor = None, store.default_advisor()
                require(advisor is not None, 'AI_NOT_CONFIGURED')
            tokens = _token_store(store, connection_id, advisor)
            credential_pin = None
            if tokens is not None:
                _, revision = tokens.get_token(actor.user_id)
                credential_pin = {'revision': revision, 'scope': tokens.credential_scope}
            description = advisor.describe()
            result[task] = {'user_id': actor.user_id, 'connection_id': connection_id,
                            'model': description['model'], 'config_sha256': description['config_sha256'],
                            'credential_pin': credential_pin}
        except ReviewError as error:
            # Missing AI must not prevent creation of an exact numerical comparison.
            result[task] = {'user_id': actor.user_id, 'error': error.code}
    return result


@dataclass
class Runtime:
    db: object
    subject: dict
    context: dict
    receipts: list = field(default_factory=list)

    def _resolve(self, host_db, task, *, token=False):
        from oa.sanity_access import AccessStore, access_lock, administrator_id
        from oa.sanity_ai_connections import ConnectionStore
        from area_power.oa.access import AreaAccess

        pin = self.context.get(task, {})
        require(isinstance(pin, dict), 'AI_CONFIG_CHANGED')
        if pin.get('error'):
            raise ReviewError(pin['error'], 'The selected AI connection is unavailable')
        require(set(pin) == {'user_id', 'connection_id', 'model', 'config_sha256', 'credential_pin'}, 'AI_CONFIG_CHANGED')
        access_lock(host_db)
        access = AccessStore(host_db, administrator_id(load_environment()))
        access.require_ready()
        # Reconstruct authority from the original authenticated request, never an
        # upload identity or a saved grant snapshot. AreaAccess rereads bindings.
        actor = SimpleNamespace(user_id=pin['user_id'], auth_method='sso', access=access)
        permission = AreaAccess(actor)
        with self.db.connect() as conn:
            permission.row(self.db, conn, self.subject, write=True)
        store = ConnectionStore(host_db)
        if pin['connection_id'] is not None:
            store.authorize_user(pin['connection_id'], pin['user_id'])
            advisor = store.advisor(pin['connection_id'], pin['model'])
        else:
            advisor = store.default_advisor(pin['model'])
            require(advisor is not None, 'AI_CONFIG_CHANGED')
        require(advisor.describe()['config_sha256'] == pin['config_sha256'], 'AI_CONFIG_CHANGED')
        tokens = _token_store(store, pin['connection_id'], advisor)
        if tokens is not None:
            credential, revision = tokens.get_token(pin['user_id'])
            require({'revision': revision, 'scope': tokens.credential_scope} == pin['credential_pin'], 'AI_CREDENTIAL_CHANGED')
            if token:
                return advisor.with_user_token(credential)
        else:
            require(pin['credential_pin'] is None, 'AI_CREDENTIAL_CHANGED')
        return advisor if token else None

    def validate(self, tasks=('CLUSTER', 'REVIEW')):
        """Check pinned authority and credentials before processing observations."""
        for task in tasks:
            pin = self.context.get(task, {})
            require(isinstance(pin, dict), 'AI_CONFIG_CHANGED')
            if pin.get('error'):
                raise ReviewError(pin['error'], 'The selected AI connection is unavailable')
            require(bool(pin), 'AI_NOT_CONFIGURED')
        with executing_on('vwp'):
            with Database('oa') as host_db, host_db.transaction():
                for task in tasks:
                    self._resolve(host_db, task)

    def infer(self, task, context, instruction, prompt_version):
        pin = self.context.get(task, {})
        if pin.get('error'):
            raise ReviewError(pin['error'], 'The selected AI connection is unavailable')
        require(bool(pin), 'AI_NOT_CONFIGURED')
        with executing_on('vwp'):
            # This is exactly the existing VWP log worker DB factory. It uses
            # already provisioned SANITY DB settings, not the Area DB login.
            with Database('oa') as host_db, host_db.transaction():
                advisor = self._resolve(host_db, task, token=True)
                advisor = advisor.bind_execution(call_id=str(uuid4()), job_id=str(self.subject['id']),
                    actor_id=pin['user_id'], scope_sha256=digest(dumps({key: self.subject.get(key)
                        for key in ('project_id', 'version_id', 'block_id', 'snapshot_id')}, canonical=True)))
            # No database transaction or authorization lock is held during HTTP.
            # The string preserves Area Decimal numbers and UUIDs losslessly;
            # the outer provider packet uses the existing log JSON wire format.
            value, metadata = advisor.infer_json({'context_json': dumps(context, canonical=True)},
                instruction, prompt_version, decoder=loads)
            with Database('oa') as host_db, host_db.transaction():
                self._resolve(host_db, task)
            self.receipts.append(metadata)
            return value


def runtime_for(db, subject, comparison=None):
    context = subject['payload'].get('ai_context')
    if context is None and comparison is not None:
        context = comparison['payload'].get('ai_context')
    return Runtime(db, subject, context or {})
