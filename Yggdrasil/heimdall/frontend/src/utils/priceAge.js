import { useEffect, useState } from 'react';

// How old a market's price is. Every pulse price carries the market's own update time
// (`offersUpdateTime`, epoch seconds); a price the market has not refreshed for a day or more
// is stale (on 2026-10-15 almost every price was minutes old, a few Steam ones 600+ days).
export const STALE_SECONDS = 24 * 60 * 60;

// The current time in epoch seconds, ticking once a minute; 0 until the first tick (then
// nothing counts as stale). One per page: pass it down instead of reading the clock in render.
export const useNowSeconds = (intervalMs = 60000) => {
    const [now, setNow] = useState(0);
    useEffect(() => {
        const tick = () => setNow(Date.now() / 1000);
        const first = setTimeout(tick, 0);
        const timer = setInterval(tick, intervalMs);
        return () => { clearTimeout(first); clearInterval(timer); };
    }, [intervalMs]);
    return now;
};

export const priceAgeSeconds = (updated, now) => (updated && now ? Math.max(0, now - updated) : null);

export const isStale = (updated, now) => {
    const age = priceAgeSeconds(updated, now);
    return age !== null && age >= STALE_SECONDS;
};

export const formatAge = (seconds) => {
    if (seconds == null) return '';
    if (seconds < 90) return 'just now';
    if (seconds < 3600) return `${Math.round(seconds / 60)} minutes ago`;
    if (seconds < 2 * 86400) return `${Math.round(seconds / 3600)} hours ago`;
    if (seconds < 60 * 86400) return `${Math.round(seconds / 86400)} days ago`;
    return `${Math.round(seconds / (30 * 86400))} months ago`;
};

// Tooltip text for a stale price.
export const staleTip = (updated, now) =>
    `The market last updated this price ${formatAge(priceAgeSeconds(updated, now))}: it may no longer be there.`;
