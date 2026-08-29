import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { RefreshCw, Search, Gavel, Lock, Camera } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';
import { getTradeonShortLink } from '../utils/tradeonShortLink';

// LOOT.Farm live auctions, cross-referenced with your buy sources: for each auctioned
// item we take the cheapest current lot (what you'd need to win it for) and show the
// flip profit if you win and resell on Steam (net of the 13% fee). A background tracker
// logs every lot over time; the header shows the backtest read of that log.
const ITEM_IMG_BASE = 'https://api.steamapis.com/image/item/730/';
const ItemIcon = ({ name }) => (
    <img src={`${ITEM_IMG_BASE}${encodeURIComponent(name)}`} alt="" loading="lazy" width={30} height={30}
         onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
         className="w-[30px] h-[30px] object-contain shrink-0 drop-shadow" />
);
const money = (v) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plClass = (v) => v == null ? 'text-slate-500' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
const plStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${money(v)}`;
const pctStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(0)}%`;

// LOOT.Farm price tier (their own colors), by the `pg` % of Steam.
const tierColor = (pg) => pg == null ? null
    : pg >= 115 ? '#d19705' : pg >= 110 ? '#8c857c' : pg >= 95 ? '#825115' : pg >= 80 ? '#00b021' : null;

const PAGE_SIZE = 100;

const Tile = ({ label, value, sub, cls = 'text-slate-100' }) => (
    <div className="bg-black/20 border border-white/5 rounded-lg px-3 py-2">
        <p className="text-[10px] font-bold tracking-widest uppercase text-slate-500">{label}</p>
        <p className={`mt-0.5 text-lg font-bold tabular-nums ${cls}`}>{value}</p>
        {sub && <p className="text-[11px] text-slate-500">{sub}</p>}
    </div>
);

const AuctionBoard = () => {
    const [data, setData] = useState(null);
    const [bt, setBt] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [tracking, setTracking] = useState(false);
    const [search, setSearch] = useState('');
    const [profitableOnly, setProfitableOnly] = useState(false);
    const [visible, setVisible] = useState(PAGE_SIZE);
    const [copied, setCopied] = useState('');

    const load = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const [a, b] = await Promise.all([
                fetch('/api/huginn/lootfarm/auctions').then(r => r.json()),
                fetch('/api/huginn/lootfarm/auctions/backtest').then(r => r.json()),
            ]);
            if (a.error) throw new Error(a.error);
            setData(a); setBt(b.error ? null : b);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { load(); }, [load]);

    const snapshotNow = async () => {
        setTracking(true);
        try { await fetch('/api/huginn/lootfarm/auctions/track', { method: 'POST' }); await load(); }
        finally { setTracking(false); }
    };

    const copyName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopied(name); setTimeout(() => setCopied(c => (c === name ? '' : c)), 1200);
    };

    const rows = data?.rows || [];
    const filtered = useMemo(() => rows.filter(r => {
        if (profitableOnly && !((r.profitPercent ?? -1) > 0)) return false;
        return matchesSearchQuery([r.itemName?.marketHashName], search);
    }), [rows, search, profitableOnly]);
    useEffect(() => { setVisible(PAGE_SIZE); }, [search, profitableOnly]);

    return (
        <div className="flex-1 flex flex-col gap-3 min-h-0">
            {/* Backtest / tracker summary */}
            <div className="shrink-0 bg-odin-blue/30 border border-white/5 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-3">
                    <Gavel size={15} className="text-amber-400" />
                    <span className="text-sm font-semibold text-slate-200">LOOT.Farm auctions</span>
                    <span className="text-xs text-slate-500">win a lot → resell on Steam (net 13% fee)</span>
                    <div className="ml-auto flex items-center gap-2">
                        <button onClick={snapshotNow} disabled={tracking}
                            title="Record one snapshot into the tracker log now"
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-black/30 border border-white/10 text-xs text-slate-300 hover:text-white hover:border-white/20 disabled:opacity-50">
                            <Camera size={12} className={tracking ? 'animate-pulse' : ''} /> Snapshot
                        </button>
                        <button onClick={load} disabled={loading}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600/70 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50">
                            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
                        </button>
                    </div>
                </div>
                {bt && (
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
                        <Tile label="Live lots" value={data?.lots ?? '—'} sub={`${data?.items ?? 0} items`} />
                        <Tile label="Snipeable now" value={bt.live_profitable ?? '—'} sub={`≥${bt.min_profit_pct}% flip`} cls="text-emerald-400" />
                        <Tile label="Tracked (cleared)" value={bt.cleared_lots ?? 0} sub="in the log" />
                        <Tile label="Bid rate" value={bt.bid_rate_pct != null ? `${bt.bid_rate_pct}%` : '—'} sub={`${bt.bid_lots ?? 0} got bids`} />
                        <Tile label="Avg clear markup" value={bt.avg_clear_markup_pct != null ? `+${bt.avg_clear_markup_pct}%` : '—'} sub="bid lots vs base" cls="text-amber-300" />
                        <Tile label="Profitable snipes" value={bt.snipe_samples ? `${bt.profitable_snipes}/${bt.snipe_samples}` : '—'} sub="0-bid, cleared" />
                    </div>
                )}
                <p className="mt-2 text-[11px] text-slate-600">
                    Tracker snapshots every 15 min; the backtest sharpens as cleared lots accumulate. Prices are by item name — float/stickers not priced in.
                </p>
            </div>

            {/* Controls */}
            <div className="shrink-0 flex items-center gap-3 flex-wrap">
                <div className="relative min-w-[220px] max-w-xs flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                    <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search auctioned items…"
                        className="w-full pl-9 pr-3 py-2 text-sm bg-black/30 border border-white/10 rounded-lg text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40" />
                </div>
                <button onClick={() => setProfitableOnly(v => !v)}
                    className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${profitableOnly ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}>
                    Profitable only
                </button>
                <span className="text-xs text-slate-500">showing {Math.min(visible, filtered.length)} of {filtered.length}</span>
            </div>

            {error && <div className="bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">{error}</div>}

            {/* Table */}
            <div className="flex-1 overflow-auto rounded-xl border border-white/5 min-h-0">
                <table className="w-full text-sm min-w-[880px]">
                    <thead className="sticky top-0 bg-odin-blue/80 backdrop-blur text-slate-400 text-[11px] uppercase tracking-wider z-10">
                        <tr>
                            <th className="text-left font-semibold px-3 py-2">Item</th>
                            <th className="text-left font-semibold px-3 py-2">Ext</th>
                            <th className="text-right font-semibold px-3 py-2" title="LOOT.Farm base price">Base</th>
                            <th className="text-right font-semibold px-3 py-2" title="Cheapest current lot — what you'd win it for">Win</th>
                            <th className="text-right font-semibold px-3 py-2" title="Total bids across lots / number of lots">Bids/Lots</th>
                            <th className="text-right font-semibold px-3 py-2" title="Steam resale, net of 13% fee">Steam net</th>
                            <th className="text-right font-semibold px-3 py-2">Tradeon</th>
                            <th className="text-right font-semibold px-3 py-2" title="Win → sell on Steam">Profit</th>
                            <th className="text-right font-semibold px-3 py-2">%</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                        {loading && !data ? (
                            <tr><td colSpan={9} className="px-3 py-16 text-center"><RefreshCw size={30} className="animate-spin text-amber-500 inline" /></td></tr>
                        ) : filtered.slice(0, visible).map((r, i) => {
                            const name = r.itemName?.marketHashName;
                            const href = getTradeonShortLink('LootFarm', name);
                            const bg = tierColor(r.pg);
                            const winText = money(r.current);
                            return (
                                <tr key={`${name}-${i}`} className="hover:bg-white/[0.02]">
                                    <td className="px-3 py-2 text-slate-200">
                                        <div className="flex items-center gap-2">
                                            <ItemIcon name={name} />
                                            <span onClick={() => copyName(name)} title={`${name}\n(click to copy)`} className="cursor-pointer hover:text-white truncate max-w-[280px]">{name}</span>
                                            {r.tradelock && <Lock size={11} className="text-orange-400 shrink-0" title="Trade-locked lot" />}
                                            {copied === name && <span className="shrink-0 text-[10px] text-emerald-400">copied</span>}
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-slate-400">{r.exterior || '—'}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(r.base)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        {href
                                            ? <a href={href} target="_blank" rel="noopener noreferrer" className="hover:opacity-80">
                                                {bg ? <span className="inline-block px-2 rounded font-semibold text-white" style={{ backgroundColor: bg, textShadow: '0 0 4px #000' }}>{winText}</span> : <span className="text-slate-200">{winText}</span>}
                                              </a>
                                            : <span className="text-slate-200">{winText}</span>}
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-400">{r.bids}/{r.lots}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-300">{money(r.steamNet)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-sky-300/80">{money(r.tradeon)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${plClass(r.profit)}`}>{plStr(r.profit)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${plClass(r.profitPercent)}`}>{pctStr(r.profitPercent)}</td>
                                </tr>
                            );
                        })}
                        {!loading && filtered.length === 0 && (
                            <tr><td colSpan={9} className="px-3 py-16 text-center text-slate-600">No auctions match.</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
            {visible < filtered.length && (
                <button onClick={() => setVisible(v => v + PAGE_SIZE)} className="shrink-0 w-full py-2 text-xs text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 rounded-lg transition-colors">
                    Load {Math.min(PAGE_SIZE, filtered.length - visible)} more ({filtered.length - visible} left)
                </button>
            )}
        </div>
    );
};

export default AuctionBoard;
