// Pure helpers for the Huginn Arbitrage filters (components/arbitrage/ArbitrageFilters.jsx).

export const EMPTY_FILTERS = { priceSide: 'buy', minPrice: '', maxPrice: '', accounts: [], collections: [] };

// '' or anything that is not a number → no bound.
export const parsePrice = (text) => {
    const value = parseFloat(String(text ?? '').replace(',', '.'));
    return Number.isFinite(value) && value >= 0 ? value : null;
};

export const hasPriceFilter = (filters) => parsePrice(filters.minPrice) !== null || parsePrice(filters.maxPrice) !== null;

export const activeFilterCount = (filters) =>
    (hasPriceFilter(filters) ? 1 : 0) + filters.accounts.length + filters.collections.length;

const money = (value) => `$${value % 1 === 0 ? value : value.toFixed(2)}`;

export const priceRangeLabel = (filters) => {
    const min = parsePrice(filters.minPrice);
    const max = parsePrice(filters.maxPrice);
    const side = filters.priceSide === 'sell' ? 'Sell' : 'Buy';
    if (min !== null && max !== null) return `${side} ${money(min)} – ${money(max)}`;
    if (min !== null) return `${side} ≥ ${money(min)}`;
    return `${side} ≤ ${money(max)}`;
};
