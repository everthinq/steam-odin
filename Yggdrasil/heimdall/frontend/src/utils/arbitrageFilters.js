// Pure helpers for the Huginn filters (components/arbitrage/ArbitrageFilters.jsx),
// shared by the Arbitrage, Case Arbitrage, Cross-Profile and Harvest tabs.
import { useState, useEffect } from 'react';

export const EMPTY_FILTERS = { priceSide: 'buy', minPrice: '', maxPrice: '', accounts: [], collections: [] };

// What the two price sides are called, per tab: [side, button label, tooltip, chip word].
export const DEFAULT_PRICE_SIDES = [
    ['buy', 'Buy price', 'What you pay on the buy market', 'Buy'],
    ['sell', 'Sell price', 'What the sell market pays or lists at', 'Sell'],
];

// '' or anything that is not a number → no bound.
export const parsePrice = (text) => {
    const value = parseFloat(String(text ?? '').replace(',', '.'));
    return Number.isFinite(value) && value >= 0 ? value : null;
};

export const hasPriceFilter = (filters) => parsePrice(filters.minPrice) !== null || parsePrice(filters.maxPrice) !== null;

export const activeFilterCount = (filters) =>
    (hasPriceFilter(filters) ? 1 : 0) + filters.accounts.length + filters.collections.length;

const money = (value) => `$${value % 1 === 0 ? value : value.toFixed(2)}`;

export const priceRangeLabel = (filters, priceSides = DEFAULT_PRICE_SIDES) => {
    const min = parsePrice(filters.minPrice);
    const max = parsePrice(filters.maxPrice);
    const side = (priceSides.find(s => s[0] === filters.priceSide) || priceSides[0])[3];
    if (min !== null && max !== null) return `${side} ${money(min)} – ${money(max)}`;
    if (min !== null) return `${side} ≥ ${money(min)}`;
    return `${side} ≤ ${money(max)}`;
};

// A row passes the price filter when its price on the chosen side is inside the
// range (both ends included). No price on that side never matches a set range.
export const priceInRange = (price, filters) => {
    const min = parsePrice(filters.minPrice);
    const max = parsePrice(filters.maxPrice);
    if (min === null && max === null) return true;
    return price != null && (min === null || price >= min) && (max === null || price <= max);
};

// Collection of each item name, from the Huginn inventory scan (`by_hash`). The
// scan is the only place collections exist, so an item never scanned has none.
export const collectionByName = (byHash) => {
    const map = new Map();
    for (const [name, entry] of Object.entries(byHash || {})) {
        const found = (entry.instances || []).find(i => i.collection);
        if (found) map.set(name, found.collection);
    }
    return map;
};

// Checklist options: every name in `known`, plus every name the rows carry, each
// with how many rows it matches. A filtered-on name no longer present stays listed
// (count 0) so it can still be unticked.
export const buildOptions = (rows, namesOf, known, selected) => {
    const counts = new Map();
    for (const name of known || []) if (name) counts.set(name, 0);
    for (const row of rows) {
        for (const name of new Set(namesOf(row))) {
            if (name) counts.set(name, (counts.get(name) || 0) + 1);
        }
    }
    for (const name of selected) if (!counts.has(name)) counts.set(name, 0);
    return [...counts].map(([name, count]) => ({ name, count }))
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
};

const readFilters = (storageKey) => {
    try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
        if (!saved || typeof saved !== 'object') return EMPTY_FILTERS;
        const list = (value) => (Array.isArray(value) ? value.filter(v => typeof v === 'string') : []);
        return {
            priceSide: saved.priceSide === 'sell' ? 'sell' : 'buy',
            minPrice: typeof saved.minPrice === 'string' ? saved.minPrice : '',
            maxPrice: typeof saved.maxPrice === 'string' ? saved.maxPrice : '',
            accounts: list(saved.accounts),
            collections: list(saved.collections),
        };
    } catch {
        return EMPTY_FILTERS;
    }
};

// Filters remembered per browser under `storageKey` (a convenience; the chips
// always show what is active).
export const useStoredFilters = (storageKey) => {
    const [filters, setFilters] = useState(() => readFilters(storageKey));
    useEffect(() => {
        try { localStorage.setItem(storageKey, JSON.stringify(filters)); } catch { /* storage unavailable */ }
    }, [storageKey, filters]);
    return [filters, setFilters];
};
