export const ALL_STORAGE_UNIT_ID = '__all_storage__';

export const ALL_STORAGE_UNIT = {
    item_id: ALL_STORAGE_UNIT_ID,
    isAllStorage: true,
    item_name: 'All storage units',
};

export const INVENTORY_LOCATION = 'Inventory';

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

export const filterItemsByQuery = (items, query) => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => {
        const name = (item.item_name || '').toLowerCase();
        const wear = (item.item_wear_name || '').toLowerCase();
        const collection = (item.item_collection || '').toLowerCase();
        const storage = (item.storage_unit_name || '').toLowerCase();
        return (
            name.includes(q) ||
            wear.includes(q) ||
            collection.includes(q) ||
            storage.includes(q)
        );
    });
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
