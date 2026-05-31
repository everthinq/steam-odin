const STORAGE_KEY = 'heimdall-dashboard-layout';

export const loadDashboardLayout = () => {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return { order: [], pinned: [] };
        const parsed = JSON.parse(raw);
        return {
            order: Array.isArray(parsed.order) ? parsed.order.map(String) : [],
            pinned: Array.isArray(parsed.pinned) ? parsed.pinned.map(String) : [],
        };
    } catch {
        return { order: [], pinned: [] };
    }
};

export const saveDashboardLayout = (layout) => {
    localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
            order: layout.order.map(String),
            pinned: layout.pinned.map(String),
        })
    );
};

/** Drop removed accounts; append new ones to the end of order. */
export const mergeLayoutWithAccounts = (layout, accounts) => {
    const ids = new Set(accounts.map((a) => String(a.steamid)));
    const order = layout.order.filter((id) => ids.has(id));
    const pinned = layout.pinned.filter((id) => ids.has(id));

    for (const account of accounts) {
        const id = String(account.steamid);
        if (!order.includes(id)) order.push(id);
    }

    return { order, pinned };
};

export const sortAccountsForDashboard = (accounts, layout) => {
    const pinnedSet = new Set(layout.pinned.map(String));
    const rank = (steamid) => {
        const i = layout.order.indexOf(String(steamid));
        return i === -1 ? Number.MAX_SAFE_INTEGER : i;
    };
    const cmp = (a, b) => {
        const byRank = rank(a.steamid) - rank(b.steamid);
        if (byRank !== 0) return byRank;
        return (a.account_name || '').localeCompare(b.account_name || '');
    };

    const pinned = accounts.filter((a) => pinnedSet.has(String(a.steamid))).sort(cmp);
    const rest = accounts.filter((a) => !pinnedSet.has(String(a.steamid))).sort(cmp);
    return [...pinned, ...rest];
};

export const toggleAccountPin = (layout, steamid) => {
    const id = String(steamid);
    const pinned = [...layout.pinned];
    const idx = pinned.indexOf(id);
    if (idx >= 0) {
        pinned.splice(idx, 1);
        return { ...layout, pinned };
    }
    pinned.unshift(id);
    const order = layout.order.filter((x) => x !== id);
    order.unshift(id);
    return { ...layout, pinned, order };
};

export const reorderAccountList = (layout, orderedAccounts) => ({
    ...layout,
    order: orderedAccounts.map((a) => String(a.steamid)),
});
