// Manual drag-order + pin-to-top for Draupnir portfolio cards, persisted to
// localStorage (per-browser). Mirrors utils/accountDashboardLayout.js but keys
// on portfolio `id` and uses its own storage key so it never clashes with the
// Heimdall dashboard layout.
const STORAGE_KEY = 'draupnir-portfolio-layout';

export const loadPortfolioLayout = () => {
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

export const savePortfolioLayout = (layout) => {
    localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
            order: layout.order.map(String),
            pinned: layout.pinned.map(String),
        })
    );
};

/** Drop removed portfolios; append newly-seen ones to the end of order. */
export const mergeLayoutWithPortfolios = (layout, portfolios) => {
    const ids = new Set(portfolios.map((p) => String(p.id)));
    const order = layout.order.filter((id) => ids.has(id));
    const pinned = layout.pinned.filter((id) => ids.has(id));

    for (const p of portfolios) {
        const id = String(p.id);
        if (!order.includes(id)) order.push(id);
    }

    return { order, pinned };
};

export const sortPortfoliosForDashboard = (portfolios, layout) => {
    const pinnedSet = new Set(layout.pinned.map(String));
    const rank = (id) => {
        const i = layout.order.indexOf(String(id));
        return i === -1 ? Number.MAX_SAFE_INTEGER : i;
    };
    const cmp = (a, b) => {
        const byRank = rank(a.id) - rank(b.id);
        if (byRank !== 0) return byRank;
        return (a.name || '').localeCompare(b.name || '');
    };

    const pinned = portfolios.filter((p) => pinnedSet.has(String(p.id))).sort(cmp);
    const rest = portfolios.filter((p) => !pinnedSet.has(String(p.id))).sort(cmp);
    return [...pinned, ...rest];
};

export const togglePortfolioPin = (layout, id) => {
    const pid = String(id);
    const pinned = [...layout.pinned];
    const idx = pinned.indexOf(pid);
    if (idx >= 0) {
        pinned.splice(idx, 1);
        return { ...layout, pinned };
    }
    pinned.unshift(pid);
    const order = layout.order.filter((x) => x !== pid);
    order.unshift(pid);
    return { ...layout, pinned, order };
};

export const reorderPortfolioList = (layout, orderedPortfolios) => ({
    ...layout,
    order: orderedPortfolios.map((p) => String(p.id)),
});
