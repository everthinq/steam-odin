/**
 * Buff163 listing URL for a CS2 market hash name.
 *
 * Buff keys its pages by an opaque numeric goods_id (not the item name), so we can't
 * build the URL directly. Instead we point at pulse's short-link service, which holds a
 * name→goods_id map and 302-redirects to https://buff.163.com/goods/<id>. Unknown items
 * 404 there (not in pulse's map).
 */
export const getBuffMarketListingUrl = (itemName) => {
    const name = (itemName || '').trim();
    if (!name) return null;
    return `https://short-pulse.tradeon.space/short-link/CsGo/Buff/${encodeURIComponent(name)}`;
};
