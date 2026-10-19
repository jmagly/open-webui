import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { models, type Model } from '$lib/stores';
import { userModelCatalog, modelCatalogErrors } from '$lib/stores/modelCatalog';
import { getModels, refreshUserModelCatalog } from './index';

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { config: writable({}), models: writable([]) };
});
vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));
vi.mock('$lib/utils', () => ({ convertOpenApiToToolPayload: vi.fn(), resolveSchema: vi.fn() }));
vi.mock('$lib/utils/tags', () => ({ normalizeTags: vi.fn() }));
vi.mock('./openai', () => ({ getOpenAIModelsDirect: vi.fn() }));

beforeEach(() => {
	userModelCatalog.set(false);
	modelCatalogErrors.set([]);
	models.set([]);
	vi.stubGlobal('localStorage', { token: 'session-token' });
	vi.stubGlobal('fetch', vi.fn());
});

const respond = (body: object) => {
	vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => body } as Response);
};

describe('catalog API', () => {
	it('uses the exact scoped list and excludes personal connections', async () => {
		respond({ data: [{ id: 'allowed' }], model_catalog_scope: 'user', errors: [] });
		const result = await getModels('token', { OPENAI_API_BASE_URLS: ['http://personal'] });
		expect(result).toEqual([{ id: 'allowed' }]);
		expect(get(userModelCatalog)).toBe(true);
		expect(fetch).toHaveBeenCalledTimes(1);
	});
	it('publishes errors for an authoritative empty list', async () => {
		respond({ data: [], model_catalog_scope: 'user', errors: ['Catalog unavailable'] });
		expect(await getModels('token')).toEqual([]);
		expect(get(modelCatalogErrors)).toEqual(['Catalog unavailable']);
	});
	it('keeps stock requests from adding refresh traffic', async () => {
		expect(await refreshUserModelCatalog()).toBe(true);
		expect(fetch).not.toHaveBeenCalled();
	});
	it('refreshes before a send and replaces the previous catalog', async () => {
		userModelCatalog.set(true);
		models.set([{ id: 'old', name: 'Old', owned_by: 'openai', external: true } as Model]);
		respond({ data: [{ id: 'new' }], model_catalog_scope: 'user', errors: [] });
		await refreshUserModelCatalog();
		expect(get(models)).toEqual([{ id: 'new' }]);
		expect(vi.mocked(fetch).mock.calls[0][0]).toContain('refresh=true');
	});
	it('clears stale models after an HTTP or authentication failure', async () => {
		userModelCatalog.set(true);
		models.set([{ id: 'old', name: 'Old', owned_by: 'openai', external: true } as Model]);
		vi.mocked(fetch).mockRejectedValue(new Error('401'));
		expect(await refreshUserModelCatalog()).toBe(false);
		expect(get(models)).toEqual([]);
		expect(get(modelCatalogErrors)).not.toEqual([]);
	});
	it('coalesces simultaneous open, focus and send refreshes', async () => {
		userModelCatalog.set(true);
		respond({ data: [], model_catalog_scope: 'user', errors: [] });
		await Promise.all([refreshUserModelCatalog(), refreshUserModelCatalog()]);
		expect(fetch).toHaveBeenCalledTimes(1);
	});
});
