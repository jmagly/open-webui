import { afterEach, describe, expect, it, vi } from 'vitest';
import { retainCatalogSelection, watchModelCatalog } from './modelCatalog';

afterEach(() => {
	vi.useRealTimers();
});

describe('user model catalog selection', () => {
	it('clears removed models and preserves remaining selections in place', () => {
		expect(retainCatalogSelection(['old', 'kept'], [{ id: 'kept' }, { id: 'new' }])).toEqual([
			'',
			'kept'
		]);
	});
	it('clears every selection for an empty or failed catalog', () => {
		expect(retainCatalogSelection(['old'], [])).toEqual(['']);
	});
	it('never automatically chooses a new paid model', () => {
		expect(retainCatalogSelection([''], [{ id: 'new-paid' }])).toEqual(['']);
	});
});

describe('catalog refresh lifecycle', () => {
	it('refreshes on focus and every 15 seconds only while visible, and cleans up', () => {
		vi.useFakeTimers();
		const target = new EventTarget();
		const refresh = vi.fn();
		let visible = false;
		const tab = Object.assign(new EventTarget(), { visibilityState: 'hidden' });
		const stop = watchModelCatalog(refresh, () => visible, target, tab);
		target.dispatchEvent(new Event('focus'));
		expect(refresh).toHaveBeenCalledTimes(1);
		vi.advanceTimersByTime(15000);
		expect(refresh).toHaveBeenCalledTimes(1);
		visible = true;
		vi.advanceTimersByTime(15000);
		expect(refresh).toHaveBeenCalledTimes(2);
		tab.dispatchEvent(new Event('visibilitychange'));
		expect(refresh).toHaveBeenCalledTimes(2);
		tab.visibilityState = 'visible';
		tab.dispatchEvent(new Event('visibilitychange'));
		expect(refresh).toHaveBeenCalledTimes(3);
		stop();
		target.dispatchEvent(new Event('focus'));
		tab.dispatchEvent(new Event('visibilitychange'));
		vi.advanceTimersByTime(15000);
		expect(refresh).toHaveBeenCalledTimes(3);
	});
});
