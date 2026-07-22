import React, { useMemo, useState } from 'react';
import { RefreshCw, Search, TrendingUp, TrendingDown, ChevronDown } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';

// Read-only ledger across ALL accounts (arbitrage legs already excluded by the
// backend). The point is the headline: overall, is the trading profitable? Then
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

const PAGE_SIZE = 10;

const SectionHead = ({ title, open, onToggle, count, total, search, setSearch, placeholder }) => (
    <div className="flex items-center gap-3 flex-wrap mb-2">
        <button onClick={onToggle} className="flex items-center gap-1.5 text-sm font-semibold text-slate-200 hover:text-white transition-colors">
            <ChevronDown size={16} className={`transition-transform ${open ? '' : '-rotate-90'}`} />
            {title}
            <span className="text-xs font-normal text-slate-500">({count === total ? count : `${count} of ${total}`})</span>
        </button>
        {open && (
            <div className="relative ml-auto min-w-[200px] max-w-xs flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                <input
                    value={search} onChange={e => setSearch(e.target.value)} placeholder={placeholder}
                    className="w-full pl-9 pr-3 py-1.5 text-sm bg-odin-blue/60 border border-white/10 rounded-lg text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-yellow-500/50"
                />
            </div>
        )}
    </div>
);

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
    const [copiedName, setCopiedName] = useState('');

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    const holdings = data?.holdings || [];
    const txns = data?.transactions || [];

    const filteredHoldings = useMemo(
        () => holdings.filter(h => matchesSearchQuery([h.item_name], holdingsSearch)),
        [holdings, holdingsSearch]
    );
    const filteredTxns = useMemo(
        () => txns.filter(t => matchesSearchQuery([t.item_name, t.account, t.platform, t.note, t.type], txnsSearch)),
        [txns, txnsSearch]
    );

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
                            {data.account_count} accounts · {data.holdings_count} holdings · {data.txn_count} real transactions
                            {data.arbitrage_excluded > 0 && ` · ${data.arbitrage_excluded} arbitrage legs excluded`}
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
                />
                {holdingsOpen && (
                    <>
                        <div className="overflow-x-auto rounded-xl border border-white/5">
                            <table className="w-full text-sm min-w-[720px]">
                                <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                    <tr>
                                        <th className="text-left font-semibold px-3 py-2">Item</th>
                                        <th className="text-right font-semibold px-3 py-2">Net qty</th>
                                        <th className="text-right font-semibold px-3 py-2">Avg cost</th>
                                        <th className="text-right font-semibold px-3 py-2">Price</th>
                                        <th className="text-right font-semibold px-3 py-2">Value</th>
                                        <th className="text-right font-semibold px-3 py-2">Unrealized</th>
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
                />
                {txnsOpen && (
                    <>
                        <div className="overflow-x-auto rounded-xl border border-white/5">
                            <table className="w-full text-sm min-w-[820px]">
                                <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                    <tr>
                                        <th className="text-left font-semibold px-3 py-2">Item</th>
                                        <th className="text-left font-semibold px-3 py-2">Account</th>
                                        <th className="text-left font-semibold px-3 py-2">Type</th>
                                        <th className="text-right font-semibold px-3 py-2">Qty</th>
                                        <th className="text-right font-semibold px-3 py-2">Unit $</th>
                                        <th className="text-right font-semibold px-3 py-2">Total</th>
                                        <th className="text-left font-semibold px-3 py-2">Platform</th>
                                        <th className="text-left font-semibold px-3 py-2">Date</th>
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
