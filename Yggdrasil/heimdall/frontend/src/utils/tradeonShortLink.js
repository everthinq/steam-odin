/**
 * pulse short-link URL for an item on a given market.
 *
 * `market` is pulse's market slug (e.g. "TradeOnMarket", "Steam", "Buff", "LisSkins").
 * The service 302-redirects to that market's page/search for the item.
 */
export const getTradeonShortLink = (market, itemName) => {
    const name = (itemName || '').trim();
    if (!name || !market) return null;
    return `https://short-pulse.tradeon.space/short-link/CsGo/${market}/${encodeURIComponent(name)}`;
};
