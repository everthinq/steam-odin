// Pure view helpers for the Ratatoskr transfer page, extracted from Transfer.jsx.
// No React — plain functions + the class-name maps they use.

const NO_COLLECTION_LABEL = 'No collection';

export const getCasketCount = (c) => c.item_storage_total ?? 0;

export const getItemCollection = (item) => {
    const name = item.item_collection?.trim();
    return name || NO_COLLECTION_LABEL;
};

const TRADE_HOLD_ROW_IDLE =
    'border-l-2 border-orange-500/50 bg-orange-950/30 hover:bg-orange-950/40';
const TRADE_HOLD_ROW_SELECTED = 'border-l-2 border-orange-400 bg-orange-500/15';
const TRADE_HOLD_VARIANT_IDLE =
    'border-l-2 border-orange-500/35 bg-orange-950/20';
const TRADE_HOLD_VARIANT_SELECTED = 'border-l-2 border-orange-400/80 bg-orange-500/10';

export const transferRowSurfaceClass = (onTradeHold, selected, { variant = false } = {}) => {
    if (!onTradeHold) {
        return selected !== 'none' ? 'bg-amber-500/10' : 'hover:bg-white/5';
    }
    if (selected !== 'none') {
        return variant ? TRADE_HOLD_VARIANT_SELECTED : TRADE_HOLD_ROW_SELECTED;
    }
    return variant ? TRADE_HOLD_VARIANT_IDLE : TRADE_HOLD_ROW_IDLE;
};
