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

/**
 * Whether a Storage Unit can hold the item. Ratatoskr's `item_moveable` is false for what the
 * Game Coordinator refuses: untradable collectibles (service medals, veteran and operation coins,
 * Loyalty Badge, Global Offensive Badge), the default music kit, Storage Units, default weapons.
 * An item without the flag counts as storable.
 */
export const canGoIntoStorageUnit = (item) => item?.item_moveable !== false;

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

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/** Milliseconds until the item can be traded again (at `now`, default this moment); 0 when it is tradable. */
const tradeHoldMs = (item, now = Date.now()) => {
    if (!item?.trade_unlock) return 0;
    return Math.max(0, new Date(item.trade_unlock).getTime() - now);
};

export const isItemOnTradeHold = (item, now) => tradeHoldMs(item, now) > 0;

/**
 * Trade protection (Ratatoskr's `trade_protected_until`, game attribute 312): set on items received
 * in a trade with another player. Such an item cannot go into a Storage Unit until it ends. A plain
 * trade hold (a Steam Market purchase) has no protection and can be stored.
 */
export const isItemTradeProtected = (item, now = Date.now()) =>
    !!item?.trade_protected_until && new Date(item.trade_protected_until).getTime() > now;

/** "14h" under a day, otherwise whole days rounded up ("6d"). */
const formatHoldDuration = (ms) =>
    ms < DAY_MS ? `${Math.max(1, Math.ceil(ms / HOUR_MS))}h` : `${Math.ceil(ms / DAY_MS)}d`;

export const getTradeHoldDaysForItems = (items) => {
    let longest = 0;
    for (const item of items || []) longest = Math.max(longest, tradeHoldMs(item));
    return longest > 0 ? Math.ceil(longest / DAY_MS) : 0;
};

export const itemsHaveTradeHold = (items, now) => (items || []).some((item) => isItemOnTradeHold(item, now));

/** Time left on the trade hold: "6d", or the range "14h–7d" when the items unlock at different times; "—" when none is held. */
export const formatTradeHoldLabel = (items) => {
    const holds = (items || []).map((item) => tradeHoldMs(item)).filter((ms) => ms > 0);
    if (holds.length === 0) return '—';
    const shortest = formatHoldDuration(Math.min(...holds));
    const longest = formatHoldDuration(Math.max(...holds));
    return shortest === longest ? longest : `${shortest}–${longest}`;
};

/** When a trade hold ends, in the browser's time zone: "Thu 6 Nov, 08:00". */
export const formatTradeUnlock = (tradeUnlock) => {
    const date = new Date(tradeUnlock);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(undefined, {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
    });
};

/** Tradable variants first; held ones after, the soonest unlock first, then by float. */
export const compareFloatVariantsWithTradeHoldLast = (a, b) => {
    const aHold = a.onTradeHold ? 1 : 0;
    const bHold = b.onTradeHold ? 1 : 0;
    if (aHold !== bHold) return aHold - bHold;
    if (a.variantKind === 'unlock' && b.variantKind === 'unlock') {
        return new Date(a.trade_unlock) - new Date(b.trade_unlock);
    }
    return (a.item_paint_wear ?? 0) - (b.item_paint_wear ?? 0);
};

/**
 * Group by name + wear (and storage when requested), then split by trade status: copies tradable
 * now, copies on a plain trade hold (`onTradeHold`, e.g. a Steam Market purchase — can be stored)
 * and copies under trade protection (`tradeProtected`, received in a trade — cannot be stored) are
 * separate lines, so a mixed stack shows each. Each line nests `floatVariants` for the
 * expandable picker: one per distinct float (skins), or, for float-less items on hold (cases,
 * stickers), one per unlock time (`variantKind: 'unlock'`). `holdSplit` marks the lines whose item also has a line of the other
 * trade status; `trade_unlock` is set on a held line whose copies all unlock at the same moment.
 * `now` (milliseconds) is the moment holds are judged at: pass a clock that ticks, so a hold that
 * ends while the page is open moves its copies to the tradable line.
 */
export const groupItemsByName = (items, { includeStorage = false, markUnstorable = false, now = Date.now() } = {}) => {
    const map = new Map();
    for (const item of items) {
        const lineKey = getItemSkinLineKey(item);
        const storageKey = includeStorage
            ? `${lineKey}\0${item.storage_unit_id ?? 'inventory'}`
            : lineKey;
        // Depositing (`markUnstorable`): items a Storage Unit cannot hold get a line of their own,
        // so a line is all one or the other. Items already in a Storage Unit can always come out.
        const unstorable = markUnstorable && !canGoIntoStorageUnit(item);
        const onHold = isItemOnTradeHold(item, now);
        const tradeStatus = isItemTradeProtected(item, now) ? 'protected' : onHold ? 'hold' : 'tradable';
        const statusKey = `${storageKey}\0${unstorable ? 'unstorable' : 'storable'}`;
        const key = `${statusKey}\0${tradeStatus}`;

        if (!map.has(key)) {
            map.set(key, {
                key,
                status_key: statusKey,
                skin_line_key: lineKey,
                item_name: item.item_name,
                item_wear_name: item.item_wear_name,
                item_collection: item.item_collection,
                stickers: item.stickers || [],
                representative: item,
                unstorable,
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

        const floatKey = getItemFloatKey(item);
        const variantKind = floatKey ? 'float' : onHold ? 'unlock' : 'none';
        const variantSegment =
            variantKind === 'float' ? floatKey
                : variantKind === 'unlock' ? `unlock:${item.trade_unlock}`
                    : '__no_float__';
        if (!line.floatVariantMap.has(variantSegment)) {
            line.floatVariantMap.set(variantSegment, {
                key: `${key}\0${variantSegment}`,
                variantKind,
                trade_unlock: item.trade_unlock,
                item_paint_wear: item.item_paint_wear,
                item_collection: item.item_collection,
                stickers: item.stickers || [],
                representative: item,
                item_ids: [],
                items: [],
            });
        }
        const variant = line.floatVariantMap.get(variantSegment);
        variant.item_ids.push(item.item_id);
        variant.items.push(item);
    }

    const linesPerStatus = new Map();
    for (const line of map.values()) {
        linesPerStatus.set(line.status_key, (linesPerStatus.get(line.status_key) || 0) + 1);
    }

    return Array.from(map.values())
        .map((line) => {
            const floatVariants = Array.from(line.floatVariantMap.values())
                .map((v) => ({
                    ...v,
                    qty: v.item_ids.length,
                    onTradeHold: itemsHaveTradeHold(v.items, now),
                    tradeProtected: v.items.some((item) => isItemTradeProtected(item, now)),
                    tradeHoldDays: getTradeHoldDaysForItems(v.items),
                }))
                .sort(compareFloatVariantsWithTradeHoldLast);
            // Drop the working map from the returned line (floatVariants replaces it).
            const rest = { ...line };
            delete rest.floatVariantMap;
            const onTradeHold = itemsHaveTradeHold(line.items, now);
            const unlockTimes = new Set(line.items.map((item) => item.trade_unlock));
            return {
                ...rest,
                qty: line.item_ids.length,
                floatVariants,
                hasMultipleFloats: floatVariants.length > 1,
                variantKind: floatVariants[0]?.variantKind || 'none',
                onTradeHold,
                tradeProtected: line.items.some((item) => isItemTradeProtected(item, now)),
                tradeHoldDays: getTradeHoldDaysForItems(line.items),
                trade_unlock: onTradeHold && unlockTimes.size === 1 ? line.items[0].trade_unlock : null,
                holdSplit: linesPerStatus.get(line.status_key) > 1,
            };
        })
        .sort((a, b) => {
            const loc = (a.storage_unit_name || '').localeCompare(b.storage_unit_name || '');
            if (loc !== 0) return loc;
            const name = (a.item_name || '').localeCompare(b.item_name || '');
            if (name !== 0) return name;
            const wear = (a.item_wear_name || '').localeCompare(b.item_wear_name || '');
            if (wear !== 0) return wear;
            // Same item: tradable now first, then the plain trade hold, then trade protection.
            const rank = (line) => (line.tradeProtected ? 2 : line.onTradeHold ? 1 : 0);
            return rank(a) - rank(b);
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
