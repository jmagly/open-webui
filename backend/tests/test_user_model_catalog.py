"""Catalog isolation tests without starting the optional inference/RAG services."""

import ast
import asyncio
import copy
import importlib.util
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from starlette.datastructures import State

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from open_webui.utils import model_catalog as catalog

pytestmark = pytest.mark.asyncio


def load_functions(relative_path, names, namespace):
    """Load real routing functions without importing the RAG/plugin service graph.

    Keep their bodies intact and inject service boundaries as ordinary unit-test
    doubles. Annotations and route decorators are irrelevant to these calls.
    """
    path = BACKEND / 'open_webui' / relative_path
    tree = ast.parse(path.read_text())
    body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            body.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


@pytest.fixture
def user():
    return SimpleNamespace(id='alice', role='user')


@pytest.fixture
def request_factory():
    shared = SimpleNamespace(
        BASE_MODELS=[{'id': 'another-users-model'}],
        OPENAI_MODELS={'another-users-model': {'id': 'another-users-model', 'urlIdx': 0}},
        MODELS={'another-users-model': {'id': 'another-users-model'}},
    )

    def make(user=None, cookie=None):
        request = Request(
            {
                'type': 'http',
                'headers': [(b'cookie', f'oauth_session_id={cookie}'.encode())] if cookie else [],
                'app': SimpleNamespace(state=shared),
            }
        )
        if user:
            request.state.user = user
        return request

    return make


@pytest.fixture
def settings(monkeypatch):
    config = {
        'openai.enable': True,
        'openai.api_base_urls': ['http://provider/v1'],
        'openai.api_configs': {
            '0': {'model_catalog_scope': 'user', 'auth_type': 'system_oauth', 'model_ids': ['manual']}
        },
    }
    monkeypatch.setattr(catalog, 'get_catalog_config', AsyncMock(side_effect=lambda: config))
    return config


async def test_concurrent_users_and_same_request_calls_are_isolated(monkeypatch, settings, request_factory, user):
    async def fetch(request, owner, url, config):
        await asyncio.sleep(0)
        return {'data': [{'id': owner.id}]}

    fetch = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(catalog, 'fetch_catalog', fetch)
    alice, bob = request_factory(user), request_factory(SimpleNamespace(id='bob', role='admin'))
    a, b, again = await asyncio.gather(
        catalog.get_user_model_catalog(alice, user),
        catalog.get_user_model_catalog(bob, bob.state.user),
        catalog.get_user_model_catalog(alice, user),
    )
    assert set(a['models']) == {'alice'}
    assert set(b['models']) == {'bob'}
    assert a is again
    assert fetch.await_count == 2
    assert alice.app.state.BASE_MODELS == [{'id': 'another-users-model'}]
    assert set(alice.app.state.OPENAI_MODELS) == {'another-users-model'}
    assert await catalog.is_user_catalog_model(alice, user, 'alice')
    assert not await catalog.is_user_catalog_model(alice, user, 'bob')
    with pytest.raises(HTTPException, match='user mismatch'):
        await catalog.get_user_model_catalog(alice, bob.state.user)


@pytest.mark.parametrize('result', [{'data': []}, None, {'error': 'denied'}, {'data': [{'name': 'bad'}]}])
async def test_empty_failed_or_malformed_never_falls_back(monkeypatch, settings, request_factory, user, result):
    fetch = AsyncMock(return_value=result)
    monkeypatch.setattr(catalog, 'fetch_catalog', fetch)
    request = request_factory(user)
    snapshot = await catalog.get_user_model_catalog(request, user)
    assert snapshot['models'] == {}
    assert snapshot['errors']
    assert catalog.catalog_response(request)['model_catalog_scope'] == 'user'
    assert not await catalog.is_user_catalog_model(request, user, 'another-users-model')
    assert not await catalog.is_user_catalog_model(request, user, 'manual')
    await catalog.get_user_model_catalog(request, user)
    assert fetch.await_count == 1


async def test_new_request_sees_entitlement_change(monkeypatch, settings, request_factory, user):
    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(side_effect=[{'data': [{'id': 'old'}]}, {'data': []}]))
    old = await catalog.get_user_model_catalog(request_factory(user), user)
    new = await catalog.get_user_model_catalog(request_factory(user), user)
    assert set(old['models']) == {'old'}
    assert new['models'] == {}


async def test_raw_ids_and_connection_settings_are_snapshot_owned(monkeypatch, settings, request_factory, user):
    settings['openai.api_configs']['0']['prefix_id'] = 'ignored'
    monkeypatch.setattr(
        catalog,
        'fetch_catalog',
        AsyncMock(return_value={'data': [{'id': 'raw', 'pipe': True, 'arena': True, 'pipeline': {'type': 'filter'}}]}),
    )
    snapshot = await catalog.get_user_model_catalog(request_factory(user), user)
    settings['openai.api_configs']['0']['auth_type'] = 'bearer'
    assert set(snapshot['models']) == {'raw'}
    assert not any(key in snapshot['models']['raw'] for key in ('pipe', 'arena', 'pipeline'))
    assert snapshot['connections'][0][2]['auth_type'] == 'system_oauth'


async def test_opt_in_only_and_disabled_connections(monkeypatch, settings, request_factory, user):
    fetch = AsyncMock()
    monkeypatch.setattr(catalog, 'fetch_catalog', fetch)
    settings['openai.api_configs']['0'].pop('model_catalog_scope')
    request = request_factory(user)
    assert await catalog.get_user_model_catalog(request, user) is None
    assert await catalog.get_request_models(request, user) is request.app.state.MODELS
    assert catalog.catalog_response(request) == {}
    settings['openai.api_configs']['0'].update(model_catalog_scope='user', enable=False)
    assert await catalog.get_user_model_catalog(request_factory(user), user) is None
    fetch.assert_not_awaited()


async def test_user_catalog_rejects_static_auth(monkeypatch, settings, request_factory, user):
    settings['openai.api_configs']['0']['auth_type'] = 'bearer'
    fetch = AsyncMock()
    monkeypatch.setattr(catalog, 'fetch_catalog', fetch)
    snapshot = await catalog.get_user_model_catalog(request_factory(user), user)
    assert snapshot['models'] == {} and snapshot['errors']
    fetch.assert_not_awaited()


@pytest.fixture
def header_helper(monkeypatch):
    fallback = AsyncMock(return_value={'access_token': 'owner-token'})
    monkeypatch.setitem(sys.modules, 'open_webui.utils.oauth', SimpleNamespace(get_system_oauth_token=fallback))
    functions = load_functions(
        'utils/headers.py',
        ['get_headers_and_cookies'],
        {
            'HTTPException': HTTPException,
            'ENABLE_FORWARD_USER_INFO_HEADERS': False,
            'get_custom_headers': AsyncMock(side_effect=lambda headers, *args, **kwargs: headers),
            'log': logging.getLogger(__name__),
        },
    )
    return functions.get_headers_and_cookies, fallback


async def test_headers_use_owner_fallback_only_for_authenticated_owner(header_helper, request_factory, user):
    headers_for, fallback = header_helper
    config = {
        'auth_type': 'system_oauth',
        'model_catalog_scope': 'user',
        'headers': {'authorization': 'Bearer static-key'},
    }
    headers, _ = await headers_for(request_factory(user), 'http://provider', 'shared-key', config, user=user)
    assert headers['Authorization'] == 'Bearer owner-token'
    assert 'authorization' not in headers
    fallback.assert_awaited_once()
    fallback.reset_mock()
    with pytest.raises(HTTPException):
        await headers_for(request_factory(), 'http://provider', 'shared-key', config, user=user)
    fallback.assert_not_awaited()
    with pytest.raises(HTTPException):
        await headers_for(request_factory(SimpleNamespace(id='bob')), 'http://provider', config=config, user=user)
    fallback.assert_not_awaited()


@pytest.mark.parametrize('oauth_token', [None, {}, {'access_token': ''}, {'access_token': ' '}])
async def test_missing_oauth_fails_closed(header_helper, request_factory, user, oauth_token):
    headers_for, fallback = header_helper
    fallback.return_value = oauth_token
    with pytest.raises(HTTPException) as exc:
        await headers_for(
            request_factory(user),
            'http://provider',
            'shared',
            {'auth_type': 'system_oauth', 'model_catalog_scope': 'user'},
            user=user,
        )
    assert exc.value.status_code == 401


async def test_expired_cookie_does_not_try_another_session(header_helper, request_factory, user):
    headers_for, fallback = header_helper
    request = request_factory(user, cookie='expired')
    request.app.state.oauth_manager = SimpleNamespace(get_oauth_token=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await headers_for(
            request, 'http://provider', config={'auth_type': 'system_oauth', 'model_catalog_scope': 'user'}, user=user
        )
    assert exc.value.status_code == 401
    fallback.assert_not_awaited()


async def test_catalog_uses_actual_oauth_headers(monkeypatch, settings, header_helper, request_factory, user):
    headers_for, _ = header_helper
    monkeypatch.setitem(sys.modules, 'open_webui.utils.headers', SimpleNamespace(get_headers_and_cookies=headers_for))
    monkeypatch.setitem(
        sys.modules,
        'open_webui.env',
        SimpleNamespace(AIOHTTP_CLIENT_SESSION_SSL=True, AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST=5),
    )
    request = request_factory(user, cookie='valid')
    request.app.state.oauth_manager = SimpleNamespace(
        get_oauth_token=AsyncMock(return_value={'access_token': 'browser'})
    )
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=AsyncMock(return_value={'data': [{'id': 'allowed'}]}),
    )

    class Context:
        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *args):
            pass

        def __init__(self, value):
            self.value = value

    def get(url, **kwargs):
        assert url == 'http://provider/v1/models'
        assert kwargs['headers']['Authorization'] == 'Bearer browser'
        return Context(response)

    monkeypatch.setattr(catalog.aiohttp, 'ClientSession', lambda **kwargs: Context(SimpleNamespace(get=get)))
    snapshot = await catalog.get_user_model_catalog(request, user)
    assert set(snapshot['models']) == {'allowed'}


@pytest.fixture
def openai_router(monkeypatch, settings):
    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(return_value={'data': [{'id': 'allowed'}]}))

    # Stop exactly at the upstream dispatch boundary after all model/header routing.
    class Dispatched(BaseException):
        pass

    dispatched = []

    async def send(**kwargs):
        dispatched.append(kwargs)
        raise Dispatched()

    namespace = {
        'get_user_model_catalog': catalog.get_user_model_catalog,
        'is_user_catalog_model': catalog.is_user_catalog_model,
        'catalog_response': catalog.catalog_response,
        '_get_all_models': AsyncMock(return_value={'data': []}),
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'BYPASS_MODEL_ACCESS_CONTROL': True,
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'apply_model_params_to_body_openai': lambda params, payload: payload,
        'check_model_access': AsyncMock(),
        'get_openai_connection': AsyncMock(side_effect=AssertionError('Shared connection must not be used')),
        'HTTPException': HTTPException,
        'Depends': lambda dependency: None,
        'get_verified_user': lambda: None,
        'ERROR_MESSAGES': SimpleNamespace(MODEL_NOT_FOUND=lambda: 'Model not found'),
        'strip_provider_model_prefix': lambda model, prefix: model,
        'is_openai_new_model': lambda model: False,
        'is_anthropic_url': lambda url: False,
        'get_headers_and_cookies': AsyncMock(return_value=({'Authorization': 'Bearer owner'}, {})),
        'JSONCodec': json,
        'get_session': AsyncMock(return_value=SimpleNamespace(request=send)),
        'get_client_timeout': lambda **kwargs: None,
        'cleanup_response': AsyncMock(),
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'log': logging.getLogger(__name__),
    }
    router = load_functions(
        'routers/openai.py',
        [
            'get_all_models',
            'get_request_openai_models',
            'get_request_connection',
            'generate_chat_completion',
            'get_anthropic_request_target',
        ],
        namespace,
    )
    return router, namespace, dispatched, Dispatched


@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_chat_dispatch_uses_snapshot_and_denies_other_models(openai_router, request_factory, user, role):
    user.role = role
    router, namespace, dispatched, Dispatched = openai_router
    request = request_factory(user)
    with pytest.raises(Dispatched):
        await router.generate_chat_completion(request, {'model': 'allowed', 'messages': []}, user)
    assert dispatched[0]['url'] == 'http://provider/v1/chat/completions'
    assert json.loads(dispatched[0]['data'])['model'] == 'allowed'
    assert dispatched[0]['headers']['Authorization'] == 'Bearer owner'
    namespace['check_model_access'].assert_not_awaited()
    namespace['Models'].get_model_by_id.assert_not_awaited()
    for model_id in ['another-users-model', 'manual', 'preset']:
        with pytest.raises(HTTPException):
            await router.generate_chat_completion(request, {'model': model_id, 'messages': []}, user)
    assert len(dispatched) == 1


async def test_aggregate_empty_snapshot_excludes_shared_and_synthetic_models(
    monkeypatch, settings, request_factory, user
):
    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(return_value={'data': []}))
    request = request_factory(user)
    namespace = {
        'get_user_model_catalog': catalog.get_user_model_catalog,
        'Config': SimpleNamespace(get_many=AsyncMock(return_value={'models.base_models_cache': True})),
        'get_all_base_models': AsyncMock(return_value=[]),
    }
    aggregate = load_functions('utils/models.py', ['get_all_models'], namespace)
    assert await aggregate.get_all_models(request, user=user) == []
    assert request.state.catalog_models == {}
    assert request.app.state.BASE_MODELS == [{'id': 'another-users-model'}]
    assert set(request.app.state.MODELS) == {'another-users-model'}


async def test_scoped_acl_and_auxiliary_resolution_reuse_snapshot(monkeypatch, settings, request_factory, user):
    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(return_value={'data': [{'id': 'allowed'}]}))
    request = request_factory(user)
    snapshot = await catalog.get_user_model_catalog(request, user)
    request.state.catalog_models = snapshot['models']
    acl = load_functions(
        'utils/models.py', ['check_model_access'], {'get_user_model_catalog': catalog.get_user_model_catalog}
    )
    for role in ['user', 'admin']:
        user.role = role
        await acl.check_model_access(user, snapshot['models']['allowed'], request=request)
        with pytest.raises(Exception, match='Model not found'):
            await acl.check_model_access(user, {'id': 'forbidden', 'model_catalog_scope': 'user'}, request=request)
    assert await catalog.get_request_models(request, user) is request.state.catalog_models
    assert catalog.fetch_catalog.await_count == 1


async def test_connection_field_survives_update_and_legacy_repair(settings):
    config_path = BACKEND / 'open_webui/models/config.py'
    tree = ast.parse(config_path.read_text())
    fields = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'API_CONFIG_FIELDS' for target in node.targets)
    )
    repair = load_functions('models/config.py', ['_split_api_config_fragment'], {'API_CONFIG_FIELDS': fields})
    assert repair._split_api_config_fragment('0.model_catalog_scope') == ('0', ['model_catalog_scope'])
    assert repair._split_api_config_fragment('https://provider.example/v1.model_catalog_scope') == (
        'https://provider.example/v1',
        ['model_catalog_scope'],
    )
    upsert = AsyncMock()
    router = load_functions(
        'routers/openai.py',
        ['update_config'],
        {
            'Config': SimpleNamespace(upsert=upsert),
            'Depends': lambda dependency: None,
            'get_admin_user': lambda: None,
            'clear_models_cache': AsyncMock(),
            'publish_event': AsyncMock(),
            'EVENTS': SimpleNamespace(MODEL_PROVIDER_CONFIG_UPDATED='updated'),
        },
    )
    form = SimpleNamespace(
        ENABLE_OPENAI_API=True,
        OPENAI_API_BASE_URLS=settings['openai.api_base_urls'],
        OPENAI_API_KEYS=[''],
        OPENAI_API_CONFIGS=settings['openai.api_configs'],
    )
    result = await router.update_config(None, form, None)
    assert result['OPENAI_API_CONFIGS']['0']['model_catalog_scope'] == 'user'
    assert upsert.await_args.args[0]['openai.api_configs']['0']['model_catalog_scope'] == 'user'


async def test_unscoped_discovery_keeps_cached_path(monkeypatch, settings, request_factory, user):
    settings['openai.api_configs'] = {}
    cached = AsyncMock(return_value={'data': [{'id': 'stock'}]})
    router = load_functions(
        'routers/openai.py',
        ['get_all_models'],
        {'get_user_model_catalog': catalog.get_user_model_catalog, '_get_all_models': cached},
    )
    request = request_factory(user)
    assert await router.get_all_models(request, user) == {'data': [{'id': 'stock'}]}
    cached.assert_awaited_once_with(request, user=user)
    assert not hasattr(request.state, 'catalog_openai_models')


async def test_bearer_headers_keep_stock_behavior(header_helper, request_factory, user):
    headers_for, fallback = header_helper
    headers, _ = await headers_for(request_factory(user), 'http://provider', 'static-key', {}, user=user)
    assert headers['Authorization'] == 'Bearer static-key'
    fallback.assert_not_awaited()


async def test_expired_token_makes_catalog_empty_before_network(
    monkeypatch, settings, request_factory, user, header_helper
):
    headers_for, _ = header_helper
    monkeypatch.setitem(sys.modules, 'open_webui.utils.headers', SimpleNamespace(get_headers_and_cookies=headers_for))
    monkeypatch.setitem(
        sys.modules,
        'open_webui.env',
        SimpleNamespace(AIOHTTP_CLIENT_SESSION_SSL=True, AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST=5),
    )
    network = AsyncMock(side_effect=AssertionError('No unauthenticated network call'))
    monkeypatch.setattr(catalog.aiohttp, 'ClientSession', network)
    request = request_factory(user, cookie='expired')
    request.app.state.oauth_manager = SimpleNamespace(get_oauth_token=AsyncMock(return_value=None))
    snapshot = await catalog.get_user_model_catalog(request, user)
    assert snapshot['models'] == {} and snapshot['errors']
    network.assert_not_called()


async def test_startup_without_user_never_discovers_scoped_models(monkeypatch, settings, request_factory):
    fetch = AsyncMock()
    monkeypatch.setattr(catalog, 'fetch_catalog', fetch)
    request = request_factory()
    snapshot = await catalog.get_user_model_catalog(request, None)
    assert snapshot['models'] == {} and snapshot['errors']
    assert await catalog.get_user_model_catalog(request, None) is snapshot
    fetch.assert_not_awaited()


async def test_unscoped_oauth_keeps_stock_cookie_only_behavior(header_helper, request_factory, user):
    headers_for, fallback = header_helper
    headers, _ = await headers_for(
        request_factory(user), 'http://provider', config={'auth_type': 'system_oauth'}, user=user
    )
    assert 'Authorization' not in headers
    fallback.assert_not_awaited()


async def test_automation_defaults_use_owner_snapshot(monkeypatch, settings, request_factory, user):
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.datastructures import Headers

    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(return_value={'data': [{'id': 'allowed'}]}))
    app = request_factory().app
    app.state.MODELS['allowed'] = {'id': 'allowed', 'info': {'meta': {'toolIds': ['stale-tool']}}}
    automation = load_functions(
        'utils/automations.py',
        ['_build_request', '_resolve_model_defaults'],
        {
            'Request': Request,
            'Headers': Headers,
            'HTTPAuthorizationCredentials': HTTPAuthorizationCredentials,
            'get_request_models': catalog.get_request_models,
        },
    )
    request = automation._build_request(app, token='owner-session', user=user)
    assert request.state.user is user
    request.state.catalog_models = (await catalog.get_user_model_catalog(request, user))['models']
    assert await automation._resolve_model_defaults(request, user, 'allowed') == {}
    assert catalog.fetch_catalog.await_count == 1


@pytest.fixture
def owner_discovery(monkeypatch, settings, header_helper):
    """Exercise real OAuth ownership checks at the provider network boundary."""
    headers_for, fallback = header_helper

    async def fetch(request, owner, url, config):
        headers, _ = await headers_for(request, url, config=config, user=owner)
        assert headers['Authorization'] == 'Bearer owner-token'
        return {'data': [{'id': 'allowed'}]}

    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(side_effect=fetch))
    return fallback


@pytest.mark.parametrize('internal', [False, True])
@pytest.mark.parametrize('expired_oauth', [False, True])
async def test_subagent_request_uses_revalidated_owner_oauth(
    request_factory, user, owner_discovery, internal, expired_oauth
):
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.datastructures import Headers

    owner_discovery.return_value = None if expired_oauth else {'access_token': 'owner-token'}
    auth = SimpleNamespace(active=True, session_stamp='current-stamp')
    builder = load_functions(
        'utils/subagents.py',
        ['_build_request'],
        {
            'Request': Request,
            'Headers': Headers,
            'HTTPAuthorizationCredentials': HTTPAuthorizationCredentials,
            'Auths': SimpleNamespace(get_auth_by_id=AsyncMock(return_value=auth)),
            'Users': SimpleNamespace(get_user_by_id=AsyncMock(return_value=user)),
            'VERIFIED_USER_ROLES': ('user', 'admin'),
            'create_token': lambda **kwargs: 'internal-token',
            'timedelta': timedelta,
        },
    )
    # The source belongs to someone else and carries a stale catalog/cookie.
    source = request_factory(SimpleNamespace(id='bob'), cookie='bob-session')
    source.state.catalog_models = {'bob-only': {'id': 'bob-only'}}
    request = await builder._build_request(source, user.id, internal=internal)
    snapshot = await catalog.get_user_model_catalog(request, user)
    assert set(snapshot['models']) == (set() if expired_oauth else {'allowed'})
    owner_discovery.assert_awaited_once_with(request, user)
    assert request.state.user is user
    assert not request.cookies
    assert getattr(request.state, 'internal', False) is internal
    assert bool(snapshot['errors']) is expired_oauth


@pytest.mark.parametrize('owner_state', ['inactive', 'missing_auth', 'missing_user', 'pending'])
async def test_subagent_request_rejects_invalid_owner(request_factory, user, owner_state):
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.datastructures import Headers

    auth = None if owner_state == 'missing_auth' else SimpleNamespace(active=owner_state != 'inactive')
    if auth:
        auth.session_stamp = 'current-stamp'
    if owner_state == 'pending':
        user.role = 'pending'
    create_token = MagicMock(return_value='internal-token')
    builder = load_functions(
        'utils/subagents.py',
        ['_build_request'],
        {
            'Request': Request,
            'Headers': Headers,
            'HTTPAuthorizationCredentials': HTTPAuthorizationCredentials,
            'Auths': SimpleNamespace(get_auth_by_id=AsyncMock(return_value=auth)),
            'Users': SimpleNamespace(
                get_user_by_id=AsyncMock(return_value=None if owner_state == 'missing_user' else user)
            ),
            'VERIFIED_USER_ROLES': ('user', 'admin'),
            'create_token': create_token,
            'timedelta': timedelta,
        },
    )
    with pytest.raises(ValueError, match='owner'):
        await builder._build_request(request_factory(), user.id, internal=True)
    create_token.assert_not_called()


@pytest.mark.parametrize(
    'owner_state', ['valid', 'expired_oauth', 'inactive', 'missing_auth', 'missing_user', 'pending']
)
async def test_timer_request_uses_only_active_owner_oauth(
    monkeypatch, request_factory, user, owner_discovery, owner_state
):
    from starlette.datastructures import Headers

    if owner_state == 'expired_oauth':
        owner_discovery.return_value = None
    if owner_state == 'pending':
        user.role = 'pending'
    timer = SimpleNamespace(
        user_id=user.id,
        meta={
            'status': 'running',
            'parent_chat_id': 'owned-chat',
            'timer_task_message_id': 'task',
            'run': {'model_id': 'allowed'},
        },
    )
    parent = SimpleNamespace(chat={})
    db = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: parent)),
        get=AsyncMock(return_value=timer),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def database():
        yield db

    sio = SimpleNamespace(emit=AsyncMock())
    monkeypatch.setitem(sys.modules, 'open_webui.socket.main', SimpleNamespace(sio=sio))
    monkeypatch.setitem(sys.modules, 'open_webui.utils.subagents', SimpleNamespace(_parent_locks={}))
    auth = None if owner_state == 'missing_auth' else SimpleNamespace(active=owner_state != 'inactive')
    record_state = AsyncMock()
    snapshots = []

    async def dispatch(request, form_data, user):
        snapshots.append(await catalog.get_user_model_catalog(request, user))
        assert request.state.user is user
        assert request.state.token is None
        assert not request.cookies

    app = request_factory().app
    app.state.redis = None
    app.state.CHAT_COMPLETION_HANDLER = AsyncMock(side_effect=dispatch)
    namespace = {
        'asyncio': asyncio,
        'copy': copy,
        'time': time,
        'uuid4': uuid4,
        'Request': Request,
        'Headers': Headers,
        'HTTPException': HTTPException,
        'Auths': SimpleNamespace(get_auth_by_id=AsyncMock(return_value=auth)),
        'Users': SimpleNamespace(
            get_user_by_id=AsyncMock(return_value=None if owner_state == 'missing_user' else user)
        ),
        'VERIFIED_USER_ROLES': ('user', 'admin'),
        '_timer_locks': {},
        '_set_timer_state': record_state,
        'Chats': SimpleNamespace(
            get_chat_by_id=AsyncMock(return_value=timer),
            get_chat_by_id_and_user_id=AsyncMock(return_value=parent),
            get_message_by_id_and_message_id=AsyncMock(return_value={'content': 'task'}),
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(),
        ),
        'ChatMessages': SimpleNamespace(upsert_message=AsyncMock()),
        'get_async_db': database,
        'select': MagicMock(),
        'Chat': SimpleNamespace(id='owned-chat', user_id=user.id),
        'has_active_tasks': AsyncMock(return_value=False),
        'get_message_list': lambda *args: [],
        'log': logging.getLogger(__name__),
    }
    timer_runner = load_functions('utils/timers.py', ['execute_due_timer'], namespace)
    await timer_runner.execute_due_timer(app, 'timer')
    if owner_state in ('valid', 'expired_oauth'):
        app.state.CHAT_COMPLETION_HANDLER.assert_awaited_once()
        assert set(snapshots[0]['models']) == (set() if owner_state == 'expired_oauth' else {'allowed'})
        assert bool(snapshots[0]['errors']) is (owner_state == 'expired_oauth')
        owner_discovery.assert_awaited_once()
        record_state.assert_not_awaited()
    else:
        app.state.CHAT_COMPLETION_HANDLER.assert_not_awaited()
        owner_discovery.assert_not_awaited()
        assert record_state.await_args.args == ('timer', 'error')
        assert 'timer_error' in record_state.await_args.kwargs


async def test_compact_chat_route_uses_user_snapshot_with_empty_shared_registry(monkeypatch, settings, user):
    from fastapi import Depends, FastAPI, status
    from httpx import ASGITransport, AsyncClient
    from pydantic import BaseModel

    class CompactChatForm(BaseModel):
        model: str | None = None

    monkeypatch.setattr(catalog, 'fetch_catalog', AsyncMock(return_value={'data': [{'id': 'allowed'}]}))
    chat = SimpleNamespace(chat={}, current_message_id=None)
    compactions = []

    async def compact(request, owner, chat, model_id, models):
        if model_id not in models:
            raise ValueError('No available model for context compaction')
        assert models is request.state.catalog_models
        assert owner is user
        compactions.append(model_id)
        return {'compacted': True, 'dropped_messages': 2}

    route = load_functions(
        'routers/chats.py',
        ['compact_chat_by_id'],
        {
            'Request': Request,
            'CompactChatForm': CompactChatForm,
            'AsyncSession': object,
            'Depends': Depends,
            'get_verified_user': lambda: user,
            'get_async_session': lambda: None,
            'HTTPException': HTTPException,
            'status': status,
            'Chats': SimpleNamespace(
                get_chat_by_id_and_user_id=AsyncMock(return_value=chat),
                get_messages_map_by_chat_id=AsyncMock(return_value={}),
            ),
            'has_active_tasks': AsyncMock(return_value=False),
            'get_all_models': AsyncMock(return_value=[{'id': 'allowed'}]),
            'get_request_models': catalog.get_request_models,
            'get_message_list': lambda *args: [],
            'compact_chat_branch': compact,
            'get_chat_context_usage': AsyncMock(return_value={'tokens': 100}),
            'publish_event': AsyncMock(),
            'EVENTS': SimpleNamespace(CHAT_COMPACTED='compacted'),
        },
    )
    app = FastAPI()
    app.state.MODELS = {}
    app.state.redis = None

    @app.middleware('http')
    async def request_snapshot(request, call_next):
        snapshot = await catalog.get_user_model_catalog(request, user)
        request.state.catalog_models = snapshot['models']
        return await call_next(request)

    app.add_api_route('/api/v1/chats/{id}/compact', route.compact_chat_by_id, methods=['POST'])
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test'
    ) as client:
        response = await client.post('/api/v1/chats/owned-chat/compact', json={'model': 'allowed'})
    assert response.status_code == 200, response.text
    assert response.json() == {'compacted': True, 'dropped_messages': 2, 'context_usage': {'tokens': 100}}
    assert compactions == ['allowed']
    assert app.state.MODELS == {}
    catalog.fetch_catalog.assert_awaited_once()


@pytest.fixture
def preset_record():
    """Construct the actual persisted-model schema without starting its database."""
    import typing

    import pydantic

    tree = ast.parse((BACKEND / 'open_webui/models/models.py').read_text())
    names = {
        'ModelControlOption',
        'ModelControl',
        'ModelParams',
        'ModelVoice',
        'ModelAvatarAnimation',
        'ModelAvatarGesture',
        'ModelVoiceAvatar',
        'ModelMeta',
        'ModelModel',
    }
    namespace = {
        **vars(typing),
        **{name: getattr(pydantic, name) for name in pydantic.__all__},
        'ModelControlKey': str,
        'AccessGrantModel': pydantic.BaseModel,
    }
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'models.py', 'exec'), namespace)
    return namespace['ModelModel'](
        id='hidden-preset',
        user_id='alice',
        base_model_id='allowed',
        name='Hidden preset',
        params={},
        meta={},
        is_active=True,
        created_at=1,
        updated_at=1,
    )


@pytest.mark.parametrize('target', ['generate_chat_completion', 'get_anthropic_request_target'])
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_direct_routes_reject_existing_preset_before_resolution(
    openai_router, request_factory, user, preset_record, target, role
):
    user.role = role
    router, namespace, dispatched, _ = openai_router
    namespace['Models'].get_model_by_id.return_value = preset_record
    # For admins the ordinary preset ACL permits access; scoped raw-ID checks must win.
    request = request_factory(user)
    with pytest.raises(HTTPException) as exc:
        await getattr(router, target)(request, {'model': preset_record.id, 'messages': []}, user)
    assert exc.value.status_code == 404
    namespace['Models'].get_model_by_id.assert_not_awaited()
    namespace['get_headers_and_cookies'].assert_not_awaited()
    assert dispatched == []
