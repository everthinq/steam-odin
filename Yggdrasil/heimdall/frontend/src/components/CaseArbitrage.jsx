import React, { useState, useEffect, useMemo, useRef, useDeferredValue } from 'react';
import { Search, RefreshCw, AlertTriangle, ArrowRight, TrendingUp, TrendingDown, ArrowUpDown, ChevronRight, Flame, Layers, Rows3, Bell } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';
import { getTradeonShortLink } from '../utils/tradeonShortLink';
import CaseAlertsPanel from './CaseAlertsPanel';

const PAGE_SIZE = 150;

// market key (backend) -> display label, short chip code, pulse short-link slug.
// `sellable` marks markets you can realistically cash out on (drives flip/profit).
const MARKETS = [
    { key: 'tradeon', label: 'Tradeon', short: 'TO', slug: 'TradeOnMarket', sellable: false },
    { key: 'steam', label: 'Steam', short: 'Steam', slug: 'Steam', sellable: true },
    { key: 'buff', label: 'Buff', short: 'Buff', slug: 'Buff', sellable: true },
    { key: 'csfloat', label: 'CSFloat', short: 'CF', slug: 'CsFloat', sellable: true },
    { key: 'lisskins', label: 'LisSkins', short: 'LIS', slug: 'LisSkins', sellable: false },
    { key: 'dmarket', label: 'DMarket', short: 'DM', slug: 'Dmarket', sellable: false },
];
const MARKET_BY_KEY = Object.fromEntries(MARKETS.map(m => [m.key, m]));

const CATEGORIES = [
    { slug: 'case', label: 'Cases' },
    { slug: 'sticker', label: 'Sticker Capsules' },
    { slug: 'souvenir', label: 'Souvenir Packages' },
    { slug: 'autograph', label: 'Autograph Capsules' },
];

const money = (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);
const compact = (n) => (n == null ? '—' : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

// Tiny inline sparkline of the cheapest-price series.
const Sparkline = ({ points, up }) => {
    if (!points || points.length < 2) return <span className="text-slate-600 text-xs">—</span>;
    const w = 56, h = 18, pad = 1;
    const min = Math.min(...points), max = Math.max(...points);
    const span = max - min || 1;
    const step = (w - pad * 2) / (points.length - 1);
    const d = points
        .map((p, i) => `${(pad + i * step).toFixed(1)},${(h - pad - ((p - min) / span) * (h - pad * 2)).toFixed(1)}`)
        .join(' ');
    return (
        <svg width={w} height={h} className="shrink-0" aria-hidden="true">
            <polyline points={d} fill="none" strokeWidth="1.5"
                className={up ? 'stroke-emerald-400' : 'stroke-red-400'}
                strokeLinejoin="round" strokeLinecap="round" />
        </svg>
    );
};

const SortHead = ({ label, k, sortKey, dir, onSort, className = '' }) => (
    <button type="button" onClick={() => onSort(k)}
        className={`flex items-center gap-1 hover:text-white transition-colors ${sortKey === k ? 'text-amber-300' : ''} ${className}`}>
        {label}
        {sortKey === k
            ? (dir === 'asc' ? <TrendingUp size={11} /> : <TrendingDown size={11} />)
            : <ArrowUpDown size={10} className="opacity-40" />}
    </button>
);

const Trend = ({ pct, points }) => {
    const up = (pct ?? 0) >= 0;
    return (
        <div className="flex items-center justify-end gap-1.5">
            <Sparkline points={points} up={up} />
            {pct != null && (
                <span className={`text-[11px] tabular-nums shrink-0 ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                    {up ? '+' : ''}{pct.toFixed(1)}%
                </span>
            )}
        </div>
    );
};

const Flip = ({ flip }) => {
    if (!flip) return <span className="text-slate-600 text-sm">—</span>;
    const good = flip.profit > 0;
    return (
        <div className="flex items-center gap-2 min-w-0">
            <span className="flex items-center gap-1 text-[11px] text-slate-400 shrink-0">
                <span className="capitalize">{flip.buy_market}</span>
                <ArrowRight size={10} className="text-slate-600" />
                <span className="capitalize">{flip.sell_market}</span>
            </span>
            <span className={`text-xs font-semibold tabular-nums shrink-0 ${good ? 'text-emerald-400' : 'text-red-400'}`}>
                {flip.profit >= 0 ? '+' : ''}{money(flip.profit)}
                <span className="opacity-70"> ({flip.profit_pct >= 0 ? '+' : ''}{flip.profit_pct?.toFixed(1)}%)</span>
            </span>
        </div>
    );
};

const NameCell = ({ r, copiedName, onCopy }) => (
    <div className="min-w-0 flex items-center gap-1.5">
        <p className="text-base text-white truncate cursor-pointer hover:text-amber-300 transition-colors"
            title={`${r.name}\n(click to copy)`}
            onClick={(e) => { e.stopPropagation(); onCopy(r.name); }}>
            {r.name}
        </p>
        {copiedName === r.name && <span className="shrink-0 text-[10px] font-medium text-emerald-400">copied</span>}
        {r.hot_today && (
            <span title="Unusually profitable today (vs its own recent norm, or the field today)"
                className="shrink-0 inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-orange-500/15 border border-orange-500/40 text-orange-300">
                <Flame size={9} /> HOT
            </span>
        )}
    </div>
);

const Img = ({ src }) => src ? (
    <img src={src} alt="" className="w-9 h-9 object-contain shrink-0 rounded bg-black/20"
        loading="lazy" decoding="async" referrerPolicy="no-referrer" />
) : <div className="w-9 h-9 shrink-0 rounded bg-black/20" />;

// grid templates per layout (spread column removed — we track real profit only)
const GRID_CHIPS = 'grid grid-cols-[minmax(0,1.5fr)_minmax(220px,2.6fr)_64px_minmax(150px,1.3fr)_104px] gap-2';
const GRID_EXPAND = 'grid grid-cols-[minmax(0,2.2fr)_120px_minmax(150px,1fr)_64px_104px] gap-2';

const CaseArbitrage = () => {
    const [categories, setCategories] = useState(['case']);
    const [layout, setLayout] = useState('chips'); // 'chips' | 'expand'
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
    const [copiedName, setCopiedName] = useState('');
    const [sortKey, setSortKey] = useState('flip'); // flip | cheapest | liquidity | name
    const [sortDir, setSortDir] = useState('desc');
    const [minProfit, setMinProfit] = useState('');
    const [profitableOnly, setProfitableOnly] = useState(false);
    const [hotOnly, setHotOnly] = useState(false);
    const [showAlerts, setShowAlerts] = useState(false);
    const [expanded, setExpanded] = useState({});
    const deferredSearch = useDeferredValue(search);
    const retryRef = useRef(null);

    const fetchCases = async (cats) => {
        setLoading(true);
        setError(null);
        try {
            const qs = cats.length ? `?types=${cats.join(',')}` : '';
            const r = await fetch(`/api/huginn/cases${qs}`);
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Fetch failed');
            setData(d);
            const warming = Object.values(d.status || {}).some(s => s === 'refreshing');
            clearTimeout(retryRef.current);
            if (warming) retryRef.current = setTimeout(() => fetchCases(cats), 4000);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchCases(categories);
        return () => clearTimeout(retryRef.current);
    }, [categories]);

    useEffect(() => { setVisibleCount(PAGE_SIZE); },
        [deferredSearch, categories, sortKey, sortDir, minProfit, profitableOnly, hotOnly]);

    const toggleCategory = (slug) => {
        setCategories(prev => {
            if (prev.includes(slug)) {
                const next = prev.filter(s => s !== slug);
                return next.length ? next : prev; // keep at least one
            }
            return [...prev, slug];
        });
    };

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    const setSort = (key) => {
        if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
        else { setSortKey(key); setSortDir(key === 'name' ? 'asc' : 'desc'); }
    };

    const containers = data?.containers ?? [];

    const sorted = useMemo(() => {
        const val = (r) => {
            if (sortKey === 'name') return r.name?.toLowerCase() ?? '';
            if (sortKey === 'cheapest') return r.cheapest;
            if (sortKey === 'liquidity') return r.liquidity;
            return r.flip?.profit_pct; // default: best net flip %
        };
        const dir = sortDir === 'asc' ? 1 : -1;
        return [...containers].sort((a, b) => {
            const av = val(a), bv = val(b);
            if (av == null && bv == null) return 0;
            if (av == null) return 1;   // nulls always sink
            if (bv == null) return -1;
            if (av < bv) return -1 * dir;
            if (av > bv) return 1 * dir;
            return 0;
        });
    }, [containers, sortKey, sortDir]);

    const filtered = useMemo(() => {
        const q = deferredSearch.trim();
        const min = parseFloat(minProfit);
        const hasMin = !Number.isNaN(min);
        return sorted.filter(r => {
            if (q && !matchesSearchQuery([r.name, r.type], q)) return false;
            if (hasMin && !((r.flip?.profit_pct ?? -Infinity) >= min)) return false;
            if (profitableOnly && !(r.flip && r.flip.profit > 0)) return false;
            if (hotOnly && !r.hot_today) return false;
            return true;
        });
    }, [sorted, deferredSearch, minProfit, profitableOnly, hotOnly]);

    const visible = useMemo(() => filtered.slice(0, visibleCount), [filtered, visibleCount]);

    const warming = Object.values(data?.status || {}).some(s => s === 'refreshing');
    const noToken = Object.values(data?.status || {}).some(s => s === 'no_token');

    // ---- row renderers -------------------------------------------------

    const renderChipsRow = (r, idx) => (
        <div key={`${r.id}-${idx}`} className={`${GRID_CHIPS} items-center px-4 py-2.5 border-b border-white/5 hover:bg-white/[0.03] transition-colors`}>
            <div className="flex items-center gap-2.5 min-w-0">
                <Img src={r.image} />
                <NameCell r={r} copiedName={copiedName} onCopy={copyItemName} />
            </div>

            {/* wrapping price chips */}
            <div className="flex flex-wrap gap-1.5">
                {MARKETS.map(m => {
                    const p = r.prices?.[m.key];
                    if (p == null) return null;
                    const isCheap = r.cheapest_market === m.key;
                    const cnt = r.counts?.[m.key];
                    const href = getTradeonShortLink(m.slug, r.name);
                    return (
                        <a key={m.key} href={href} target="_blank" rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            title={`${r.name} on ${m.label}${cnt != null ? ` · ${cnt} listed` : ''}${m.sellable ? '' : ' · not a sell venue'}`}
                            className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs tabular-nums border transition-colors ${
                                isCheap
                                    ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300 font-semibold'
                                    : m.sellable
                                        ? 'bg-black/20 border-white/10 text-slate-300 hover:border-white/25'
                                        : 'bg-black/10 border-white/5 text-slate-500 hover:border-white/15'}`}>
                            <span className={`text-[10px] ${isCheap ? 'text-emerald-400/80' : 'text-slate-500'}`}>{m.short}</span>
                            {money(p)}
                            {isCheap && <span className="text-emerald-400">✓</span>}
                        </a>
                    );
                })}
            </div>

            <span className="text-sm text-right tabular-nums text-slate-400" title={r.total_listings != null ? `${r.total_listings} total across markets` : ''}>
                {compact(r.liquidity)}
            </span>
            <Flip flip={r.flip} />
            <Trend pct={r.trend_pct} points={r.sparkline} />
        </div>
    );

    const renderExpandRow = (r, idx) => {
        const open = !!expanded[r.id];
        const flip = r.flip;
        return (
            <div key={`${r.id}-${idx}`} className="border-b border-white/5">
                <div className={`${GRID_EXPAND} items-center px-4 py-2.5 hover:bg-white/[0.03] transition-colors cursor-pointer`}
                    onClick={() => setExpanded(e => ({ ...e, [r.id]: !e[r.id] }))}>
                    <div className="flex items-center gap-2 min-w-0">
                        <ChevronRight size={15} className={`text-slate-500 shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
                        <Img src={r.image} />
                        <NameCell r={r} copiedName={copiedName} onCopy={copyItemName} />
                    </div>
                    {/* Buy@ */}
                    <div className="text-right">
                        <div className="text-base tabular-nums text-emerald-400 font-semibold">{money(r.cheapest)}</div>
                        <div className="text-[10px] text-slate-500 capitalize">{r.cheapest_market || ''}</div>
                    </div>
                    {/* Flip (net of fee) */}
                    <div className="text-right">
                        {flip ? (
                            <>
                                <div className={`text-sm tabular-nums font-semibold ${flip.profit > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                                    {flip.profit >= 0 ? '+' : ''}{money(flip.profit)} ({flip.profit_pct?.toFixed(1)}%)
                                </div>
                                <div className="text-[10px] text-slate-500 capitalize">{money(flip.net_sell)} on {flip.sell_market}</div>
                            </>
                        ) : <span className="text-slate-600 text-sm">—</span>}
                    </div>
                    <span className="text-sm text-right tabular-nums text-slate-400">{compact(r.liquidity)}</span>
                    <Trend pct={r.trend_pct} points={r.sparkline} />
                </div>
                {open && (
                    <div className="px-4 pb-3 pl-14 flex flex-wrap gap-x-5 gap-y-1.5 bg-black/10">
                        {MARKETS.map(m => {
                            const p = r.prices?.[m.key];
                            const cnt = r.counts?.[m.key];
                            const isCheap = r.cheapest_market === m.key && p != null;
                            const href = p != null ? getTradeonShortLink(m.slug, r.name) : null;
                            const inner = (
                                <>
                                    <span className={`text-[11px] ${isCheap ? 'text-emerald-400' : 'text-slate-500'}`}>{m.label}{!m.sellable && <span className="text-slate-600"> (buy only)</span>}</span>
                                    <span className={`tabular-nums ${p == null ? 'text-slate-600' : isCheap ? 'text-emerald-400 font-semibold' : 'text-slate-300'}`}>{money(p)}</span>
                                    {cnt != null && <span className="text-[10px] text-slate-600">×{cnt}</span>}
                                </>
                            );
                            return href ? (
                                <a key={m.key} href={href} target="_blank" rel="noopener noreferrer"
                                    onClick={(e) => e.stopPropagation()}
                                    className="flex items-center gap-1.5 hover:underline">{inner}</a>
                            ) : <span key={m.key} className="flex items-center gap-1.5">{inner}</span>;
                        })}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="flex-1 flex flex-col bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden min-h-0">
            {/* Controls */}
            <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-white/5 bg-black/10 flex-wrap">
                <div className="relative flex-1 min-w-[11rem] max-w-xs">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                    <input type="text" placeholder="Search containers…" value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-base text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600" />
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                    {CATEGORIES.map(c => {
                        const on = categories.includes(c.slug);
                        return (
                            <button key={c.slug} type="button" onClick={() => toggleCategory(c.slug)}
                                className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${on ? 'bg-amber-500/20 border-amber-500/40 text-amber-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                                {c.label}
                            </button>
                        );
                    })}
                </div>
                {/* min profit % */}
                <div className="flex items-center gap-1.5 shrink-0">
                    <span className="text-xs text-slate-500">min profit</span>
                    <input type="number" step="1" value={minProfit} placeholder="0"
                        onChange={(e) => setMinProfit(e.target.value)}
                        className="w-16 bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white tabular-nums focus:outline-none focus:border-amber-500/40" />
                    <span className="text-xs text-slate-500">%</span>
                </div>
                <button type="button" onClick={() => setProfitableOnly(v => !v)}
                    className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${profitableOnly ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                    Profitable only
                </button>
                <button type="button" onClick={() => setHotOnly(v => !v)}
                    className={`shrink-0 flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${hotOnly ? 'bg-orange-500/20 border-orange-500/40 text-orange-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                    <Flame size={12} /> Hot only
                </button>

                {/* layout toggle */}
                <div className="flex items-center gap-1 ml-auto p-0.5 rounded-lg bg-black/30 border border-white/10 shrink-0">
                    <button type="button" onClick={() => setLayout('chips')}
                        title="Prices as wrapping chips"
                        className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${layout === 'chips' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                        <Layers size={13} /> Chips
                    </button>
                    <button type="button" onClick={() => setLayout('expand')}
                        title="Compact rows, click to expand prices"
                        className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${layout === 'expand' ? 'bg-amber-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                        <Rows3 size={13} /> Expandable
                    </button>
                </div>
                <button type="button" onClick={() => setShowAlerts(v => !v)}
                    title="Configure price alerts (Telegram / webhook)"
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors shrink-0 ${showAlerts ? 'bg-amber-500/20 border-amber-500/40 text-amber-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                    <Bell size={12} /> Alerts
                </button>
                <button type="button" onClick={() => fetchCases(categories)} disabled={loading}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600/70 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50 transition-colors shrink-0">
                    <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
                </button>
            </div>

            {showAlerts && <CaseAlertsPanel />}

            <div className="shrink-0 flex items-center gap-3 px-4 py-1.5 text-xs text-slate-500 border-b border-white/5 bg-black/5">
                <span>showing {Math.min(visibleCount, filtered.length)} of {filtered.length}{data ? ` · ${data.priced} priced` : ''}</span>
                {data?.hot_threshold_pct != null && <span className="text-slate-600">· hot ≥ {data.hot_threshold_pct}% profit</span>}
                <span className="text-slate-600">· profit is net of seller fee; flips sell on Steam/Buff/CSFloat (DMarket excluded)</span>
            </div>

            {(warming || noToken || error) && (
                <div className="shrink-0 px-4 py-2 text-xs border-b border-white/5 flex items-center gap-2">
                    {error ? (
                        <span className="text-red-400 flex items-center gap-1"><AlertTriangle size={12} /> {error}</span>
                    ) : noToken ? (
                        <span className="text-amber-400 flex items-center gap-1"><AlertTriangle size={12} /> Set tradeon_token in Settings to load prices.</span>
                    ) : (
                        <span className="text-amber-400 flex items-center gap-1"><RefreshCw size={12} className="animate-spin" /> Warming prices from pulse — refreshes automatically…</span>
                    )}
                </div>
            )}

            {/* Header */}
            {layout === 'chips' ? (
                <div className={`${GRID_CHIPS} shrink-0 px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20`}>
                    <SortHead label="Container" k="name" sortKey={sortKey} dir={sortDir} onSort={setSort} />
                    <span>Prices (✓ cheapest)</span>
                    <SortHead label="Liq" k="liquidity" sortKey={sortKey} dir={sortDir} onSort={setSort} className="justify-end" />
                    <SortHead label="Best flip (net of fee)" k="flip" sortKey={sortKey} dir={sortDir} onSort={setSort} />
                    <span className="text-right">Trend</span>
                </div>
            ) : (
                <div className={`${GRID_EXPAND} shrink-0 px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20`}>
                    <SortHead label="Container" k="name" sortKey={sortKey} dir={sortDir} onSort={setSort} />
                    <SortHead label="Buy @" k="cheapest" sortKey={sortKey} dir={sortDir} onSort={setSort} className="justify-end" />
                    <SortHead label="Flip (net of fee)" k="flip" sortKey={sortKey} dir={sortDir} onSort={setSort} className="justify-end" />
                    <SortHead label="Liq" k="liquidity" sortKey={sortKey} dir={sortDir} onSort={setSort} className="justify-end" />
                    <span className="text-right">Trend</span>
                </div>
            )}

            {/* Rows */}
            <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
                {visible.map((r, idx) => (layout === 'chips' ? renderChipsRow(r, idx) : renderExpandRow(r, idx)))}

                {visible.length === 0 && !loading && (
                    <div className="py-16 text-center text-slate-600 text-sm">
                        {data ? 'No containers match — loosen the filters or search.' : 'Loading containers…'}
                    </div>
                )}

                {visibleCount < filtered.length && (
                    <div className="flex justify-center py-4">
                        <button type="button" onClick={() => setVisibleCount(c => c + PAGE_SIZE)}
                            className="px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-300 hover:text-white hover:border-white/20 transition-colors">
                            Load {Math.min(PAGE_SIZE, filtered.length - visibleCount)} more
                            <span className="text-slate-500 ml-2">({filtered.length - visibleCount} left)</span>
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default CaseArbitrage;
