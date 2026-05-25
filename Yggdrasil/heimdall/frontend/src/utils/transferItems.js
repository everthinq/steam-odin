export const ALL_STORAGE_UNIT_ID = '__all_storage__';

export const ALL_STORAGE_UNIT = {
    item_id: ALL_STORAGE_UNIT_ID,
    isAllStorage: true,
    item_name: 'All storage units',
};

export const INVENTORY_LOCATION = 'Inventory';

/** Common CS2 wear shorthand → words stored in item_wear_name */
const WEAR_QUERY_ALIASES = {
    fn: 'factory new',
    mw: 'minimal wear',
    ft: 'field tested',
    'field-tested': 'field tested',
    ww: 'well worn',
    'well-worn': 'well worn',
    bs: 'battle scarred',
    'battle-scarred': 'battle scarred',
};

/**
 * Normalize market-style names for search (pipes, punctuation, symbols).
 * "FAMAS | Half Sleeve (Factory New)" → "famas half sleeve factory new"
 */
export const normalizeItemSearchText = (text) => {
    let s = (text || '')
        .toLowerCase()
        .replace(/[|★™®]/g, ' ')
        .replace(/[()[\]]/g, ' ')
        .replace(/-/g, ' ')
        .replace(/[^\w\s]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    const tokens = s.split(' ').filter(Boolean);
    const expanded = tokens.flatMap((token) => {
        const alias = WEAR_QUERY_ALIASES[token];
        return alias ? alias.split(' ') : [token];
    });
    return expanded.join(' ');
};

/**
 * Token search: every word in the query must appear somewhere in the fields.
 * Works without "|", wear in or out of parentheses, and out-of-order terms.
 */
export const matchesSearchQuery = (fields, query) => {
    const q = normalizeItemSearchText(query);
    if (!q) return true;

    const haystack = fields
        .flat()
        .filter((f) => f != null && f !== '')
        .map((f) => normalizeItemSearchText(String(f)))
        .join(' ');

    const tokens = q.split(' ').filter(Boolean);
    return tokens.every((token) => haystack.includes(token));
};

export const tagInventoryItems = (items) =>
    (items || []).map((item) => ({
        ...item,
        storage_unit_id: null,
        storage_unit_name: INVENTORY_LOCATION,
    }));

export const tagCasketItems = (items, casketId, casketName) =>
    (items || []).map((item) => ({
        ...item,
        storage_unit_id: casketId,
        storage_unit_name: casketName,
    }));

/** Display float (CS2 paint wear 0–1). */
export const formatItemFloat = (paintWear) => {
    if (paintWear == null || paintWear === '') return null;
    const n = Number(paintWear);
    if (!Number.isFinite(n)) return null;
    return n.toFixed(6);
};

/** Stable key segment for grouping identical float values. */
export const getItemFloatKey = (item) => {
    const pw = item?.item_paint_wear;
    if (pw == null || pw === '') return '';
    const n = Number(pw);
    if (!Number.isFinite(n)) return '';
    return n.toFixed(10);
};

/** Market line (name + wear) — used for row grouping and “one of each”. */
export const getItemSkinLineKey = (item) =>
    `${item?.item_name || 'Unknown'}\0${item?.item_wear_name || ''}`;

/** @deprecated alias */
export const getItemSkinKey = (item) => getItemSkinLineKey(item);

export const isItemOnTradeHold = (item) => {
    if (!item?.trade_unlock) return false;
    return new Date(item.trade_unlock) > new Date();
};

export const getTradeHoldDaysForItems = (items) => {
    let maxDays = 0;
    for (const item of items || []) {
        if (!isItemOnTradeHold(item)) continue;
        const unlock = new Date(item.trade_unlock);
        const days = Math.ceil((unlock - new Date()) / (1000 * 60 * 60 * 24));
        maxDays = Math.max(maxDays, days);
    }
    return maxDays;
};

export const itemsHaveTradeHold = (items) => getTradeHoldDaysForItems(items) > 0;

export const formatTradeHoldLabel = (items) => {
    const days = getTradeHoldDaysForItems(items);
    return days > 0 ? `${days}d` : '—';
};

/** Tradeable floats first; stacks on trade hold sink to the bottom. */
export const compareFloatVariantsWithTradeHoldLast = (a, b) => {
    const aHold = itemsHaveTradeHold(a.items) ? 1 : 0;
    const bHold = itemsHaveTradeHold(b.items) ? 1 : 0;
    if (aHold !== bHold) return aHold - bHold;
    return (a.item_paint_wear ?? 0) - (b.item_paint_wear ?? 0);
};

/**
 * Group by name + wear (and storage when requested). Distinct floats are nested
 * under `floatVariants` for the expandable picker in the transfer table.
 */
export const groupItemsByName = (items, { includeStorage = false } = {}) => {
    const map = new Map();
    for (const item of items) {
        const lineKey = getItemSkinLineKey(item);
        const key = includeStorage
            ? `${lineKey}\0${item.storage_unit_id ?? 'inventory'}`
            : lineKey;

        if (!map.has(key)) {
            map.set(key, {
                key,
                skin_line_key: lineKey,
                item_name: item.item_name,
                item_wear_name: item.item_wear_name,
                item_collection: item.item_collection,
                stickers: item.stickers || [],
                representative: item,
                item_ids: [],
                items: [],
                floatVariantMap: new Map(),
                storage_unit_id: includeStorage ? item.storage_unit_id ?? null : undefined,
                storage_unit_name: includeStorage
                    ? item.storage_unit_name ?? INVENTORY_LOCATION
                    : undefined,
            });
        }

        const line = map.get(key);
        line.item_ids.push(item.item_id);
        line.items.push(item);

        const floatSeg = getItemFloatKey(item) || '__no_float__';
        if (!line.floatVariantMap.has(floatSeg)) {
            line.floatVariantMap.set(floatSeg, {
                key: `${key}\0${floatSeg}`,
                item_paint_wear: item.item_paint_wear,
                item_collection: item.item_collection,
                stickers: item.stickers || [],
                representative: item,
                item_ids: [],
                items: [],
            });
        }
        const variant = line.floatVariantMap.get(floatSeg);
        variant.item_ids.push(item.item_id);
        variant.items.push(item);
    }

    return Array.from(map.values())
        .map((line) => {
            const floatVariants = Array.from(line.floatVariantMap.values())
                .map((v) => ({
                    ...v,
                    qty: v.item_ids.length,
                    onTradeHold: itemsHaveTradeHold(v.items),
                    tradeHoldDays: getTradeHoldDaysForItems(v.items),
                }))
                .sort(compareFloatVariantsWithTradeHoldLast);
            const { floatVariantMap, ...rest } = line;
            return {
                ...rest,
                qty: line.item_ids.length,
                floatVariants,
                hasMultipleFloats: floatVariants.length > 1,
                onTradeHold: itemsHaveTradeHold(line.items),
                tradeHoldDays: getTradeHoldDaysForItems(line.items),
            };
        })
        .sort((a, b) => {
            const loc = (a.storage_unit_name || '').localeCompare(b.storage_unit_name || '');
            if (loc !== 0) return loc;
            return (a.item_name || '').localeCompare(b.item_name || '');
        });
};

const itemSearchFields = (item) => {
    const stickerNames = (item.stickers || [])
        .map((s) => s.sticker_name || s.name)
        .filter(Boolean);
    const displayWithWear = item.item_wear_name
        ? `${item.item_name || ''} (${item.item_wear_name})`
        : item.item_name;

    return [
        item.item_name,
        item.item_wear_name,
        item.item_collection,
        item.storage_unit_name,
        displayWithWear,
        ...stickerNames,
    ];
};

export const filterItemsByQuery = (items, query) => {
    if (!query?.trim()) return items;
    return items.filter((item) => matchesSearchQuery(itemSearchFields(item), query));
};

/** Selectable count while keeping one copy in the source (inventory or storage). */
export const getGroupReserveOneQty = (group) => Math.max(0, (group?.qty ?? 0) - 1);

/**
 * To storage: max selectable for one float stack when reserve-one applies to the
 * whole skin line (name + wear). Another float on the line can be the kept copy.
 */
export const getVariantMaxSelectableToStorage = (lineGroup, variant, selectedIds) => {
    const budget = getGroupReserveOneQty(lineGroup);
    const variantIdSet = new Set(variant.item_ids);
    const otherSelected = lineGroup.item_ids.filter(
        (id) => selectedIds.includes(id) && !variantIdSet.has(id)
    ).length;
    return Math.min(variant.qty, Math.max(0, budget - otherSelected));
};

/** One item_id per name + wear line from a flat item list. */
export const pickOneItemIdPerSkin = (items) => {
    const byKey = new Map();
    for (const item of items) {
        if (item?.item_moveable === false || item?.def_index === 1201) continue;
        const key = getItemSkinLineKey(item);
        if (!byKey.has(key)) byKey.set(key, item.item_id);
    }
    return Array.from(byKey.values());
};

export const groupItemsByCasket = (itemIds, itemsWithStorage) => {
    const byCasket = new Map();
    for (const id of itemIds) {
        const item = itemsWithStorage.find((i) => i.item_id === id);
        if (!item?.storage_unit_id) continue;
        const cid = item.storage_unit_id;
        if (!byCasket.has(cid)) byCasket.set(cid, []);
        byCasket.get(cid).push(id);
    }
    return byCasket;
};
