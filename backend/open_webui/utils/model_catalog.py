"""Request-local discovery for connections whose upstream owns user entitlements."""

import asyncio
import copy

import aiohttp
from fastapi import HTTPException


async def get_catalog_config():
    from open_webui.models.config import Config

    return await Config.get_many('openai.enable', 'openai.api_base_urls', 'openai.api_configs')


async def fetch_catalog(request, user, url, config):
    from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST
    from open_webui.utils.headers import get_headers_and_cookies

    headers, cookies = await get_headers_and_cookies(request, url, config=config, user=user)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST), trust_env=True
    ) as session:
        async with session.get(
            f'{url.rstrip("/")}/models', headers=headers, cookies=cookies, ssl=AIOHTTP_CLIENT_SESSION_SSL
        ) as response:
            response.raise_for_status()
            return await response.json()


async def _get_user_model_catalog(request, user):
    """Return one snapshot per authenticated request; None means stock behavior.

    Keep even failed/empty snapshots. Neither model IDs nor connection settings
    from this path may be placed in application or Redis model caches.
    """
    if hasattr(request.state, 'user_model_catalog'):
        snapshot = request.state.user_model_catalog
        if snapshot is not None and snapshot['user_id'] != (user.id if user else None):
            raise HTTPException(status_code=403, detail='Model catalog user mismatch')
        return snapshot

    config = await get_catalog_config()
    urls = config.get('openai.api_base_urls') or []
    configs = config.get('openai.api_configs') or {}
    connections = {
        idx: (url, '', copy.deepcopy(configs.get(str(idx), configs.get(url, {}))))
        for idx, url in enumerate(urls)
        if config.get('openai.enable')
        and configs.get(str(idx), configs.get(url, {})).get('enable', True)
        and configs.get(str(idx), configs.get(url, {})).get('model_catalog_scope') == 'user'
    }
    if not connections:
        request.state.user_model_catalog = None
        return None

    snapshot = {'user_id': user.id if user else None, 'models': {}, 'connections': connections, 'errors': []}

    async def discover(idx, url, connection):
        try:
            if user is None or connection.get('auth_type') != 'system_oauth':
                raise ValueError('User catalogs require system_oauth authentication')
            result = await fetch_catalog(request, user, url, connection)
            if not isinstance(result, dict) or not isinstance(result.get('data'), list) or 'error' in result:
                raise ValueError('Invalid model catalog response')
            models = {}
            for item in result['data']:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    raise ValueError('Invalid model catalog entry')
                model_id = item['id']
                models[model_id] = {
                    'id': model_id,
                    'name': item['name'] if isinstance(item.get('name'), str) and item['name'] else model_id,
                    'object': 'model',
                    'owned_by': 'openai',
                    'connection_type': connection.get('connection_type', 'external'),
                    'urlIdx': idx,
                    'model_catalog_scope': 'user',
                }
            return models, None
        except Exception:
            # Do not expose upstream bodies, URLs or credentials in UI errors.
            return {}, f'User model catalog unavailable for connection {idx}. Please sign in again or retry.'

    results = await asyncio.gather(*(discover(idx, url, conn) for idx, (url, _, conn) in connections.items()))
    for models, error in results:
        snapshot['models'].update(models)
        if error:
            snapshot['errors'].append(error)
    if not snapshot['models'] and not snapshot['errors']:
        snapshot['errors'].append('No models are currently available for your account.')
    request.state.user_model_catalog = snapshot
    return snapshot


async def get_user_model_catalog(request, user):
    if not hasattr(request.state, 'user_model_catalog_lock'):
        request.state.user_model_catalog_lock = asyncio.Lock()
    async with request.state.user_model_catalog_lock:
        return await _get_user_model_catalog(request, user)


async def is_user_catalog_model(request, user, model_id):
    snapshot = await get_user_model_catalog(request, user)
    return snapshot is not None and model_id in snapshot['models']


async def get_request_models(request, user):
    """Resolve models once, including for continuations and auxiliary tasks."""
    snapshot = await get_user_model_catalog(request, user)
    if snapshot is not None:
        if not hasattr(request.state, 'catalog_models'):
            from open_webui.utils.models import get_all_models

            await get_all_models(request, user=user)
        return request.state.catalog_models
    return request.app.state.MODELS


def catalog_response(request):
    snapshot = getattr(request.state, 'user_model_catalog', None)
    return {'model_catalog_scope': 'user', 'errors': snapshot['errors']} if snapshot is not None else {}
