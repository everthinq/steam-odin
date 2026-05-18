/** CS2 (app 730) Steam Community Market listing URL for a market hash name. */
export const getSteamMarketListingUrl = (itemName) => {
    const name = (itemName || '').trim();
    if (!name) return null;
    return `https://steamcommunity.com/market/listings/730/${encodeURIComponent(name)}`;
};
