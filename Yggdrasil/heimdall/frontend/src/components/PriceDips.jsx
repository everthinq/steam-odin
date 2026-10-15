import React, { useEffect, useMemo, useRef, useState, useDeferredValue } from 'react';
import { RefreshCw, Search, AlertTriangle, TrendingDown, TrendingUp } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';
import { parsePrice } from '../utils/arbitrageFilters';
import { useNowSeconds, isStale } from '../utils/priceAge';
import SteamMarketLink from './SteamMarketLink';
import PriceCell from './arbitrage/PriceCell';
import InfoTip from './gjallarhorn/InfoTip';

// Huginn → Price dips: one market's lowest listing against its own 30-day median (or average)
// from Tradeon pulse, with the weekly sales pulse reports. "Under" = listed below its usual
// month (a dip to buy, if it comes back); "Over" = listed above it (a spike: with "My Inventory",
// the items you hold that sell above their usual price right now). Backend:
// GET /api/huginn/price-dips (pulse_views.price_dips).
const ITEM_IMG_BASE = 'https://api.steamapis.com/image/item/730/';
const PAGE_SIZE = 150;
const SETTINGS_KEY = 'huginn.priceDips';
const DEFAULTS = { market: 'Steam', basis: 'Median30d', direction: 'under', minAway: '10', minSales: '10', minPrice: '1', hideStale: true };

const loadSettings = () => {
    try { return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}) }; } catch { return DEFAULTS; }
};

const money = (value) => (value == null ? '—' : `$${value.toFixed(2)}`);
const BASIS_LABEL = { Median30d: '30-day median', Average30d: '30-day average' };

const HELP = 'Lowest listing now vs the price the item usually had over the last 30 days (pulse’s 30-day '
    + 'median or average on the same market). A median lags: an item that has been falling for a month '
    + 'looks like a dip. Before buying, check that it sells every week (Sold / week) and look at the '
    + 'price chart (Steam icon). "Back to usual" is what you would make buying at the listing and '
    + 'selling at that usual price, after this market’s fee from the Fees editor.';

const PriceDips = ({ byHash }) => {
    const [settings, setSettings] = useState(loadSettings);
    const [markets, setMarkets] = useState([]);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [myInventory, setMyInventory] = useState(false);
    // How many rows are painted, for one list (market, basis, filters, search); a new list starts at PAGE_SIZE.
    const [shown, setShown] = useState({ list: '', count: PAGE_SIZE });
    const deferredSearch = useDeferredValue(search);
    const request = useRef(0);
    const now = useNowSeconds();

    const update = (patch) => setSettings(prev => {
        const next = { ...prev, ...patch };
        try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(next)); } catch { /* private window */ }
        return next;
    });

    useEffect(() => {
        fetch('/api/huginn/price-dips/markets')
            .then(response => (response.ok ? response.json() : null))
            .then(answer => { if (answer?.markets) setMarkets(answer.markets); })
            .catch(() => {});
    }, []);

    const load = async (fresh = false) => {
        const n = ++request.current;
        setLoading(true);
        setError(null);
        try {
            const params = new URLSearchParams({ market: settings.market, basis: settings.basis });
            if (fresh) params.set('fresh', '1');
            const res = await fetch(`/api/huginn/price-dips?${params}`);
            const answer = await res.json();
            if (n !== request.current) return;
            if (!res.ok) throw new Error(answer.error || 'Could not load prices');
            setData(answer);
        } catch (err) {
            if (n === request.current) setError(err.message);
        } finally {
            if (n === request.current) setLoading(false);
        }
    };

    // Load when the market or the basis changes (pulse tables are shared and kept 10 minutes,
    // so switching back is instant).
    useEffect(() => {
        const timer = setTimeout(() => load(), 0);
        return () => clearTimeout(timer);
    }, [settings.market, settings.basis]); // eslint-disable-line react-hooks/exhaustive-deps

    const rows = useMemo(() => {
        if (!data?.rows) return [];
        const minAway = parsePrice(settings.minAway) ?? 0;
        const minSales = parsePrice(settings.minSales) ?? 0;
        const minPrice = parsePrice(settings.minPrice);
        const under = settings.direction === 'under';
        const q = deferredSearch.trim();
        const out = data.rows.filter(row => {
            const away = row.vs_basis_percent;
            if (under ? away > -minAway : away < minAway) return false;
            const sold = row.sold_week ?? row.steam_sold_week;
            if (minSales > 0 && (sold == null || sold < minSales)) return false;
            if (minPrice !== null && row.price < minPrice) return false;
            if (settings.hideStale && isStale(row.updated, now)) return false;
            if (myInventory && !byHash?.[row.name]) return false;
            if (q && !matchesSearchQuery([row.name], q)) return false;
            return true;
        });
        return under ? out : out.reverse();      // the backend sorts deepest dip first
    }, [data, settings, deferredSearch, myInventory, byHash, now]);

    const listKey = `${data?.downloaded_at}|${JSON.stringify(settings)}|${deferredSearch}|${myInventory}`;
    const visible = shown.list === listKey ? shown.count : PAGE_SIZE;

    const fee = data?.fee ?? 0;
    const basisLabel = BASIS_LABEL[settings.basis] || settings.basis;
    const marketName = data?.display || settings.market;

    return (
        <div className="flex-1 flex flex-col gap-3 min-h-0">
            {/* Controls */}
            <div className="shrink-0 flex flex-wrap items-center gap-2 bg-odin-blue/30 border border-white/5 rounded-xl px-4 py-3">
                <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Market</span>
                <select value={settings.market} onChange={event => update({ market: event.target.value })}
                    className="bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40">
                    {(markets.length ? markets : [{ id: 'Steam', display: 'Steam' }]).map(market => (
                        <option key={market.id} value={market.id}>{market.display}{market.premium ? ' (pulse subscription)' : ''}</option>
                    ))}
                </select>
                <select value={settings.basis} onChange={event => update({ basis: event.target.value })}
                    className="bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500/40">
                    <option value="Median30d">vs 30-day median</option>
                    <option value="Average30d">vs 30-day average</option>
                </select>
                <div className="flex items-center p-0.5 rounded-lg bg-black/30 border border-white/10">
                    <button type="button" onClick={() => update({ direction: 'under' })}
                        title="Listed under its usual price: candidates to buy"
                        className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium ${settings.direction === 'under' ? 'bg-emerald-600/80 text-white' : 'text-slate-400 hover:text-white'}`}>
                        <TrendingDown size={12} /> Under (dips)
                    </button>
                    <button type="button" onClick={() => update({ direction: 'over' })}
                        title="Listed above its usual price: with My Inventory, what to sell now"
                        className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium ${settings.direction === 'over' ? 'bg-red-600/80 text-white' : 'text-slate-400 hover:text-white'}`}>
                        <TrendingUp size={12} /> Over (spikes)
                    </button>
                </div>
                <label className="flex items-center gap-1 text-xs text-slate-400" title="How far from its usual price, at least">
                    at least
                    <input value={settings.minAway} onChange={event => update({ minAway: event.target.value })} inputMode="decimal"
                        className="w-12 bg-black/30 border border-white/10 rounded px-1.5 py-1 text-sm text-white text-right" />%
                </label>
                <label className="flex items-center gap-1 text-xs text-slate-400" title="Sold in the last week on this market (Steam's count when pulse has none for it), at least">
                    sold / week ≥
                    <input value={settings.minSales} onChange={event => update({ minSales: event.target.value })} inputMode="numeric"
                        className="w-14 bg-black/30 border border-white/10 rounded px-1.5 py-1 text-sm text-white text-right" />
                </label>
                <label className="flex items-center gap-1 text-xs text-slate-400" title="Lowest listing, at least (US dollars)">
                    price ≥ $
                    <input value={settings.minPrice} onChange={event => update({ minPrice: event.target.value })} inputMode="decimal"
                        className="w-14 bg-black/30 border border-white/10 rounded px-1.5 py-1 text-sm text-white text-right" />
                </label>
                <button type="button" onClick={() => update({ hideStale: !settings.hideStale })}
                    title="Hide prices the market has not updated for a day or more"
                    className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border ${settings.hideStale ? 'bg-slate-500/20 border-slate-400/40 text-slate-200' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white'}`}>
                    Hide stale
                </button>
                <InfoTip tip={HELP}><span className="text-xs text-slate-500 underline decoration-dotted cursor-help">how to read it</span></InfoTip>
                <button type="button" onClick={() => load(true)} disabled={loading}
                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600/70 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50">
                    <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
                    {loading ? 'Loading…' : 'Refresh'}
                </button>
            </div>

            {error && (
                <div className="shrink-0 bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-lg text-sm flex items-center gap-2">
                    <AlertTriangle size={14} /> {error}
                </div>
            )}

            {/* Table */}
            <div className="flex-1 flex flex-col bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden min-h-0">
                <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/10">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input type="text" placeholder="Search items (e.g. ak redline ft)" value={search} onChange={event => setSearch(event.target.value)}
                            className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-base text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                    </div>
                    <button type="button" onClick={() => setMyInventory(on => !on)} disabled={!byHash}
                        title={byHash ? 'Only items you hold (from "Get all items")' : 'Press "Get all items" first'}
                        className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40 ${myInventory ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                        My Inventory
                    </button>
                    <span className="text-sm text-slate-500 shrink-0">
                        {data ? <>showing {Math.min(visible, rows.length)} of {rows.length} <span className="text-slate-600">({data.rows.length.toLocaleString()} {marketName} items with a {basisLabel})</span></> : loading ? 'Loading pulse tables…' : ''}
                    </span>
                </div>
                <div className="shrink-0 grid grid-cols-[minmax(0,2.4fr)_56px_84px_92px_84px_96px_84px_76px] gap-2 px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20">
                    <span>Item</span>
                    <span className="text-right">Owned</span>
                    <span className="text-right" title={`${marketName}'s lowest listing now`}>Listed</span>
                    <span className="text-right">{basisLabel}</span>
                    <span className="text-right" title={`Lowest listing vs the ${basisLabel}`}>vs usual</span>
                    <span className="text-right" title={`Buying at the listing and selling at the ${basisLabel}, after ${marketName}'s fee (${(fee * 100).toFixed(1)}%)`}>Back to usual</span>
                    <span className="text-right" title={data?.has_instant ? `${marketName}'s highest buy order: sell at once` : `${marketName} has no buy orders on pulse`}>Sells now</span>
                    <span className="text-right" title="Sold in the last week (pulse); hover for Steam and Market.CSGO">Sold / week</span>
                </div>
                <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
                    {rows.slice(0, visible).map(row => {
                        const owned = byHash?.[row.name];
                        const back = row.basis * (1 - fee) - row.price;
                        return (
                            <div key={row.name} className="grid grid-cols-[minmax(0,2.4fr)_56px_84px_92px_84px_96px_84px_76px] gap-2 items-center px-4 py-2 border-b border-white/5 hover:bg-white/[0.03]">
                                <div className="flex items-center gap-2.5 min-w-0">
                                    <img src={`${ITEM_IMG_BASE}${encodeURIComponent(row.name)}`} alt="" loading="lazy" decoding="async"
                                        referrerPolicy="no-referrer" className="w-9 h-9 object-contain shrink-0 rounded bg-black/20" />
                                    <span className="text-base text-white truncate" title={row.name}>{row.name}</span>
                                    <SteamMarketLink itemName={row.name} viaPulse />
                                </div>
                                <span className="text-base text-right font-bold text-amber-400 tabular-nums">{owned ? owned.count : <span className="text-slate-600">—</span>}</span>
                                <PriceCell market={settings.market} itemName={row.name} price={row.price} updated={row.updated} now={now}
                                    className="text-base text-right text-slate-200 tabular-nums" />
                                <span className="text-base text-right text-slate-400 tabular-nums">{money(row.basis)}</span>
                                <span className={`text-base text-right font-semibold tabular-nums ${row.vs_basis_percent < 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                    {row.vs_basis_percent > 0 ? '+' : ''}{row.vs_basis_percent.toFixed(0)}%
                                </span>
                                <span className={`text-base text-right tabular-nums ${back > 0 ? 'text-emerald-400' : 'text-slate-500'}`}
                                    title={`${money(row.basis)} × (1 − ${(fee * 100).toFixed(1)}%) − ${money(row.price)}`}>
                                    {back > 0 ? '+' : ''}{money(back)} <span className="text-xs text-slate-500">{row.price ? `${((back / row.price) * 100).toFixed(0)}%` : ''}</span>
                                </span>
                                <span className="text-base text-right text-slate-400 tabular-nums">{money(row.instant)}</span>
                                <span className="text-base text-right text-slate-300 tabular-nums"
                                    title={[`Steam: ${row.steam_sold_week ?? '—'}`, `Market.CSGO: ${row.tm_sold_week ?? '—'}`, `Offers listed: ${row.count ?? '—'}`].join('\n')}>
                                    {(row.sold_week ?? row.steam_sold_week)?.toLocaleString() ?? '—'}
                                    {row.sold_week == null && row.steam_sold_week != null && <span className="text-[10px] text-slate-500"> Steam</span>}
                                </span>
                            </div>
                        );
                    })}
                    {visible < rows.length && (
                        <div className="flex justify-center py-4">
                            <button type="button" onClick={() => setShown({ list: listKey, count: visible + PAGE_SIZE })}
                                className="px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-300 hover:text-white hover:border-white/20">
                                Load {Math.min(PAGE_SIZE, rows.length - visible)} more
                                <span className="text-slate-500 ml-2">({rows.length - visible} left)</span>
                            </button>
                        </div>
                    )}
                    {data && !rows.length && (
                        <div className="text-center text-slate-500 text-sm py-12">Nothing matches. Lower “at least %”, “sold / week” or the price.</div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default PriceDips;
