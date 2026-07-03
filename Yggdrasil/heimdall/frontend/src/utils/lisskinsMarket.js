/**
 * LisSkins listing URL for a CS2 market hash name.
 *
 * Routes through pulse's short-link service, which 302-redirects to a lis-skins.ru
 * market search for the item.
 */
export const getLisskinsMarketListingUrl = (itemName) => {
    const name = (itemName || '').trim();
    if (!name) return null;
    return `https://short-pulse.tradeon.space/short-link/CsGo/LisSkins/${encodeURIComponent(name)}`;
};
