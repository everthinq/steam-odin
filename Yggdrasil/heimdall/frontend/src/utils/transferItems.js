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

export const groupItemsByName = (items, { includeStorage = false } = {}) => {
    const map = new Map();
    for (const item of items) {
        const baseKey = item.item_name || 'Unknown';
        const key = includeStorage
            ? `${baseKey}\0${item.storage_unit_id ?? 'inventory'}`
            : baseKey;
        if (!map.has(key)) {
            map.set(key, {
                key,
                item_name: item.item_name,
                item_wear_name: item.item_wear_name,
                item_collection: item.item_collection,
                stickers: item.stickers || [],
                representative: item,
                item_ids: [item.item_id],
                items: [item],
                storage_unit_id: includeStorage ? item.storage_unit_id ?? null : undefined,
                storage_unit_name: includeStorage
                    ? item.storage_unit_name ?? INVENTORY_LOCATION
                    : undefined,
            });
        } else {
            const group = map.get(key);
            group.item_ids.push(item.item_id);
            group.items.push(item);
        }
    }
    return Array.from(map.values())
        .map((g) => ({ ...g, qty: g.item_ids.length }))
        .sort((a, b) => {
            const loc = (a.storage_unit_name || '').localeCompare(b.storage_unit_name || '');
            if (loc !== 0) return loc;
            return a.item_name.localeCompare(b.item_name);
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
