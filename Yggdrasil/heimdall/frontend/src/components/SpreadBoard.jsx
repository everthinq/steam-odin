import React, { useMemo, useState } from 'react';
import { RefreshCw, Search, TrendingUp, TrendingDown, ArrowRight, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';

// Cross-market arbitrage board: buy on one market (cheap), exit on another
// (fee-aware). Aggregates all accounts; inter-account moves already excluded by
// the backend. Answers two things per held item: is the arb still open in the
// market right now (live spread), and what's my exit profit vs what I paid.
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
const pctStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;

const PAGE_SIZE = 15;

const Tile = ({ label, value, sub, valueClass = 'text-slate-100' }) => (
    <div className="bg-odin-blue/40 border border-white/5 rounded-xl px-4 py-3">
        <p className="text-[10px] font-bold tracking-widest uppercase text-slate-500">{label}</p>
        <p className={`mt-1 text-xl font-bold tabular-nums ${valueClass}`}>{value}</p>
        {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
);

const SpreadBoard = ({ data, loading, pricing, marketLabel }) => {
    const [search, setSearch] = useState('');
    const [visible, setVisible] = useState(PAGE_SIZE);
    const [copiedName, setCopiedName] = useState('');

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    // Initial sort: Live spread, biggest first (is the arb most open here?).
    const [sort, setSort] = useState({ key: 'live_spread_pct', dir: 'desc' });

    const rows = data?.rows || [];
    const filtered = useMemo(
        () => rows.filter(r => matchesSearchQuery([r.item_name], search)),
        [rows, search]
    );
    const sorted = useMemo(() => {
        const { key, dir } = sort;
        const mult = dir === 'asc' ? 1 : -1;
        return [...filtered].sort((a, b) => {
            const av = a[key], bv = b[key];
            // Missing values (unpriced) always sink to the bottom, either direction.
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === 'string') return mult * av.localeCompare(bv);
            return mult * (av - bv);
        });
    }, [filtered, sort]);

    const toggleSort = (key, numeric) => setSort(s =>
        s.key === key
            ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
            : { key, dir: numeric ? 'desc' : 'asc' }   // numbers default high→low, text A→Z
    );

    if (loading && !data) {
        return <div className="flex justify-center items-center h-64"><RefreshCw className="animate-spin text-yellow-500" size={36} /></div>;
    }
    if (!data) return null;

    const buyLbl = marketLabel(data.buy_market);
    const sellLbl = marketLabel(data.sell_market);
    const up = (data.unrealized_spread ?? 0) >= 0;

    return (
        <div className="flex flex-col gap-5">
            {/* Headline */}
            <div className="bg-odin-blue/40 border border-white/5 rounded-2xl p-5">
                <div className="flex items-center gap-2 text-sm text-slate-300 mb-3">
                    <span className="font-semibold text-sky-300">{buyLbl}</span>
                    <ArrowRight size={15} className="text-slate-500" />
                    <span className="font-semibold text-emerald-300">{sellLbl}</span>
                    <span className="text-slate-500">· net of {data.sell_fee_pct}% {sellLbl} fee</span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                    <Tile
                        label="Unrealized spread"
                        value={
                            <span className={plClass(data.unrealized_spread)}>
                                {plStr(data.unrealized_spread)}
                                {data.unrealized_spread_pct != null && (
                                    <span className="ml-1.5 text-sm font-semibold">({pctStr(data.unrealized_spread_pct)})</span>
                                )}
                            </span>
                        }
                        sub={`exit all on ${sellLbl} now`}
                    />
                    <Tile label="Capital deployed" value={money(data.capital_deployed)}
                          sub={`${data.holdings_count} holdings${data.unpriced_count ? ` · ${data.unpriced_count} unpriced` : ''}`} />
                    <Tile label={`Exit value (net)`} value={money(data.exit_value)} sub={`after ${sellLbl} fee`} />
                    <Tile label="Realized" value={<span className={plClass(data.realized_pl)}>{plStr(data.realized_pl)}</span>}
                          sub="already sold" />
                    <Tile
                        label="Live market spread"
                        value={
                            <span className="flex items-center gap-1">
                                {data.avg_spread_pct != null && (data.avg_spread_pct >= 0 ? <TrendingUp size={16} className="text-emerald-400" /> : <TrendingDown size={16} className="text-red-400" />)}
                                <span className={plClass(data.avg_spread_pct)}>{pctStr(data.avg_spread_pct)}</span>
                            </span>
                        }
                        sub="arb still open?" />
                </div>
                <p className="text-xs text-slate-500 mt-3">
                    {data.account_count} accounts
                    {data.arbitrage_excluded > 0 && ` · ${data.arbitrage_excluded} inter-account legs excluded`}
                </p>
                {pricing === 'refreshing' && (
                    <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-400"><RefreshCw size={12} className="animate-spin" /> Fetching live prices for both markets…</p>
                )}
                {pricing === 'no_token' && (
                    <p className="mt-2 text-xs text-amber-400/80">Live prices unavailable — set the Tradeon token to price the spread; showing your cost basis only.</p>
                )}
            </div>

            {/* Board */}
            <section>
                <div className="flex items-center gap-3 flex-wrap mb-2">
                    <h3 className="text-sm font-semibold text-slate-200">
                        Spread board <span className="text-xs font-normal text-slate-500">({filtered.length === rows.length ? rows.length : `${filtered.length} of ${rows.length}`})</span>
                    </h3>
                    <div className="relative ml-auto min-w-[200px] max-w-xs flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input
                            value={search} onChange={e => setSearch(e.target.value)} placeholder="Search items…"
                            className="w-full pl-9 pr-3 py-1.5 text-sm bg-odin-blue/60 border border-white/10 rounded-lg text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-yellow-500/50"
                        />
                    </div>
                </div>
                <div className="overflow-x-auto rounded-xl border border-white/5">
                    <table className="w-full text-sm min-w-[860px]">
                        <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                            <tr>
                                {[
                                    { key: 'item_name', label: 'Item', numeric: false, align: 'left' },
                                    { key: 'net_qty', label: 'Qty', numeric: true, align: 'right' },
                                    { key: 'avg_cost', label: 'Avg cost', numeric: true, align: 'right' },
                                    { key: 'buy_price', label: `${buyLbl} now`, numeric: true, align: 'right' },
                                    { key: 'sell_net', label: `${sellLbl} net`, numeric: true, align: 'right' },
                                    { key: 'live_spread_pct', label: 'Live spread', numeric: true, align: 'right', title: 'Live market gap between buy and sell — is the arb still open?' },
                                    { key: 'margin', label: 'Your margin', numeric: true, align: 'right', title: 'Your exit profit vs what you paid, for the whole holding' },
                                ].map(col => {
                                    const active = sort.key === col.key;
                                    const Arrow = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown;
                                    return (
                                        <th key={col.key} title={col.title} className={`font-semibold px-3 py-2 ${col.align === 'left' ? 'text-left' : 'text-right'}`}>
                                            <button
                                                onClick={() => toggleSort(col.key, col.numeric)}
                                                className={`inline-flex items-center gap-1 uppercase tracking-wider transition-colors hover:text-slate-200 ${active ? 'text-yellow-400' : ''} ${col.align === 'right' ? 'flex-row-reverse' : ''}`}
                                            >
                                                {col.label}
                                                <Arrow size={12} className={active ? '' : 'opacity-40'} />
                                            </button>
                                        </th>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-white/5">
                            {sorted.slice(0, visible).map(r => (
                                <tr key={r.item_name} className="hover:bg-white/[0.02]">
                                    <td className="px-3 py-2 text-slate-200">
                                        <div className="flex items-center gap-2">
                                            <ItemIcon name={r.item_name} />
                                            <span onClick={() => copyItemName(r.item_name)} title={`${r.item_name}\n(click to copy)`} className="cursor-pointer transition-colors hover:text-white">{r.item_name}</span>
                                            {copiedName === r.item_name && <span className="shrink-0 text-xs font-medium text-emerald-400">Copied!</span>}
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-300">{r.net_qty}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(r.avg_cost)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-sky-300/90">{money(r.buy_price)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-emerald-300/90">{money(r.sell_net)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${plClass(r.live_spread_pct)}`}>{pctStr(r.live_spread_pct)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${plClass(r.margin)}`}>
                                        {plStr(r.margin)}
                                        {r.margin_pct != null && <span className="block text-[11px] opacity-70">{pctStr(r.margin_pct)}</span>}
                                    </td>
                                </tr>
                            ))}
                            {filtered.length === 0 && (
                                <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-600">{rows.length ? 'No items match your search.' : 'No holdings to arbitrage yet.'}</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
                {visible < filtered.length && (
                    <button onClick={() => setVisible(v => v + PAGE_SIZE)} className="mt-2 w-full py-2 text-xs text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 rounded-lg transition-colors">
                        Show more ({filtered.length - visible} more)
                    </button>
                )}
            </section>
        </div>
    );
};

export default SpreadBoard;
