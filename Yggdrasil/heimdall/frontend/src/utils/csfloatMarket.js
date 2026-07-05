/**
 * CSFloat listing URL for a CS2 market hash name.
 *
 * Routes through pulse's short-link service, which 302-redirects to a csfloat.com
 * search for the item (sorted by lowest price).
 */
export const getCsfloatMarketListingUrl = (itemName) => {
    const name = (itemName || '').trim();
    if (!name) return null;
    return `https://short-pulse.tradeon.space/short-link/CsGo/CsFloat/${encodeURIComponent(name)}`;
};
