import { Fragment, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Search, AlertTriangle, Loader2, ShoppingCart, ChevronRight, ChevronDown, TrendingUp, TrendingDown, Info } from 'lucide-react';
import { useColumnSort, sortRows } from '../draupnir/columnSort';
import SortHeader from './SortHeader';
import SteamMarketLink from '../SteamMarketLink';

// Store Catalogue → Arbitrage tab: which in-game store items resell for more than the store
// charges. A recommendation board only: nothing is bought or sold here; Buy opens the item's
// guarded buy page. Backend: GET /api/ratatoskr/store-arbitrage?markets=… (non-blocking: it
// warms missing markets in the background, so the page polls while warming) and
// GET /api/ratatoskr/store-arbitrage/options (the markets and the default choice).

const money = (v, digits = 2) => (v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toFixed(digits)}`);
const percent = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const tone = (v) => (v == null ? 'text-slate-500' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400');
const SIDE_LABEL = { instant: 'sell instantly', listing: 'list' };
const REASON_ORDER = ['cannot_trade', 'keys', 'passes', 'no_price'];

// Remembered per browser: the chosen markets and filters.
const PREFERENCES_KEY = 'ratatoskr.storeArbitrage';
const loadPreferences = () => {
    try { return JSON.parse(localStorage.getItem(PREFERENCES_KEY)) || {}; } catch { return {}; }
};
const savePreferences = (preferences) => {
    try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences)); } catch { /* private window: not remembered */ }
};

// The offer the row is judged by: the best of any payout, or the best paid out as money.
const judged = (row, cashOnly) => (cashOnly ? row.cash : (row.best && !row.best.suspicious ? row.best : null));
const profitPercent = (row, cashOnly) => (cashOnly ? row.cash_profit_pct : row.profit_pct);

const SORT_VALUES = {
    item: (r) => r.name,
    store: (r) => r.cost,
    instant: (r) => r.instant?.net,
    listing: (r) => r.listing?.net,
    history: (r) => (r.history.days ? r.history.profitable_days / r.history.days : null),
};

const Sparkline = ({ series, cost }) => {
    if (!series || series.length < 2) return null;
    const low = Math.min(...series, cost);
    const high = Math.max(...series, cost);
    const span = high - low || 1;
    const x = (index) => (index / (series.length - 1)) * 60;
    const y = (value) => 16 - ((value - low) / span) * 16;
    return (
        <svg width="60" height="16" className="inline-block align-middle ml-1" aria-hidden="true">
            <line x1="0" x2="60" y1={y(cost)} y2={y(cost)} stroke="currentColor" className="text-slate-600" strokeDasharray="2 2" />
            <polyline fill="none" strokeWidth="1.2" stroke="currentColor" className="text-amber-400"
                points={series.map((value, index) => `${x(index)},${y(value)}`).join(' ')} />
        </svg>
    );
};

const OfferCell = ({ offer }) => {
    if (!offer) return <span className="text-slate-600">—</span>;
    return (
        <span title={`${offer.display}: ${money(offer.gross, 3)} before its ${(offer.fee * 100).toFixed(1)}% fee${offer.balance ? `, paid as ${offer.balance}` : ''}${offer.suspicious ? '. Far above Buff163’s listing: probably not a price you can sell at' : ''}`}>
            <span className={`tabular-nums ${offer.suspicious ? 'text-slate-500 line-through' : 'text-slate-100'}`}>{money(offer.net, 2)}</span>
            <span className={`ml-1.5 tabular-nums text-xs ${offer.suspicious ? 'text-slate-500' : tone(offer.profit)}`}>{percent(offer.profit_pct)}</span>
            <span className="block text-[10px] text-slate-500">{offer.display}{offer.balance ? ` · ${offer.balance}` : ''}</span>
        </span>
    );
};

const Badge = ({ className, title, children }) => (
    <span title={title} className={`ml-1.5 px-1 rounded border text-[10px] whitespace-nowrap ${className}`}>{children}</span>
);

const StoreArbitrage = () => {
    const [preferences] = useState(loadPreferences);
    const [options, setOptions] = useState(null);
    const [markets, setMarkets] = useState(preferences.markets || null);
    const [cashOnly, setCashOnly] = useState(preferences.cashOnly ?? false);
    const [showLosses, setShowLosses] = useState(preferences.showLosses ?? false);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState('all');
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [answers, setAnswers] = useState(0);   // every finished request, so a failed one still re-arms polling
    const [open, setOpen] = useState(() => new Set());
    const [showExcluded, setShowExcluded] = useState(false);
    const sort = useColumnSort();
    const requests = useRef(0);            // only the newest answer is kept

    useEffect(() => {
        fetch('/api/ratatoskr/store-arbitrage/options')
            .then((r) => r.json())
            .then((o) => {
                setOptions(o);
                // Remembered markets that no longer exist are dropped; none left: the defaults.
                setMarkets((chosen) => {
                    const known = (chosen || []).filter((id) => o.markets.some((m) => m.id === id));
                    return known.length ? known : o.default_markets;
                });
            })
            .catch(() => setError('Could not reach the backend.'));
    }, []);

    useEffect(() => { if (markets) savePreferences({ markets, cashOnly, showLosses }); }, [markets, cashOnly, showLosses]);

    const load = useCallback(() => {
        if (!markets) return Promise.resolve();
        const number = ++requests.current;
        return fetch(`/api/ratatoskr/store-arbitrage?markets=${encodeURIComponent(markets.join(','))}`)
            .then(async (r) => {
                const body = await r.json().catch(() => null);
                if (number !== requests.current) return;
                if (!r.ok || !body || (body.error && !body.rows)) setError(body?.error || `The board could not load (HTTP ${r.status}).`);
                else { setData(body); setError(null); }
            })
            .catch(() => { if (number === requests.current) setError('Could not reach the backend.'); })
            .finally(() => { if (number === requests.current) setAnswers((count) => count + 1); });
    }, [markets]);

    useEffect(() => { load(); }, [load]);
    const warming = data?.status === 'warming' || data?.status === 'refreshing';
    // The next poll is armed after every answer, a failed one included: fast while warming or
    // after a failure, every 5 minutes otherwise.
    useEffect(() => {
        if (!answers) return undefined;
        const timer = setTimeout(load, warming ? 3000 : error ? 30000 : 5 * 60 * 1000);
        return () => clearTimeout(timer);
    }, [answers, warming, error, load]);

    const toggleMarket = (id) => setMarkets((chosen) => {
        const next = chosen.includes(id) ? chosen.filter((m) => m !== id) : [...chosen, id];
        return next.length ? next : chosen;          // at least one market
    });
    const toggleOpen = (entry) => setOpen((current) => {
        const next = new Set(current);
        if (next.has(entry)) next.delete(entry); else next.add(entry);
        return next;
    });

    const rows = useMemo(() => data?.rows || [], [data]);
    const categories = useMemo(() => [...new Set(rows.map((r) => r.category))], [rows]);
    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const matching = rows.filter((r) => (category === 'all' || r.category === category)
            && (!needle || r.name.toLowerCase().includes(needle))
            && (showLosses || (judged(r, cashOnly)?.net ?? 0) > r.cost));
        // Unconfirmed items (probably not resellable) below every confirmed one, then by profit.
        const byProfit = [...matching].sort((a, b) => (a.unconfirmed - b.unconfirmed)
            || (profitPercent(b, cashOnly) ?? -Infinity) - (profitPercent(a, cashOnly) ?? -Infinity));
        return sortRows(byProfit, sort.sortKey, sort.sortDir, { ...SORT_VALUES, profit: (r) => profitPercent(r, cashOnly) });
    }, [rows, search, category, showLosses, cashOnly, sort.sortKey, sort.sortDir]);
    const excludedByReason = useMemo(() => {
        const groups = {};
        for (const item of data?.excluded || []) (groups[item.reason] ||= { text: item.reason_text, names: [] }).names.push(item.name);
        return REASON_ORDER.filter((reason) => groups[reason]).map((reason) => ({ reason, ...groups[reason] }));
    }, [data]);

    if (!options || !markets) {
        return <div className="p-8 flex items-center justify-center text-slate-400"><Loader2 className="animate-spin mr-2" size={18} /> Loading markets…</div>;
    }
    const summary = data?.summary || {};
    const profitable = cashOnly ? summary.cash_profit : (summary.instant_profit || 0) + (summary.listing_profit || 0);

    return (
        <div>
            <div className="flex flex-wrap items-center gap-1.5 p-3 border-b border-white/10 text-xs">
                <span className="text-slate-500 mr-1">Sell on</span>
                {options.markets.map((m) => (
                    <button key={m.id} type="button" onClick={() => toggleMarket(m.id)} aria-pressed={markets.includes(m.id)}
                        title={`${m.display}: ${m.instant ? 'buy orders and listings' : 'listings only'}, fee ${(m.fee * 100).toFixed(1)}%${m.fee_known ? '' : ' (not confirmed: edit it in Huginn’s Fees editor)'}${m.balance ? `; pays in ${m.balance}` : ''}`}
                        className={`px-2 py-0.5 rounded border ${markets.includes(m.id) ? 'border-amber-500/40 bg-amber-600/20 text-amber-200' : 'border-white/10 text-slate-500 hover:text-white hover:bg-white/5'}`}>
                        {m.display}
                    </button>
                ))}
            </div>

            <div className="flex flex-wrap items-center gap-3 p-3 border-b border-white/10">
                <label className="relative flex-1 min-w-[12rem]">
                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                    <span className="sr-only">Search items</span>
                    <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search items"
                        className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-2 py-1.5 text-sm focus:border-amber-500/60 focus:outline-none" />
                </label>
                <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category"
                    className="bg-black/30 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-300">
                    <option value="all">Every category</option>
                    {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer"
                    title="Judge each item only by prices paid out as money: no Steam wallet, no site balance">
                    <input type="checkbox" className="accent-amber-500" checked={cashOnly} onChange={(e) => setCashOnly(e.target.checked)} />
                    Cash only
                </label>
                <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                    <input type="checkbox" className="accent-amber-500" checked={showLosses} onChange={(e) => setShowLosses(e.target.checked)} />
                    Show items that lose money
                </label>
                <span className="text-xs text-slate-500">
                    {warming ? <span className="inline-flex items-center gap-1 text-amber-300"><Loader2 size={12} className="animate-spin" /> Reading market prices…</span>
                        : data?.status === 'no_token' ? 'No Tradeon token (tradeon_token in backend/settings.json)'
                            : data ? `Prices ${data.status === 'fresh' ? 'fresh' : data.status}` : ''}
                </span>
            </div>

            {(error || data?.error) && (
                <div className="m-3 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-950/80 px-3 py-2 text-red-300 text-xs" role="alert">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" /> {error || data.error}
                </div>
            )}

            {data && (
                <div className="flex flex-wrap gap-x-6 gap-y-1 px-3 py-2 border-b border-white/10 text-xs text-slate-400">
                    <span><b className="text-emerald-300">{profitable || 0}</b> of {summary.items} items sell for more than the store price{cashOnly ? ', paid as money' : ''}</span>
                    {summary.unconfirmed > 0 && <span><b className="text-amber-300">{summary.unconfirmed}</b> unconfirmed</span>}
                    <button type="button" onClick={() => setShowExcluded((v) => !v)} className="hover:text-white underline decoration-dotted">
                        {summary.excluded} left out
                    </button>
                    <span title="One point per day: the best price each market showed. It grows every hour, also while this page is closed.">
                        History: {data.history_days} {data.history_days === 1 ? 'day' : 'days'}
                    </span>
                </div>
            )}

            {showExcluded && (
                <div className="px-3 py-2 border-b border-white/10 text-xs text-slate-400 space-y-1">
                    {excludedByReason.map((group) => (
                        <p key={group.reason}><span className="text-slate-300">{group.text[0].toUpperCase() + group.text.slice(1)}:</span> {group.names.join(', ')}</p>
                    ))}
                </div>
            )}

            <div className="overflow-auto custom-scrollbar max-h-[65vh]">
                <table className="w-full">
                    <thead className="sticky top-0 z-10 bg-slate-950 text-[11px] uppercase tracking-wider text-slate-500">
                        <tr>
                            <SortHeader column="item" label="Item" sort={sort} className="py-2 pl-3" resetLabel="the best profit first" />
                            <SortHeader column="store" label="Store" sort={sort} align="right" resetLabel="the best profit first"
                                title="The game store's US dollar price" />
                            <SortHeader column="instant" label="Sell instantly" sort={sort} className="pl-4" resetLabel="the best profit first"
                                title="The best buy order or autobuy, after the market's fee" />
                            <SortHeader column="listing" label="List at" sort={sort} className="pl-4" resetLabel="the best profit first"
                                title="The best lowest listing, after the market's fee: you list at that price and wait" />
                            <SortHeader column="profit" label="Profit" sort={sort} align="right" resetLabel="the best profit first"
                                title={cashOnly ? 'The best price paid out as money, against the store price' : 'The best price of any payout, against the store price'} />
                            <SortHeader column="history" label="30 days" sort={sort} className="pl-4" resetLabel="the best profit first"
                                title="Days the best price beat the store price, of the days tracked; the line is the best price per day, the dashes the store price" />
                            <th className="font-medium text-left pl-4">Your record</th>
                            <th className="pr-3"><span className="sr-only">Market and buy</span></th>
                        </tr>
                    </thead>
                    <tbody>
                        {data && visible.length === 0 && (
                            <tr><td colSpan={8} className="py-8 text-center text-slate-500">
                                {!data.catalogue_read_at ? 'No store prices yet: press “Read prices again” on the Catalogue tab.'
                                    : warming ? 'Reading market prices…'
                                        : data.status === 'no_token' ? 'No market prices: the Tradeon token (tradeon_token in backend/settings.json) is missing.'
                                            : !rows.length ? 'No market prices could be read.'
                                                : showLosses || search || category !== 'all' ? 'No item matches.'
                                                    : 'Nothing sells above the store price right now. Tick “Show items that lose money” to see every item.'}
                            </td></tr>
                        )}
                        {visible.map((r) => {
                            const expanded = open.has(r.entry);
                            const judgedOffer = judged(r, cashOnly);
                            return (
                                <Fragment key={r.entry}>
                                    <tr className="border-t border-white/5 hover:bg-white/[0.03] cursor-pointer align-top" onClick={() => toggleOpen(r.entry)}>
                                        <td className="py-1.5 pl-3">
                                            <span className="inline-flex items-start gap-1">
                                                {expanded ? <ChevronDown size={13} className="mt-0.5 text-slate-500 shrink-0" /> : <ChevronRight size={13} className="mt-0.5 text-slate-500 shrink-0" />}
                                                <span>
                                                    <span className="text-slate-100">{r.name}</span>
                                                    {r.unconfirmed && <Badge className="border-amber-500/40 text-amber-300"
                                                        title={`Markets ask at least ${data.unconfirmed_ratio}× the store price now, and did on most days tracked (or there is little history yet). If store copies could be resold, traders would have closed that gap, so they probably cannot be. Buy one and look at it in the web inventory: “Tradable After …” means a ${data.trade_hold_days}-day hold, “Not Tradable” means never.`}>unconfirmed</Badge>}
                                                    {r.resold_before && <Badge className="border-emerald-500/30 text-emerald-300" title="You sold this item before (Draupnir): store copies are resellable">resold before</Badge>}
                                                    {r.best?.rising && <TrendingUp size={12} className="inline ml-1.5 text-emerald-400" aria-label="price rising" />}
                                                    {r.best?.falling && <TrendingDown size={12} className="inline ml-1.5 text-red-400" aria-label="price falling" />}
                                                    <span className="block text-[10px] text-slate-500">{r.category}</span>
                                                </span>
                                            </span>
                                        </td>
                                        <td className="text-right tabular-nums">
                                            <span className="text-slate-100">{money(r.cost)}</span>
                                            {r.cheapest_wallet && r.cheapest_wallet.usd < r.cost - 0.005 && (
                                                <span className="block text-[10px] text-sky-300" title={`In ${r.cheapest_wallet.currency} the store charges about ${money(r.cheapest_wallet.usd, 3)} (today's exchange rate): ${r.cheapest_wallet.accounts.join(', ')}`}>
                                                    {money(r.cheapest_wallet.usd, 2)} in {r.cheapest_wallet.currency}
                                                </span>
                                            )}
                                        </td>
                                        <td className="pl-4"><OfferCell offer={r.instant} /></td>
                                        <td className="pl-4"><OfferCell offer={r.listing} /></td>
                                        <td className={`text-right tabular-nums font-medium ${tone(judgedOffer ? judgedOffer.net - r.cost : null)}`}>
                                            {judgedOffer ? money(judgedOffer.net - r.cost) : '—'}
                                            <span className="block text-[10px] font-normal">{percent(profitPercent(r, cashOnly))}</span>
                                        </td>
                                        <td className="pl-4 text-xs text-slate-400 whitespace-nowrap">
                                            {r.history.days ? `${r.history.profitable_days} of ${r.history.days}` : '—'}
                                            <Sparkline series={r.history.series} cost={r.cost} />
                                        </td>
                                        <td className="pl-4 text-xs text-slate-400">
                                            {r.ledger ? (
                                                <span title={Object.entries(r.ledger.sold_on).map(([platform, units]) => `${units} on ${platform}`).join(', ') || 'nothing sold yet'}>
                                                    bought {r.ledger.bought}, sold {r.ledger.sold}
                                                    {r.ledger.sold > 0 && <span className="block text-[10px]">avg {money(r.ledger.received / r.ledger.sold)} after fees</span>}
                                                </span>
                                            ) : '—'}
                                        </td>
                                        <td className="text-right pr-3 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                                            {r.market_url && <SteamMarketLink itemName={r.name} className="mr-2 align-middle" />}
                                            {r.definition_index != null && (
                                                <Link to={`/store-catalogue/buy/${encodeURIComponent(r.entry)}`} title={`Buy ${r.name} on any of your accounts (dry run first)`}
                                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-amber-500/30 text-amber-200 text-xs hover:bg-amber-500/10">
                                                    <ShoppingCart size={11} /> Buy
                                                </Link>
                                            )}
                                        </td>
                                    </tr>
                                    {expanded && (
                                        <tr className="bg-black/30">
                                            <td colSpan={8} className="px-8 py-2">
                                                <table className="text-xs w-full max-w-3xl">
                                                    <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                                                        <tr>
                                                            <th className="text-left font-medium py-1">Market</th>
                                                            <th className="text-left font-medium">How</th>
                                                            <th className="text-right font-medium">Price</th>
                                                            <th className="text-right font-medium">Fee</th>
                                                            <th className="text-right font-medium">You get</th>
                                                            <th className="text-right font-medium">Profit</th>
                                                            <th className="text-right font-medium" title="Listings or buy orders at that market">Depth</th>
                                                            <th className="text-left font-medium pl-4">Paid as</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {r.offers.map((o) => (
                                                            <tr key={`${o.market}-${o.side}`} className={`border-t border-white/5 ${o.suspicious ? 'text-slate-500' : 'text-slate-300'}`}>
                                                                <td className="py-1">{o.display}</td>
                                                                <td>{SIDE_LABEL[o.side]}</td>
                                                                <td className="text-right tabular-nums">{money(o.gross, 3)}</td>
                                                                <td className="text-right tabular-nums">{(o.fee * 100).toFixed(1)}%</td>
                                                                <td className="text-right tabular-nums">{money(o.net, 3)}</td>
                                                                <td className={`text-right tabular-nums ${o.suspicious ? '' : tone(o.profit)}`}>{money(o.profit, 3)} <span className="opacity-70">{percent(o.profit_pct)}</span></td>
                                                                <td className="text-right tabular-nums">{o.count ?? '—'}</td>
                                                                <td className="pl-4">
                                                                    {o.suspicious ? <span title="Far above Buff163's lowest listing: one pattern's buy order, or one seller asking what nobody pays">not a real price</span>
                                                                        : o.balance || 'money'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                                {r.history.best_net != null && (
                                                    <p className="mt-2 text-[11px] text-slate-500">Best price in the last 30 days: {money(r.history.best_net, 3)} after fees on {r.history.best_date}.</p>
                                                )}
                                            </td>
                                        </tr>
                                    )}
                                </Fragment>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            <div className="px-3 py-2 border-t border-white/10 text-[11px] text-slate-500 space-y-1">
                <p className="flex items-start gap-1.5"><Info size={12} className="mt-0.5 shrink-0" />
                    <span>
                        Store purchases cannot be traded for {data?.trade_hold_days ?? 7} days, so the price has to hold for a week: the 30-day column shows whether a gap is a spike or lasts.
                        Steam pays into the Steam wallet, which buys more store items, so a Steam profit is a wallet-to-wallet loop, not cash: tick “Cash only” for money.
                        Prices are after each market&apos;s fee (Huginn&apos;s Fees editor); a price far above Buff163&apos;s lowest listing is crossed out.
                    </span>
                </p>
            </div>
        </div>
    );
};

export default StoreArbitrage;
