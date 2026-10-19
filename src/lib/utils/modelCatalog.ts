// Never replace a vanished selection with another model: sending may incur a charge.
export const retainCatalogSelection = (selected: string[], available: { id: string }[]) => {
	const ids = new Set(available.map((model) => model.id));
	return selected.map((id) => (ids.has(id) ? id : ''));
};

export const watchModelCatalog = (
	refresh: () => void,
	visible: () => boolean,
	target: EventTarget = window,
	visibilityTarget: (EventTarget & { visibilityState: string }) | undefined = typeof document ===
	'undefined'
		? undefined
		: document
) => {
	const focus = () => refresh();
	target.addEventListener('focus', focus);
	const onVisible = () => {
		if (visibilityTarget?.visibilityState === 'visible') refresh();
	};
	visibilityTarget?.addEventListener('visibilitychange', onVisible);
	const timer = setInterval(() => {
		if (visible()) refresh();
	}, 15000);
	return () => {
		target.removeEventListener('focus', focus);
		visibilityTarget?.removeEventListener('visibilitychange', onVisible);
		clearInterval(timer);
	};
};
