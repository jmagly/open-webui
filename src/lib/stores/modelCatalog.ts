import { writable } from 'svelte/store';

export const userModelCatalog = writable(false);
export const modelCatalogErrors = writable<string[]>([]);
