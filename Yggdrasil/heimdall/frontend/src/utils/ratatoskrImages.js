const STEAM_CDN = 'https://community.cloudflare.steamstatic.com/economy/image/';
const STEAM_APIS_ITEM = 'https://api.steamapis.com/image/item/730';

/** Strip prefixes that break market image lookup. */
export const cleanMarketName = (name) =>
    (name || '')
        .replace(/^★\s*/, '')
        .replace(/^StatTrak™\s*/, '')
        .trim();

/**
 * CS2 skin images: steamapis by market hash name.
 * Non-generated econ paths (crates, keys) may use Steam CDN.
 */
export const getItemImageUrl = (item) => {
    const marketName = cleanMarketName(item?.item_name);
    if (marketName) {
        return `${STEAM_APIS_ITEM}/${encodeURIComponent(marketName)}`;
    }
    const path = item?.item_url;
    if (path && !path.includes('default_generated')) {
        return `${STEAM_CDN}${path}/96fx96f`;
    }
    return null;
};

export const getStickerImageUrl = (sticker) => {
    if (sticker?.sticker_name) {
        return `${STEAM_APIS_ITEM}/${encodeURIComponent(sticker.sticker_name)}`;
    }
    return null;
};
