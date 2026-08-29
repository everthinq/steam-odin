import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { RefreshCw, Search, Repeat, Lock, Check } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';
import { getTradeonShortLink } from '../utils/tradeonShortLink';

// LOOT.Farm arbitrage: buy LF balance cheap (from the USDT OTC trader) → acquire an item
// from LOOT.Farm (unlocked +3% so there's no 7-day ban) → instant-sell into a Steam/Buff/
// CSFloat BUY ORDER. Cost = LF_price × markup × balance_rate; profit is vs the best
// REAL-CASH exit (Buff/CSFloat) — Steam is shown but is locked wallet, not real cash.
const ITEM_IMG_BASE = 'https://api.steamapis.com/image/item/730/';
const ItemIcon = ({ name }) => (
    <img src={`${ITEM_IMG_BASE}${encodeURIComponent(name)}`} alt="" loading="lazy" width={30} height={30}
         onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
         className="w-[30px] h-[30px] object-contain shrink-0 drop-shadow" />
);
const money = (v) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plClass = (v) => v == null ? 'text-slate-500' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
const pctStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(0)}%`;
// LOOT.Farm price tier colors (their own), by `rate` % of Steam.
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

const load = (k, d) => { try { const v = Number(localStorage.getItem(k)); return Number.isFinite(v) && v > 0 ? v : d; } catch { return d; } };

const LootfarmArbitrage = ({ byHash }) => {
    const [balancePct, setBalancePct] = useState(() => load('lf_balance_pct', 92));   // trader sells balance at +92%
    const [unlocked, setUnlocked] = useState(() => { try { return localStorage.getItem('lf_unlocked') !== '0'; } catch { return true; } });
    const [inStock, setInStock] = useState(true);
    const [myInventory, setMyInventory] = useState(false);   // flip items you already own (unlocked → no ban)
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [profitableOnly, setProfitableOnly] = useState(true);
    const [visible, setVisible] = useState(PAGE_SIZE);
    const [copied, setCopied] = useState('');

    const rate = 1 / (1 + (balancePct || 0) / 100);   // USDT per $1 of LF balance

    const fetchData = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const p = new URLSearchParams({ balance: rate.toFixed(4), unlocked: unlocked ? '1' : '0', in_stock: inStock ? '1' : '0' });
            const r = await fetch(`/api/huginn/lootfarm/arbitrage?${p}`).then(x => x.json());
            if (r.error) throw new Error(r.error);
            setData(r);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, [rate, unlocked, inStock]);

    useEffect(() => { fetchData(); }, [fetchData]);

    const copyName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopied(name); setTimeout(() => setCopied(c => (c === name ? '' : c)), 1200);
    };

    const rows = data?.rows || [];
    const owned = (name) => byHash?.[name]?.count || 0;
    const filtered = useMemo(() => rows.filter(r => {
        const name = r.itemName?.marketHashName;
        if (inStock && (r.avail || 0) <= 0) return false;   // buyable = have − trade-locked − reserved
        if (myInventory && !owned(name)) return false;
        if (profitableOnly && !((r.profitPercent ?? -1) > 0)) return false;
        return matchesSearchQuery([name], search);
    }), [rows, search, profitableOnly, myInventory, inStock, byHash]);
    useEffect(() => { setVisible(PAGE_SIZE); }, [search, profitableOnly, myInventory, data]);

    const positive = rows.filter(r => (r.profitPercent ?? -1) > 0).length;

    return (
        <div className="flex-1 flex flex-col gap-3 min-h-0">
            {/* Header + controls */}
            <div className="shrink-0 bg-odin-blue/30 border border-white/5 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <Repeat size={15} className="text-amber-400" />
                    <span className="text-sm font-semibold text-slate-200">LOOT.Farm arbitrage</span>
                    <span className="text-xs text-slate-500">buy balance cheap → LF item → instant-sell into a buy order</span>
                    <button onClick={fetchData} disabled={loading}
                        className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600/70 hover:bg-amber-500 text-white text-xs font-medium disabled:opacity-50">
                        <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
                    </button>
                </div>
                <div className="flex items-center gap-4 flex-wrap text-xs">
                    <label className="flex items-center gap-1.5" title="What the OTC trader charges for LOOT.Farm balance. +92% = you pay 0.52 USDT per $1 of balance.">
                        <span className="font-bold tracking-widest text-slate-500 uppercase">Balance @ +</span>
                        <input type="number" min="0" max="300" step="1" value={balancePct}
                            onChange={e => { const v = e.target.value === '' ? '' : Math.max(0, Number(e.target.value)); setBalancePct(v); try { if (v !== '') localStorage.setItem('lf_balance_pct', String(v)); } catch { /* */ } }}
                            onBlur={e => { if (e.target.value === '') setBalancePct(92); }}
                            className="w-16 bg-black/30 border border-white/10 rounded px-2 py-1 text-slate-200 tabular-nums focus:outline-none focus:ring-1 focus:ring-amber-500/50" />
                        <span className="text-slate-500">% → {rate.toFixed(3)} USDT/$</span>
                    </label>
                    <button onClick={() => setUnlocked(v => { try { localStorage.setItem('lf_unlocked', v ? '0' : '1'); } catch { /* */ } return !v; })}
                        title="Buy the unlocked (+3%) variant so there's no 7-day trade ban — sell same day."
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium border transition-colors ${unlocked ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white'}`}>
                        {unlocked ? <Check size={12} /> : <span className="w-3" />} Unlocked +3%
                    </button>
                    <button onClick={() => setInStock(v => !v)}
                        title="Only items LOOT.Farm currently holds (have > 0) — required to rebuy them. On by default."
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium border transition-colors ${inStock ? 'bg-sky-500/20 border-sky-500/40 text-sky-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white'}`}>
                        {inStock ? <Check size={12} /> : <span className="w-3" />} In stock only
                    </button>
                    <button onClick={() => setMyInventory(v => !v)} disabled={!byHash}
                        title={byHash ? 'Only items you own — sell your copy now, rebuy the same item on LOOT.Farm (keep "In stock only" on so it\'s rebuyable).' : 'Scan your inventory (Get all items) first'}
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium border transition-colors disabled:opacity-40 ${myInventory ? 'bg-amber-500/20 border-amber-500/40 text-amber-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white'}`}>
                        {myInventory ? <Check size={12} /> : <span className="w-3" />} My Inventory
                    </button>
                    <button onClick={() => setProfitableOnly(v => !v)}
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg font-medium border transition-colors ${profitableOnly ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300' : 'bg-black/20 border-white/10 text-slate-400 hover:text-white'}`}>
                        {profitableOnly ? <Check size={12} /> : <span className="w-3" />} Profitable only
                    </button>
                    {data && (
                        <span className="text-slate-500">{positive} profitable / {data.items} items · CSFloat buy-order coverage {data.csfloat_coverage}</span>
                    )}
                </div>
                <p className="mt-2 text-[11px] text-slate-600">
                    Profit is vs the best <b>real-cash</b> exit (Buff/CSFloat buy order, net of fees). Steam is shown but pays locked wallet, so it's excluded from profit. Prices by item name — float/stickers not priced in.
                    {myInventory && <span className="text-amber-400/80"> · <b>Sell-and-rebuy:</b> items you own that LOOT.Farm also stocks — sell your (unlocked) copy now on the best exit market, rebuy the same item on LOOT.Farm with cheap balance (Cost). Profit = sell − rebuy; you keep the item (rebought, locked) and your cash-out never waits on a ban. Keep "In stock only" on so it's rebuyable.</span>}
                </p>
            </div>

            {/* Search */}
            <div className="shrink-0 flex items-center gap-3 flex-wrap">
                <div className="relative min-w-[220px] max-w-xs flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                    <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search items…"
                        className="w-full pl-9 pr-3 py-2 text-sm bg-black/30 border border-white/10 rounded-lg text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-amber-500/40" />
                </div>
                <span className="text-xs text-slate-500">showing {Math.min(visible, filtered.length)} of {filtered.length}</span>
            </div>

            {error && <div className="bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">{error}</div>}

            {/* Table */}
            <div className="flex-1 overflow-auto rounded-xl border border-white/5 min-h-0">
                <table className="w-full text-sm min-w-[940px]">
                    <thead className="sticky top-0 bg-odin-blue/80 backdrop-blur text-slate-400 text-[11px] uppercase tracking-wider z-10">
                        <tr>
                            <th className="text-left font-semibold px-3 py-2">Item</th>
                            <th className="text-right font-semibold px-3 py-2" title="LOOT.Farm base price">LF</th>
                            <th className="text-right font-semibold px-3 py-2" title="Buyable now = LOOT.Farm holdings minus trade-locked and reserved">Buyable</th>
                            <th className="text-right font-semibold px-3 py-2" title="Real USDT cost = LF × markup × balance rate">Cost</th>
                            <th className="text-right font-semibold px-3 py-2" title="Steam buy-order, net 13% — LOCKED wallet">Steam</th>
                            <th className="text-right font-semibold px-3 py-2" title="Buff buy-order, net 1.5%">Buff</th>
                            <th className="text-right font-semibold px-3 py-2" title="CSFloat buy-order (sweep cache), net 2%">CSFloat</th>
                            <th className="text-right font-semibold px-3 py-2" title="Real-cash profit vs cost">Profit</th>
                            <th className="text-right font-semibold px-3 py-2">%</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                        {loading && !data ? (
                            <tr><td colSpan={9} className="px-3 py-16 text-center"><RefreshCw size={30} className="animate-spin text-amber-500 inline" /></td></tr>
                        ) : filtered.slice(0, visible).map((r, i) => {
                            const name = r.itemName?.marketHashName;
                            const href = getTradeonShortLink('LootFarm', name);
                            const bg = tierColor(r.rate);
                            const lfTxt = money(r.lf);
                            const cell = (v, mk) => <td className={`px-3 py-2 text-right tabular-nums ${r.bestRealMarket === mk ? 'text-emerald-300 font-semibold' : 'text-slate-400'}`}>{money(v)}</td>;
                            return (
                                <tr key={`${name}-${i}`} className="hover:bg-white/[0.02]">
                                    <td className="px-3 py-2 text-slate-200">
                                        <div className="flex items-center gap-2">
                                            <ItemIcon name={name} />
                                            <span onClick={() => copyName(name)} title={`${name}\n(click to copy)`} className="cursor-pointer hover:text-white truncate max-w-[300px]">{name}</span>
                                            {owned(name) > 0 && <span className="shrink-0 text-[10px] font-bold text-amber-400" title="in your inventory">×{owned(name)}</span>}
                                            {copied === name && <span className="shrink-0 text-[10px] text-emerald-400">copied</span>}
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        {href
                                            ? <a href={href} target="_blank" rel="noopener noreferrer" className="hover:opacity-80">
                                                {bg ? <span className="inline-block px-2 rounded font-semibold text-white" style={{ backgroundColor: bg, textShadow: '0 0 4px #000' }}>{lfTxt}</span> : <span className="text-slate-300">{lfTxt}</span>}
                                              </a>
                                            : <span className="text-slate-300">{lfTxt}</span>}
                                    </td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${(r.avail || 0) > 0 ? 'text-slate-300' : 'text-red-400'}`}
                                        title={`Buyable now: ${r.avail}. LOOT.Farm holds ${r.have} (${r.tr || 0} trade-locked, ${r.res || 0} reserved) · max ${r.max === 0 ? '∞ (Unstable)' : r.max}`}>
                                        {r.avail}{(r.have !== r.avail) && <span className="text-slate-600">/{r.have}</span>}
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums text-amber-200/90">{money(r.cost)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.steam != null ? <span title="locked Steam wallet"><Lock size={9} className="inline mr-0.5 -mt-0.5" />{money(r.steam)}</span> : '—'}</td>
                                    {cell(r.buff, 'buff')}
                                    {cell(r.csfloat, 'csfloat')}
                                    <td className={`px-3 py-2 text-right tabular-nums ${plClass(r.profit)}`}>{r.profit == null ? '—' : `${r.profit > 0 ? '+' : ''}${money(r.profit)}`}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${plClass(r.profitPercent)}`}>{pctStr(r.profitPercent)}</td>
                                </tr>
                            );
                        })}
                        {!loading && filtered.length === 0 && (
                            <tr><td colSpan={9} className="px-3 py-16 text-center text-slate-600">No items match.</td></tr>
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

export default LootfarmArbitrage;
