import React, { useMemo, useState } from 'react';
import { RefreshCw, TrendingUp, TrendingDown, ArrowLeftRight } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';
import SectionHead from './draupnir/SectionHead';
import SortTh from './draupnir/SortTh';
import { useColumnSort, sortRows } from './draupnir/columnSort';

// How each sortable column reads its value from a row (see columnSort.js).
const HOLDINGS_ACCESSORS = {
    item_name: h => h.item_name,
    net_qty: h => h.net_qty,
    avg_cost: h => h.avg_cost,
    current_price: h => h.current_price,
    market_value: h => h.market_value,
    unrealized_pl: h => h.unrealized_pl,
};
const TXN_ACCESSORS = {
    item_name: t => t.item_name,
    account: t => t.account,
    type: t => t.type,
    qty: t => t.qty,
    price: t => t.price,
    total: t => t.qty * t.price,
    platform: t => t.platform,
    date: t => t.date,
};
const COLLECTION_HINT = 'Scan your inventory in Huginn (Get all items) to filter by collection';

// Read-only ledger across ALL accounts (arbitrage excluded from P/L — tracked on
// its own tab). The point is the headline: overall, is the trading profitable? Then
// supporting Holdings + Transactions tables, the latter tagged with the account
// each leg happened on.
const ITEM_IMG_BASE = 'https://api.steamapis.com/image/item/730/';
const ItemIcon = ({ name }) => (
    <img
        src={`${ITEM_IMG_BASE}${encodeURIComponent(name)}`}
        alt="" loading="lazy" width={32} height={32}
        onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
        className="w-8 h-8 object-contain shrink-0 drop-shadow"
    />
);
const money = (v) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plClass = (v) => v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
const plStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${money(v)}`;

const PAGE_SIZE = 100;

const LoadMore = ({ visible, total, onMore }) => visible < total && (
    <button onClick={onMore} className="mt-2 w-full py-2 text-xs text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 rounded-lg transition-colors">
        Show more ({total - visible} more)
    </button>
);

const CombinedLedger = ({ data, loading, pricing }) => {
    const [holdingsOpen, setHoldingsOpen] = useState(false);
    const [txnsOpen, setTxnsOpen] = useState(true);
    const [holdingsSearch, setHoldingsSearch] = useState('');
    const [txnsSearch, setTxnsSearch] = useState('');
    const [holdingsVisible, setHoldingsVisible] = useState(PAGE_SIZE);
    const [txnsVisible, setTxnsVisible] = useState(PAGE_SIZE);
    const [holdingsCols, setHoldingsCols] = useState([]);   // selected collection filter (holdings)
    const [txnsCols, setTxnsCols] = useState([]);           // selected collection filter (transactions)
    const holdingsSort = useColumnSort();
    const txnsSort = useColumnSort();
    const [copiedName, setCopiedName] = useState('');

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    const holdings = useMemo(() => data?.holdings || [], [data]);
    const txns = useMemo(() => data?.transactions || [], [data]);

    // Collections available to filter on — from each row's backend collection
    // (looked up against Huginn's inventory scan). Empty until a scan exists.
    const availableCollections = useMemo(() => {
        const set = new Set();
        for (const h of holdings) if (h.collection) set.add(h.collection);
        for (const t of txns) if (t.collection) set.add(t.collection);
        return [...set].sort((a, b) => a.localeCompare(b));
    }, [holdings, txns]);

    const filteredHoldings = useMemo(() => {
        const cols = holdingsCols.length ? new Set(holdingsCols) : null;
        const rows = holdings.filter(h =>
            matchesSearchQuery([h.item_name], holdingsSearch) &&
            (!cols || cols.has(h.collection))
        );
        return sortRows(rows, holdingsSort.sortKey, holdingsSort.sortDir, HOLDINGS_ACCESSORS);
    }, [holdings, holdingsSearch, holdingsCols, holdingsSort.sortKey, holdingsSort.sortDir]);
    const filteredTxns = useMemo(() => {
        const cols = txnsCols.length ? new Set(txnsCols) : null;
        const rows = txns.filter(t =>
            matchesSearchQuery([t.item_name, t.account, t.platform, t.note, t.type], txnsSearch) &&
            (!cols || cols.has(t.collection))
        );
        return sortRows(rows, txnsSort.sortKey, txnsSort.sortDir, TXN_ACCESSORS);
    }, [txns, txnsSearch, txnsCols, txnsSort.sortKey, txnsSort.sortDir]);

    if (loading && !data) {
        return <div className="flex justify-center items-center h-64"><RefreshCw className="animate-spin text-yellow-500" size={36} /></div>;
    }
    if (!data) return null;

    const up = (data.total_pl ?? 0) >= 0;
    const pct = (data.invested && data.total_pl != null) ? (data.total_pl / data.invested) * 100 : null;

    return (
        <div className="flex flex-col gap-5">
            {/* Overall headline */}
            <div className="bg-odin-blue/40 border border-white/5 rounded-2xl p-5">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                        <p className="text-[10px] font-bold tracking-widest uppercase text-slate-500">Overall profit — all accounts</p>
                        <div className="flex items-center gap-2 mt-1">
                            {up ? <TrendingUp className="text-emerald-400" size={26} /> : <TrendingDown className="text-red-400" size={26} />}
                            <span className={`text-3xl md:text-4xl font-bold tabular-nums ${plClass(data.total_pl)}`}>{plStr(data.total_pl)}</span>
                            {pct != null && <span className={`text-lg font-semibold tabular-nums ${plClass(data.total_pl)}`}>({up ? '+' : ''}{pct.toFixed(1)}%)</span>}
                        </div>
                        <p className="text-xs text-slate-500 mt-1">
                            {data.account_count} accounts · {data.holdings_count} holdings · {data.txn_count} transactions
                            {data.arbitrage_count > 0 && ` · ${data.arbitrage_count} arbitrage legs excluded (see Arbitrage tab)`}
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                        <span className="text-slate-500">Invested</span>
                        <span className="text-right tabular-nums text-slate-300">{money(data.invested)}</span>
                        <span className="text-slate-500">Current value</span>
                        <span className="text-right tabular-nums text-slate-100">{money(data.current_value)}</span>
                        <span className="text-slate-500">Realized</span>
                        <span className={`text-right tabular-nums ${plClass(data.realized_pl)}`}>{plStr(data.realized_pl)}</span>
                        <span className="text-slate-500">Unrealized</span>
                        <span className={`text-right tabular-nums ${plClass(data.unrealized_pl)}`}>{plStr(data.unrealized_pl)}</span>
                    </div>
                </div>
                {pricing === 'refreshing' && (
                    <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-400"><RefreshCw size={12} className="animate-spin" /> Fetching live prices…</p>
                )}
                {pricing === 'no_token' && (
                    <p className="mt-3 text-xs text-amber-400/80">Live prices unavailable — set the Tradeon token to value holdings; showing cost basis only.</p>
                )}
            </div>

            {/* Holdings (aggregated across accounts) */}
            <section>
                <SectionHead
                    title="Holdings" open={holdingsOpen} onToggle={() => setHoldingsOpen(o => !o)}
                    count={filteredHoldings.length} total={holdings.length}
                    search={holdingsSearch} setSearch={setHoldingsSearch} placeholder="Search holdings…"
                    collections={availableCollections}
                    selectedCollections={holdingsCols} onCollectionsChange={setHoldingsCols}
                    collectionHint={COLLECTION_HINT}
                />
                {holdingsOpen && (
                    <>
                        <div className="overflow-x-auto rounded-xl border border-white/5">
                            <table className="w-full text-sm min-w-[720px]">
                                <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                    <tr>
                                        <SortTh col="item_name" label="Item" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                        <SortTh col="net_qty" label="Net qty" align="right" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                        <SortTh col="avg_cost" label="Avg cost" align="right" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                        <SortTh col="current_price" label="Price" align="right" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                        <SortTh col="market_value" label="Value" align="right" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                        <SortTh col="unrealized_pl" label="Unrealized" align="right" sortKey={holdingsSort.sortKey} sortDir={holdingsSort.sortDir} onSort={holdingsSort.toggle} />
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-white/5">
                                    {filteredHoldings.slice(0, holdingsVisible).map(h => (
                                        <tr key={h.item_name} className="hover:bg-white/[0.02]">
                                            <td className="px-3 py-2 text-slate-200">
                                                <div className="flex items-center gap-2">
                                                    <ItemIcon name={h.item_name} />
                                                    <span onClick={() => copyItemName(h.item_name)} title={`${h.item_name}\n(click to copy)`} className="cursor-pointer transition-colors hover:text-white">{h.item_name}</span>
                                                    {copiedName === h.item_name && <span className="shrink-0 text-xs font-medium text-emerald-400">Copied!</span>}
                                                </div>
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-300">{h.net_qty}</td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(h.avg_cost)}</td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-300">{money(h.current_price)}</td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-100">{money(h.market_value)}</td>
                                            <td className={`px-3 py-2 text-right tabular-nums ${plClass(h.unrealized_pl)}`}>{plStr(h.unrealized_pl)}</td>
                                        </tr>
                                    ))}
                                    {filteredHoldings.length === 0 && (
                                        <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-600">{holdings.length ? 'No holdings match your search.' : 'No holdings yet.'}</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                        <LoadMore visible={holdingsVisible} total={filteredHoldings.length} onMore={() => setHoldingsVisible(c => c + PAGE_SIZE)} />
                    </>
                )}
            </section>

            {/* Transactions (with account column) */}
            <section>
                <SectionHead
                    title="Transactions" open={txnsOpen} onToggle={() => setTxnsOpen(o => !o)}
                    count={filteredTxns.length} total={txns.length}
                    search={txnsSearch} setSearch={setTxnsSearch} placeholder="Search item / account / platform…"
                    collections={availableCollections}
                    selectedCollections={txnsCols} onCollectionsChange={setTxnsCols}
                    collectionHint={COLLECTION_HINT}
                />
                {txnsOpen && (
                    <>
                        <div className="overflow-x-auto rounded-xl border border-white/5">
                            <table className="w-full text-sm min-w-[820px]">
                                <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                    <tr>
                                        <SortTh col="item_name" label="Item" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="account" label="Account" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="type" label="Type" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="qty" label="Qty" align="right" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="price" label="Unit $" align="right" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="total" label="Total" align="right" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="platform" label="Platform" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                        <SortTh col="date" label="Date" sortKey={txnsSort.sortKey} sortDir={txnsSort.sortDir} onSort={txnsSort.toggle} />
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-white/5">
                                    {filteredTxns.slice(0, txnsVisible).map(t => (
                                        <tr key={t.id} className="hover:bg-white/[0.02]">
                                            <td className="px-3 py-2 text-slate-200">
                                                <div className="flex items-center gap-2">
                                                    <ItemIcon name={t.item_name} />
                                                    <span>
                                                        <span onClick={() => copyItemName(t.item_name)} title={`${t.item_name}\n(click to copy)`} className="cursor-pointer transition-colors hover:text-white">{t.item_name}</span>
                                                        {t.is_arbitrage && (
                                                            <span className="ml-2 inline-flex items-center gap-1 align-middle text-[10px] font-medium text-sky-300 bg-sky-500/15 px-1.5 py-0.5 rounded" title="Arbitrage deal — also counted in the Arbitrage tab">
                                                                <ArrowLeftRight size={10} /> arb
                                                            </span>
                                                        )}
                                                        {copiedName === t.item_name && <span className="ml-2 text-xs font-medium text-emerald-400">Copied!</span>}
                                                        {t.note && <span className="block text-[11px] text-slate-600 truncate max-w-[220px]">{t.note}</span>}
                                                    </span>
                                                </div>
                                            </td>
                                            <td className="px-3 py-2 text-slate-400 truncate max-w-[160px]" title={t.account}>{t.account}</td>
                                            <td className="px-3 py-2">
                                                <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${t.type === 'sell' ? 'bg-orange-500/15 text-orange-300' : 'bg-emerald-500/15 text-emerald-300'}`}>{t.type}</span>
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-300">{t.qty}</td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(t.price)}</td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-200">{money(t.qty * t.price)}</td>
                                            <td className="px-3 py-2 text-slate-400 truncate max-w-[160px]" title={t.platform}>{t.platform}</td>
                                            <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{t.date}</td>
                                        </tr>
                                    ))}
                                    {filteredTxns.length === 0 && (
                                        <tr><td colSpan={8} className="px-3 py-8 text-center text-slate-600">{txns.length ? 'No transactions match your search.' : 'No transactions yet.'}</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                        <LoadMore visible={txnsVisible} total={filteredTxns.length} onMore={() => setTxnsVisible(c => c + PAGE_SIZE)} />
                    </>
                )}
            </section>
        </div>
    );
};

export default CombinedLedger;
