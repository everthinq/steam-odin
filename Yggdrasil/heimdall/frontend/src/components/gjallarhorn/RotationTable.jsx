import { useMemo, useState } from 'react';
import { ArrowUpDown, Lock, Unlock, Info } from 'lucide-react';
import InfoTip from './InfoTip';

// The sell list. Each held item scored for how well it funds a rotation into a
// freshly-limited case: liquidity-first (Steam 7-day turnover, from Tradeon pulse), deflation shown
// as its own column, tradable-now overlaid when an account is picked. Sorting is
// client-side; the backend pre-sorts by rotation score.
const money = (v) => (v == null ? '—' : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const num = (v) => (v == null ? '—' : Number(v).toLocaleString());
const pct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`);

const SCORE_HINT = 'Rotation score (0–100): mostly Steam turnover (log-scaled 7-day volume) '
    + '— can I offload it fast — nudged up by how deflated it is vs your cost (dead money '
    + 'is more worth rotating), and cut to a quarter when the item is held but trade-locked '
    + '(can’t be sold right now). Higher = better sell-and-rotate candidate. '
    + 'Deflation is shown separately in the “Δ vs cost” column, not baked away.';

const COLUMNS = [
    { key: 'item_name', label: 'Item', align: 'left' },
    { key: 'buy_platform', label: 'Bought on', align: 'left', hint: 'Where you bought it, from your Draupnir buy records (most-bought platform first).' },
    { key: 'holder', label: 'Held by', align: 'left', hint: 'Which account (portfolio) holds the item. In the combined view, the accounts with an open position, most-held first.' },
    { key: 'net_qty', label: 'Qty', align: 'right' },
    { key: 'avg_cost', label: 'Avg cost', align: 'right' },
    { key: 'current_price', label: 'Now', align: 'right' },
    { key: 'deflation_pct', label: 'Δ vs cost', align: 'right', hint: 'Current price vs your average buy cost. Negative = deflated (below what you paid).' },
    { key: 'realized_if_sold', label: 'P/L if sold', align: 'right', hint: 'Profit or loss you would realize selling the whole position now at the current price.' },
    { key: 'volume7d', label: '7d sold', align: 'right', hint: 'Units sold on the Steam market in the last 7 days (turnover / liquidity), from Tradeon pulse — no Steam request, so no Steam rate limits. Hover a cell for Market.CSGO\'s week too.' },
    { key: 'spread_pct', label: 'Spread', align: 'right', hint: 'Steam lowest listing vs highest buy order — a tight spread means a liquid item; a wide one means selling now (into the buy orders) costs you.' },
    { key: 'vs_median_pct', label: 'vs 30-day median', align: 'right', hint: 'Steam lowest listing now vs its 30-day median price (pulse). Negative = cheaper than its usual month — it is still bleeding, or the dip is temporary. Items pulse does not know show Steam\'s own 7-day trend instead, marked "7d".' },
    { key: 'tradable', label: 'Tradable', align: 'center', hint: 'From the picked account’s live inventory: how many are tradable now vs still trade-locked (Steam holds sale funds on locked items).' },
    { key: 'rotation_score', label: 'Score', align: 'right', hint: SCORE_HINT },
];

const signClass = (v) => (v == null ? 'text-slate-500' : v < 0 ? 'text-red-400' : v > 0 ? 'text-emerald-400' : 'text-slate-300');

function TradableCell({ overlay, active }) {
    if (!active) return <span className="text-slate-600 text-xs">—</span>;
    if (!overlay || !overlay.inInventory) return <span className="text-slate-600 text-xs">not held</span>;
    if (overlay.tradableNow && !overlay.locked) {
        return <span className="inline-flex items-center gap-1 text-emerald-400 text-xs"><Unlock size={11} />{overlay.tradableNow} now</span>;
    }
    if (overlay.locked && !overlay.tradableNow) {
        return <span className="inline-flex items-center gap-1 text-red-400 text-xs" title={overlay.nextUnlock || ''}><Lock size={11} />{overlay.locked} locked</span>;
    }
    return (
        <span className="inline-flex items-center gap-1 text-xs" title={overlay.nextUnlock || ''}>
            <span className="text-emerald-400">{overlay.tradableNow}</span>
            <span className="text-slate-600">/</span>
            <span className="text-red-400">{overlay.locked}<Lock size={10} className="inline ml-0.5" /></span>
        </span>
    );
}

const RotationTable = ({ rows, overlayActive, deflatedOnly }) => {
    const [sortKey, setSortKey] = useState('rotation_score');
    const [sortDir, setSortDir] = useState('desc');

    const sortValue = (row, key) => {
        if (key === 'tradable') {
            const o = row.tradable;
            if (!o || !o.inInventory) return -1;
            return o.tradableNow || 0;
        }
        return row[key];
    };

    const sorted = useMemo(() => {
        const base = deflatedOnly ? rows.filter((r) => r.deflated) : rows;
        const dir = sortDir === 'asc' ? 1 : -1;
        return [...base].sort((a, b) => {
            const av = sortValue(a, sortKey);
            const bv = sortValue(b, sortKey);
            if (av == null && bv == null) return 0;
            if (av == null) return 1;      // nulls always last
            if (bv == null) return -1;
            if (typeof av === 'string') return dir * av.localeCompare(bv);
            return dir * (av - bv);
        });
    }, [rows, sortKey, sortDir, deflatedOnly]);

    const toggleSort = (key) => {
        if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        else { setSortKey(key); setSortDir(key === 'item_name' ? 'asc' : 'desc'); }
    };

    if (!rows.length) {
        return <div className="text-center text-slate-500 text-sm py-12">No holdings to rotate. Pick a portfolio with open positions.</div>;
    }

    return (
        <div className="flex-1 overflow-auto custom-scrollbar rounded-xl border border-white/10">
            <table className="w-full text-sm border-collapse">
                <thead className="sticky top-0 z-10 bg-[#0d1520]">
                    <tr className="text-[11px] uppercase tracking-wider text-slate-500">
                        {COLUMNS.map((c) => (
                            <th
                                key={c.key}
                                onClick={() => toggleSort(c.key)}
                                className={`px-3 py-2.5 font-semibold cursor-pointer select-none hover:text-slate-300 whitespace-nowrap ${c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left'}`}
                            >
                                <span className="inline-flex items-center gap-1">
                                    {c.label}
                                    {c.hint && (
                                        <InfoTip tip={c.hint}>
                                            <Info size={10} className="text-slate-500 hover:text-amber-400/80" />
                                        </InfoTip>
                                    )}
                                    {sortKey === c.key && <ArrowUpDown size={11} className="text-amber-500/70" />}
                                </span>
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {sorted.map((r) => (
                        <tr key={r.item_name} className={`border-t border-white/5 hover:bg-white/5 ${r.deflated ? 'bg-red-500/[0.04]' : ''}`}>
                            <td className="px-3 py-2 text-slate-200 max-w-[22rem] truncate" title={r.item_name}>{r.item_name}</td>
                            <td className="px-3 py-2 text-slate-400 capitalize max-w-[9rem] truncate" title={r.buy_platform || ''}>{r.buy_platform || '—'}</td>
                            <td className="px-3 py-2 text-slate-400 max-w-[10rem] truncate" title={r.holder || ''}>{r.holder || '—'}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-300">{num(r.net_qty)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(r.avg_cost)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-200">{money(r.current_price)}</td>
                            <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.deflation_pct)}`}>{pct(r.deflation_pct)}</td>
                            <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.realized_if_sold)}`}>{r.realized_if_sold == null ? '—' : money(r.realized_if_sold)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-300" title={[r.volume24h != null && `${num(r.volume24h)} in 24h`, r.tm_volume7d != null && `Market.CSGO: ${num(r.tm_volume7d)} in 7 days`, r.liquidity_source === 'steam' && 'From Steam (pulse does not know this item)'].filter(Boolean).join('\n')}>{num(r.volume7d)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-400" title={r.steam_highest_buy != null ? `Lowest listing ${money(r.steam_lowest)} · highest buy order ${money(r.steam_highest_buy)}` : ''}>{r.spread_pct == null ? '—' : `${r.spread_pct.toFixed(1)}%`}</td>
                            {r.vs_median_pct != null
                                ? <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.vs_median_pct)}`} title={r.steam_median != null ? `30-day median ${money(r.steam_median)}` : ''}>{pct(r.vs_median_pct)}</td>
                                : <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.trend7d_pct)}`} title="Steam's own 7-day trend (pulse does not know this item)">{r.trend7d_pct == null ? '—' : <>{pct(r.trend7d_pct)} <span className="text-[10px] text-slate-500">7d</span></>}</td>}
                            <td className="px-3 py-2 text-center"><TradableCell overlay={r.tradable} active={overlayActive} /></td>
                            <td className="px-3 py-2 text-right">
                                {r.rotation_score == null ? <span className="text-slate-600">—</span> : (
                                    <InfoTip tip={SCORE_HINT} className="items-center gap-2">
                                        <div className="w-10 h-1.5 rounded-full bg-white/10 overflow-hidden">
                                            <div className="h-full bg-amber-500" style={{ width: `${Math.min(100, r.rotation_score)}%` }} />
                                        </div>
                                        <span className="tabular-nums text-slate-300 w-8 text-right">{r.rotation_score}</span>
                                    </InfoTip>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

export default RotationTable;
