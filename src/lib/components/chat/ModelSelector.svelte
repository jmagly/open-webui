<script lang="ts">
	import { models, pinnedModels, settings, user } from '$lib/stores';
	import { getContext, onMount } from 'svelte';
	import { userModelCatalog, modelCatalogErrors } from '$lib/stores/modelCatalog';
	import { refreshUserModelCatalog } from '$lib/apis';
	import { retainCatalogSelection, watchModelCatalog } from '$lib/utils/modelCatalog';
	import { toast } from 'svelte-sonner';
	import Selector from './ModelSelector/Selector.svelte';

	import { updateUserSettings } from '$lib/apis/users';
	import { resolveLocalizedModelName } from '$lib/utils/localizedContent';
	import equal from 'fast-deep-equal';
	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	export let showSetDefault = true;
	export let triggerClassName = 'text-lg';
	export let className = undefined;
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';

	let compareModels = selectedModels.length > 1;
	let selector;
	let selectorOpen = false;

	const refreshCatalog = async () => {
		await refreshUserModelCatalog();
	};

	onMount(() => watchModelCatalog(refreshCatalog, () => selectorOpen));
	$: if (selectorOpen && $userModelCatalog) refreshCatalog();

	export const open = () => selector?.open();

	const saveDefaultModel = async () => {
		const hasEmptyModel = selectedModels.filter((it) => it === '');
		if (hasEmptyModel.length) {
			toast.error($i18n.t('Choose a model before saving...'));
			return;
		}
		settings.set({ ...$settings, models: selectedModels });
		await updateUserSettings(localStorage.token, { ui: { models: selectedModels } });

		toast.success($i18n.t('Default model updated'));
	};

	const pinModelHandler = async (modelId) => {
		settings.set({
			...$settings,
			pinnedModels: $pinnedModels.includes(modelId)
				? $pinnedModels.filter((id) => id !== modelId)
				: [...$pinnedModels, modelId]
		});
		await updateUserSettings(localStorage.token, { ui: { pinnedModels: $settings.pinnedModels } });
	};

	$: if (selectedModels.length > 0 && ($models.length > 0 || $userModelCatalog)) {
		const _selectedModels = retainCatalogSelection(selectedModels, $models);

		if (!equal(_selectedModels, selectedModels)) {
			selectedModels = _selectedModels;
		}
	}

	$: if (selectedModels.length > 1 && !compareModels) {
		compareModels = true;
	}
</script>

<div class="flex min-w-0 max-w-full flex-col items-start">
	{#if $userModelCatalog && $modelCatalogErrors.length}
		<div role="alert" class="text-sm text-red-600 dark:text-red-400">
			{$modelCatalogErrors.join(' ')}
		</div>
	{/if}
	<div class="flex min-w-0 max-w-full">
		<div class="min-w-0 max-w-full overflow-hidden">
			<div class="min-w-0 max-w-full">
				<Selector
					bind:this={selector}
					bind:show={selectorOpen}
					id="model"
					placeholder={$i18n.t('Select a model')}
					items={$models.map((model) => ({
						value: model.id,
						label: resolveLocalizedModelName(model, $i18n.language),
						model: model
					}))}
					{pinModelHandler}
					{className}
					{triggerClassName}
					{placement}
					{align}
					{showSetDefault}
					onSetDefault={saveDefaultModel}
					multipleEnabled={$user?.role === 'admin' ||
						($user?.permissions?.chat?.multiple_models ?? true)}
					{disabled}
					bind:compareEnabled={compareModels}
					bind:values={selectedModels}
				/>
			</div>
		</div>
	</div>
</div>
