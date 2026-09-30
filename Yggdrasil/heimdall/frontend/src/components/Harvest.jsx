import { useState, useEffect, useCallback, useMemo, useDeferredValue, useRef } from 'react';
import { RefreshCw, Search, Clock, Filter, ArrowUp, ArrowDown } from 'lucide-react';
import InfoTip from './gjallarhorn/InfoTip';
import ProfilePicker from './arbitrage/ProfilePicker';
import PriceCell from './arbitrage/PriceCell';
import CsfloatBuyOrdersPanel from './arbitrage/CsfloatBuyOrdersPanel';
import SteamMarketLink from './SteamMarketLink';
import BuffMarketLink from './BuffMarketLink';
import LisSkinsMarketLink from './LisSkinsMarketLink';
import CSFloatMarketLink from './CSFloatMarketLink';
import { useColumnSort, sortRows } from './draupnir/columnSort';

// Harvest tab (Huginn Arbitrage page): what you can sell RIGHT NOW for more than
// you paid. Laid out like the Arbitrage profiles: a profile is "account (portfolio)
// → market (autobuy)", e.g. everthinklol → CSMoney (Trade). Every purchase lot
// still held is its own row at the price you actually paid (no averaging), against
// what that market pays instantly. Backend: GET /api/huginn/harvest (non-blocking;
// warms the market's prices in the background, so we poll while warming) and
// GET /api/huginn/harvest/options (accounts + autobuy markets).

const PAGE_SIZE = 150;
const GRID = 'grid grid-cols-[minmax(0,2.5fr)_56px_80px_88px_80px_80px_88px_minmax(0,1.2fr)] gap-2';

const money = (v) => `${v < 0 ? '−' : ''}$${Math.abs(Number(v || 0)).toFixed(2)}`;
const tone = (v) => (v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400');
const ago = (days) => {
    if (days == null) return '';
    if (days === 0) return 'today';
    return days < 365 ? `${days} d ago` : `${(days / 365).toFixed(1)} y ago`;
};

// Remembered per browser: the last profile and filters.
const STORE_KEY = 'huginn.harvest';
const loadPrefs = () => {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
};
const savePrefs = (prefs) => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(prefs)); } catch { /* private window: not remembered */ }
};

const COLUMNS = [
    { key: 'item', label: 'Item', get: (r) => r.item_name },
    { key: 'qty', label: 'Qty', get: (r) => r.qty, right: true },
    { key: 'paid', label: 'Paid', get: (r) => r.paid, right: true },
    { key: 'sell', label: 'Sell', get: (r) => r.best.net, right: true },
    { key: 'profit', label: 'Profit', get: (r) => r.profit, right: true },
    { key: 'profit_pct', label: 'Profit %', get: (r) => (r.profit_pct === null ? Infinity : r.profit_pct), right: true },
    { key: 'total', label: 'Total', get: (r) => r.total_profit, right: true },
    { key: 'bought', label: 'Bought on', get: (r) => r.platform },
];
const ACCESSORS = Object.fromEntries(COLUMNS.map((c) => [c.key, c.get]));

// Human-readable reason for a { status: 'error' } board (or a failed request):
// the backend's message when it sent one, otherwise the market that failed.
const harvestErrorText = (d) => {
    if (!d) return 'Harvest request failed.';
    if (d.error) return String(d.error);
    const failed = d.failed_markets || d.failed_market || d.market;
    if (Array.isArray(failed) && failed.length) return `Pricing failed for ${failed.join(', ')}.`;
    if (failed) return `Pricing failed for ${failed}.`;
    return 'Pricing this market failed.';
};

const Badge = ({ className, title, children }) => (
    <span title={title} className={`px-1 rounded border text-[10px] ${className}`}>{children}</span>
);

const Harvest = () => {
    const [prefs] = useState(loadPrefs);
    const [options, setOptions] = useState(null);
    const [account, setAccount] = useState(prefs.account || 'all');
    const [market, setMarket] = useState(prefs.market || 'CsMoneyTrade');
    const [profitableOnly, setProfitableOnly] = useState(prefs.profitableOnly ?? true);
    const [minPct, setMinPct] = useState('');
    const [query, setQuery] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
    const [copiedName, setCopiedName] = useState(null);
    const deferredQuery = useDeferredValue(query);
    const { sortKey, sortDir, toggle } = useColumnSort();

    useEffect(() => {
        fetch('/api/huginn/harvest/options')
            .then((r) => (r.ok ? r.json() : null))
            .then((o) => {
                if (!o) return;
                setOptions(o);
                // A remembered account/market that no longer exists falls back.
                setAccount((a) => (a === 'all' || o.accounts.some((x) => x.id === a) ? a : 'all'));
                setMarket((m) => (o.markets.some((x) => x.id === m) ? m : o.default_markets[0]));
            })
            .catch(() => {});
    }, []);

    useEffect(() => { savePrefs({ account, market, profitableOnly }); }, [account, market, profitableOnly]);

    // Profiles = every account (plus "All accounts") × every autobuy market,
    // grouped by account in the picker, like the Arbitrage buy → sell profiles.
    const profiles = useMemo(() => {
        if (!options) return [];
        const accounts = [{ id: 'all', name: 'All accounts', sub: 'every portfolio' },
            ...options.accounts.map((a) => ({ id: a.id, name: a.name, sub: 'portfolio' }))];
        return accounts.flatMap((a) => options.markets.map((m) => ({
            id: `${a.id}|${m.id}`, from: a.name, fromSub: a.sub, to: m.display, toSub: 'autobuy',
        })));
    }, [options]);

    // Each board request takes a number; only the newest one may write state, so
    // switching profile mid-request never shows the previous profile's rows.
    const requestRef = useRef(0);
    const fetchData = useCallback(() => {
        const requestNumber = ++requestRef.current;
        const isCurrent = () => requestNumber === requestRef.current;
        const params = new URLSearchParams({ account, markets: market });
        if (minPct !== '') params.set('min_pct', minPct);
        return fetch(`/api/huginn/harvest?${params.toString()}`)
            .then(async (r) => {
                const body = await r.json().catch(() => null);
                // A failed request is shown like a { status: 'error' } board.
                return r.ok ? body : { status: 'error', error: body?.error || `Harvest request failed (HTTP ${r.status})` };
            })
            .then((d) => { if (isCurrent()) { setData(d); setLoading(false); } })
            .catch((e) => { if (isCurrent()) { setData({ status: 'error', error: e.message }); setLoading(false); } });
    }, [account, market, minPct]);

    useEffect(() => { fetchData(); }, [fetchData]);

    const status = data?.status;
    // An 'error' status is terminal: polling stops (only 'warming' or
    // 'refreshing' re-polls) and the message replaces the summary.
    const warming = status === 'warming' || status === 'refreshing';
    const failed = status === 'error';
    useEffect(() => {
        if (!warming) return undefined;
        const id = setTimeout(fetchData, 3000);
        return () => clearTimeout(id);
    }, [warming, data, fetchData]);

    const allRows = useMemo(() => data?.rows || [], [data]);
    const rows = useMemo(() => {
        const q = deferredQuery.trim().toLowerCase();
        const shown = allRows.filter((r) => (!profitableOnly || r.total_profit > 0)
            && (!q || r.item_name.toLowerCase().includes(q) || r.platform.toLowerCase().includes(q)));
        return sortRows(shown, sortKey, sortDir, ACCESSORS);
    }, [allRows, profitableOnly, deferredQuery, sortKey, sortDir]);
    const visibleRows = rows.slice(0, visibleCount);

    const summary = data?.summary;
    const sellMarket = data?.markets?.[0];
    const showAccount = account === 'all';

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).then(() => {
            setCopiedName(name);
            setTimeout(() => setCopiedName((c) => (c === name ? null : c)), 1200);
        }).catch(() => {});
    };

    return (
        <>
            {/* Profile picker: account (portfolio) → market (autobuy) */}
            <div className="shrink-0 flex items-center gap-3">
                <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Profile</span>
                {profiles.length > 0 && (
                    <ProfilePicker
                        profiles={profiles}
                        value={`${account}|${market}`}
                        onChange={(id) => { const [a, m] = id.split('|'); setAccount(a); setMarket(m); setVisibleCount(PAGE_SIZE); }}
                        groupLabel={(from) => `Sell from ${from}`}
                        searchPlaceholder="Search accounts or markets…"
                    />
                )}
                <InfoTip tip="What you can sell right now for more than you paid. Every purchase lot you still hold (Draupnir, at the price you actually paid; sells use up the oldest buys first) against what the chosen market pays instantly (autobuy / buy orders), after its sell fee. Prices move fast: check the offer on the market before you sell." />
            </div>

            {/* CSFloat has no bulk buy-order feed: its autobuy prices come from a sweep
                of your items (inventory scan + Draupnir holdings), refreshed here. */}
            {market === 'CsFloat' && <CsfloatBuyOrdersPanel includeHoldings onSwept={fetchData} />}

            {/* Status bar, like the pulse data bar of the Arbitrage profiles */}
            <div className="shrink-0 bg-odin-blue/30 border border-white/5 rounded-xl px-4 py-3">
                <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-[11px] font-bold tracking-widest text-slate-500 uppercase">Draupnir ⇒ {sellMarket?.display || 'market'}</span>
                    {warming ? (
                        <span className="inline-flex items-center gap-1.5 text-xs text-amber-300/90">
                            <RefreshCw size={12} className="animate-spin" /> loading {sellMarket?.display || 'market'} prices…
                        </span>
                    ) : status === 'no_token' ? (
                        <span className="text-xs text-amber-400/80">No tradeon_token set: add it in Settings to price markets.</span>
                    ) : failed ? (
                        <span className="text-xs text-red-400">{harvestErrorText(data)}</span>
                    ) : summary && (
                        <span className="text-xs text-slate-400 tabular-nums">
                            <span className="text-amber-300">{summary.profitable} profitable lots</span>
                            {' · '}<span className="text-emerald-400 font-semibold">{money(summary.total_profit)}</span> if sold now
                            {' '}({money(summary.proceeds)} for what cost {money(summary.cost)})
                            {summary.to_check > 0 && <span className="text-red-300"> · {summary.to_check} to check</span>}
                            {' · '}{summary.unpriced} lots not bought there
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={() => { setLoading(true); fetchData(); }}
                        disabled={loading || warming}
                        className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50 transition-colors"
                    >
                        <RefreshCw size={12} className={(loading || warming) ? 'animate-spin' : ''} /> Refresh
                    </button>
                </div>
                <p className="mt-1.5 text-[11px] text-slate-500 flex items-center gap-x-3 gap-y-1 flex-wrap">
                    {sellMarket?.balance && (
                        <span><span className="text-sky-300/90">{sellMarket.display} pays {sellMarket.balance}</span>, not money: it only buys items there.</span>
                    )}
                    {sellMarket && !sellMarket.fee_known && <span>Its sell fee is not confirmed: prices are taken as-is.</span>}
                    <span className="inline-flex items-center gap-1"><Clock size={11} className="text-orange-400" /> bought under 8 days ago: may still be trade-protected</span>
                </p>
            </div>

            {/* Results */}
            <div className="flex-1 flex flex-col bg-odin-blue/30 border border-white/5 rounded-xl overflow-hidden min-h-0">
                <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/10 flex-wrap">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input
                            type="text"
                            placeholder="Search items or where bought"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-base text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                        />
                    </div>
                    <button
                        type="button"
                        onClick={() => setProfitableOnly((v) => !v)}
                        className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${profitableOnly ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}
                    >
                        Profitable only
                    </button>
                    <div className="shrink-0 inline-flex items-center gap-1 text-xs text-slate-500">
                        <Filter size={12} /> min %
                        <input
                            type="number"
                            value={minPct}
                            onChange={(e) => setMinPct(e.target.value)}
                            placeholder="any"
                            className="w-16 bg-black/30 border border-white/10 rounded px-1.5 py-1 text-slate-200 outline-none focus:border-amber-500/40"
                        />
                    </div>
                    <span className="text-sm text-slate-500 shrink-0">
                        showing {Math.min(visibleCount, rows.length)} of {rows.length}
                        {rows.length !== allRows.length && ` (${allRows.length} total)`}
                    </span>
                </div>
                <div className={`shrink-0 ${GRID} px-4 py-2 border-b border-white/5 text-[11px] font-bold tracking-wider text-slate-400 uppercase bg-black/20`}>
                    {COLUMNS.map((c) => (
                        <button
                            key={c.key}
                            type="button"
                            onClick={() => toggle(c.key)}
                            title={`Sort by ${c.label}`}
                            className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-slate-200 transition-colors ${c.right ? 'justify-end' : ''} ${sortKey === c.key ? 'text-amber-400' : ''}`}
                        >
                            {c.label}
                            {sortKey === c.key && (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                        </button>
                    ))}
                </div>
                <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
                    {visibleRows.map((r) => {
                        const name = r.item_name;
                        const best = r.best;
                        return (
                            <div
                                key={`${r.portfolio_id}-${name}-${r.paid}-${r.platform}`}
                                className={`${GRID} items-center px-4 py-2.5 border-b border-white/5 hover:bg-white/[0.03] transition-colors ${best.suspicious ? 'opacity-60' : ''}`}
                            >
                                <div className="flex items-center gap-2.5 min-w-0">
                                    {r.image && (
                                        <img src={r.image} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer"
                                            className="w-9 h-9 object-contain shrink-0 rounded bg-black/20" />
                                    )}
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-1.5 min-w-0">
                                            <p className="text-base text-white truncate cursor-pointer hover:text-amber-300 transition-colors"
                                                title={`${name}\n(click to copy)`} onClick={() => copyItemName(name)}>
                                                {name}
                                            </p>
                                            {copiedName === name && <span className="shrink-0 text-[10px] font-medium text-emerald-400">copied</span>}
                                            <SteamMarketLink itemName={name} />
                                            <BuffMarketLink itemName={name} />
                                            <LisSkinsMarketLink itemName={name} />
                                            <CSFloatMarketLink itemName={name} />
                                        </div>
                                        <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
                                            {r.buff_listing != null && <span>Buff163 listing {money(r.buff_listing)}</span>}
                                            {best.suspicious && (
                                                <Badge className="bg-red-500/10 border-red-500/30 text-red-300/90"
                                                    title="Far above Buff163's cheapest listing: most likely a buy order for one float or pattern, not for any copy. Left out of the totals.">
                                                    check
                                                </Badge>
                                            )}
                                            {best.count != null && r.qty > best.count && (
                                                <span className="text-orange-400" title={`You hold ${r.qty} here but pulse sees ${best.count} offers at this price: the rest may sell lower`}>
                                                    only {best.count} offers at this price
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>

                                <span className="text-base text-right font-bold text-amber-400 tabular-nums">{r.qty}</span>
                                <span className="text-base text-right text-slate-300 tabular-nums">{money(r.paid)}</span>
                                <PriceCell market={best.market} itemName={name} price={best.net}
                                    className="text-base text-right text-slate-300 tabular-nums" />
                                <span className={`text-base text-right tabular-nums ${tone(r.profit)}`}>{money(r.profit)}</span>
                                <span className={`text-base text-right font-semibold tabular-nums ${r.profit_pct === null ? 'text-emerald-300' : tone(r.profit_pct)}`}
                                    title={r.profit_pct === null ? 'Cost nothing (a drop or a gift): the whole sale is profit' : undefined}>
                                    {r.profit_pct === null ? 'free' : `${r.profit_pct.toFixed(0)}%`}
                                </span>
                                <span className={`text-base text-right font-semibold tabular-nums ${best.suspicious ? 'text-slate-500 line-through' : tone(r.total_profit)}`}
                                    title={best.suspicious ? 'Not counted: the offer needs checking' : undefined}>
                                    {money(r.total_profit)}
                                </span>

                                <div className="flex flex-wrap items-center gap-1 min-w-0">
                                    {showAccount && (
                                        <span className="inline-flex items-center rounded bg-white/5 border border-white/10 px-2 py-0.5 text-xs text-slate-300">
                                            <span className="truncate max-w-[9rem]">{r.account}</span>
                                        </span>
                                    )}
                                    <span className="text-xs text-slate-300 truncate" title={r.last_buy_date ? `Last bought ${r.last_buy_date}` : undefined}>
                                        {r.platform || <span className="text-slate-600 italic">not set</span>}
                                    </span>
                                    <span className={`text-[11px] whitespace-nowrap ${r.maybe_trade_held ? 'text-orange-400' : 'text-slate-600'}`}>
                                        {r.maybe_trade_held && <Clock size={10} className="inline mr-0.5" />}
                                        {ago(r.days_since_buy)}
                                    </span>
                                </div>
                            </div>
                        );
                    })}

                    {!rows.length && (
                        <div className="py-12 text-center text-slate-600 text-sm">
                            {warming ? 'Loading prices…'
                                : status === 'no_token' ? 'Set a tradeon_token to price markets.'
                                    : failed ? harvestErrorText(data)
                                    : profitableOnly && allRows.length ? 'Nothing sells above what you paid on this market right now. Try another profile, or turn off “Profitable only”.'
                                        : 'None of these holdings is bought by this market.'}
                        </div>
                    )}

                    {visibleCount < rows.length && (
                        <div className="flex justify-center py-4">
                            <button
                                type="button"
                                onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
                                className="px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-slate-300 hover:text-white hover:border-white/20 transition-colors"
                            >
                                Load {Math.min(PAGE_SIZE, rows.length - visibleCount)} more
                                <span className="text-slate-500 ml-2">({rows.length - visibleCount} left)</span>
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </>
    );
};

export default Harvest;
