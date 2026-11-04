import { useEffect, useState } from 'react';
import { readJson } from './readJson';

// The markets a holding can be valued on, from the backend (one list for
// Draupnir and Gjallarhorn: GET /api/huginn/valuation-markets). Asked once per
// page load and shared by every picker. A page keeps '' as its market until
// the user picks one, which the backend reads as its default.
let request = null;
const load = () => {
    if (!request) {
        request = fetch('/api/huginn/valuation-markets').then(readJson)
            .catch((error) => { request = null; throw error; });
    }
    return request;
};

export const useValuationMarkets = () => {
    const [view, setView] = useState({ markets: [], default: '' });
    useEffect(() => {
        let alive = true;
        load().then((data) => { if (alive) setView(data); }).catch(() => {});
        return () => { alive = false; };
    }, []);
    const chosen = (market) => market || view.default;
    const label = (market) => view.markets.find((m) => m.id === chosen(market))?.label || chosen(market);
    return { markets: view.markets, chosen, label };
};
