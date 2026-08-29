import React, { useMemo, useState, useEffect } from 'react';
import { RefreshCw, Search, Repeat, Lock, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';

// Draupnir's arbitrage deal counter. Pools every transaction you tagged as an
// arbitrage deal across ALL accounts (a play can source on one account/market and
// sell on another) and books the realized spread with the same avg-cost method as
// the rest of Draupnir. Each sell is split by where it settled: MARKET (real,
// withdrawable cash) vs STEAM (locked wallet money) — counted separately so Steam
// balance is never confused with real cash. Backward-looking, not a live scanner
// (that's Huginn).
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

const PAGE_SIZE = 100;

// Generic sortable + paginated table. `columns` items: { key, label, numeric,
// align, title, render(row), tdClass(row)|string }. Sort/page state lives here so
// each table on the tab sorts independently.
const SortableTable = ({ columns, rows, initialSort, emptyText, rowKey, minWidth = 820 }) => {
    const [sort, setSort] = useState(initialSort);
    const [visible, setVisible] = useState(PAGE_SIZE);
    useEffect(() => { setVisible(PAGE_SIZE); }, [rows]);

    const sorted = useMemo(() => {
        const { key, dir } = sort;
        const mult = dir === 'asc' ? 1 : -1;
        return [...rows].sort((a, b) => {
            const av = a[key], bv = b[key];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;            // missing values always sink
            if (bv == null) return -1;
            if (typeof av === 'string') return mult * av.localeCompare(bv);
            return mult * (av - bv);
        });
    }, [rows, sort]);

    const toggleSort = (key, numeric) => setSort(s =>
        s.key === key
            ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
            : { key, dir: numeric ? 'desc' : 'asc' }   // numbers default high→low, text A→Z
    );

    return (
        <>
            <div className="overflow-x-auto rounded-xl border border-white/5">
                <table className="w-full text-sm" style={{ minWidth }}>
                    <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                        <tr>
                            {columns.map(col => {
                                const active = sort.key === col.key;
                                const Arrow = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown;
                                return (
                                    <th key={col.key} title={col.title} className={`font-semibold px-3 py-2 ${col.align === 'right' ? 'text-right' : 'text-left'}`}>
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
                        {sorted.slice(0, visible).map((r, i) => (
                            <tr key={rowKey(r, i)} className="hover:bg-white/[0.02]">
                                {columns.map(col => {
                                    const extra = typeof col.tdClass === 'function' ? col.tdClass(r) : (col.tdClass || '');
                                    const base = col.align === 'right' ? 'text-right tabular-nums' : '';
                                    return (
                                        <td key={col.key} className={`px-3 py-2 ${base} ${extra}`}>
                                            {col.render ? col.render(r) : r[col.key]}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                        {rows.length === 0 && (
                            <tr><td colSpan={columns.length} className="px-3 py-8 text-center text-slate-600">{emptyText}</td></tr>
                        )}
                    </tbody>
                </table>
            </div>
            {visible < sorted.length && (
                <button onClick={() => setVisible(v => v + PAGE_SIZE)} className="mt-2 w-full py-2 text-xs text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 rounded-lg transition-colors">
                    Show more ({sorted.length - visible} more)
                </button>
            )}
        </>
    );
};

const TypeBadge = ({ type }) => (
    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${type === 'sell' ? 'bg-orange-500/15 text-orange-300' : 'bg-emerald-500/15 text-emerald-300'}`}>{type}</span>
);

// Headline card for one category (market = real cash, steam = locked wallet).
const CategoryCard = ({ title, note, icon, accent, cat }) => {
    const c = cat || {};
    return (
        <div className={`flex-1 min-w-[260px] bg-odin-blue/40 border ${accent.border} rounded-2xl p-5`}>
            <div className="flex items-center gap-2 mb-2">
                {icon}
                <span className={`text-sm font-semibold ${accent.title}`}>{title}</span>
                <span className="text-[11px] text-slate-500">{note}</span>
            </div>
            <div className="flex items-baseline gap-2">
                <span className={`text-3xl font-bold tabular-nums ${plClass(c.realized_pl)}`}>{plStr(c.realized_pl)}</span>
                {c.avg_margin_pct != null && <span className={`text-sm font-semibold tabular-nums ${plClass(c.avg_margin_pct)}`}>({pctStr(c.avg_margin_pct)})</span>}
            </div>
            <div className="grid grid-cols-2 gap-x-5 gap-y-1 mt-3 text-sm">
                <span className="text-slate-500">Deals</span>
                <span className="text-right tabular-nums text-slate-300">{c.closed_deals ?? 0} <span className="text-slate-600">· {c.units_flipped ?? 0}u</span></span>
                <span className="text-slate-500">Capital cycled</span>
                <span className="text-right tabular-nums text-slate-300">{money(c.cost_of_sold)}</span>
                <span className="text-slate-500">Proceeds</span>
                <span className="text-right tabular-nums text-slate-300">{money(c.proceeds)}</span>
            </div>
        </div>
    );
};

const ArbitrageDeals = ({ data, loading, pricing }) => {
    const [search, setSearch] = useState('');
    const [copiedName, setCopiedName] = useState('');

    const copyItemName = (name) => {
        navigator.clipboard?.writeText(name).catch(() => {});
        setCopiedName(name);
        setTimeout(() => setCopiedName(c => (c === name ? '' : c)), 1200);
    };

    const market = data?.market || {};
    const steam = data?.steam || {};
    const legs = data?.legs || [];

    const itemCell = (name) => (
        <div className="flex items-center gap-2">
            <ItemIcon name={name} />
            <span onClick={() => copyItemName(name)} title={`${name}\n(click to copy)`} className="cursor-pointer text-slate-200 transition-colors hover:text-white">{name}</span>
            {copiedName === name && <span className="shrink-0 text-xs font-medium text-emerald-400">Copied!</span>}
        </div>
    );

    const filterRows = (rows) => rows.filter(r => matchesSearchQuery([r.item_name], search));
    const marketRows = useMemo(() => filterRows(market.rows || []), [market.rows, search]);
    const steamRows = useMemo(() => filterRows(steam.rows || []), [steam.rows, search]);
    const filteredLegs = useMemo(
        () => legs.filter(l => matchesSearchQuery([l.item_name, l.account, l.platform], search)),
        [legs, search]
    );

    const itemColumns = [
        { key: 'item_name', label: 'Item', align: 'left', render: r => itemCell(r.item_name) },
        { key: 'sell_qty', label: 'Flipped', numeric: true, align: 'right', title: 'Units sold in this category', tdClass: 'text-slate-300' },
        { key: 'avg_cost', label: 'Avg cost', numeric: true, align: 'right', tdClass: 'text-slate-400', render: r => money(r.avg_cost) },
        { key: 'cost_of_sold', label: 'Cost', numeric: true, align: 'right', title: 'Avg-cost basis of the units sold', tdClass: 'text-slate-400', render: r => money(r.cost_of_sold) },
        { key: 'proceeds', label: 'Proceeds', numeric: true, align: 'right', title: 'Gross sell proceeds', tdClass: 'text-slate-300', render: r => money(r.proceeds) },
        {
            key: 'realized_pl', label: 'Profit', numeric: true, align: 'right', title: 'Realized profit (proceeds − cost)',
            tdClass: r => plClass(r.realized_pl),
            render: r => (<>{plStr(r.realized_pl)}{r.margin_pct != null && <span className="block text-[11px] opacity-70">{pctStr(r.margin_pct)}</span>}</>),
        },
    ];

    const CategoryBadge = ({ category }) => {
        if (!category) return <span className="text-slate-600">—</span>;
        const steam = category === 'steam';
        return (
            <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded ${steam ? 'text-sky-300 bg-sky-500/15' : 'text-emerald-300 bg-emerald-500/15'}`}>
                {steam && <Lock size={9} />}{steam ? 'steam' : 'market'}
            </span>
        );
    };

    const legColumns = [
        { key: 'item_name', label: 'Item', align: 'left', render: l => itemCell(l.item_name) },
        { key: 'account', label: 'Account', align: 'left', tdClass: 'text-slate-400 truncate max-w-[150px]', render: l => <span title={l.account}>{l.account}</span> },
        { key: 'date', label: 'Date', align: 'left', tdClass: 'text-slate-400 whitespace-nowrap' },
        { key: 'type', label: 'Type', align: 'left', render: l => <TypeBadge type={l.type} /> },
        { key: 'category', label: 'Cat.', align: 'left', title: 'Where the sell settled: market (real cash) or steam (locked wallet)', render: l => <CategoryBadge category={l.category} /> },
        { key: 'qty', label: 'Qty', numeric: true, align: 'right', tdClass: 'text-slate-300' },
        { key: 'price', label: 'Unit $', numeric: true, align: 'right', tdClass: 'text-slate-400', render: l => money(l.price) },
        { key: 'total', label: 'Total', numeric: true, align: 'right', tdClass: 'text-slate-200', render: l => money(l.total) },
        { key: 'platform', label: 'Platform', align: 'left', tdClass: 'text-slate-400 truncate max-w-[140px]', render: l => <span title={l.platform}>{l.platform || '—'}</span> },
        { key: 'realized_pl', label: 'P/L', numeric: true, align: 'right', title: 'Sell legs: proceeds − avg cost. Buy legs open a position (no P/L yet).', tdClass: l => plClass(l.realized_pl), render: l => plStr(l.realized_pl) },
    ];

    if (loading && !data) {
        return <div className="flex justify-center items-center h-64"><RefreshCw className="animate-spin text-yellow-500" size={36} /></div>;
    }
    if (!data) return null;

    const hasOpen = data.open_units > 0;
    const accounts = data.accounts_used || [];

    return (
        <div className="flex flex-col gap-5">
            {/* Header + category split */}
            <div>
                <div className="flex items-center gap-2 text-sm text-slate-300 mb-3">
                    <Repeat size={15} className="text-yellow-400" />
                    <span className="font-semibold text-slate-200">Arbitrage deals</span>
                    <span className="text-slate-500">· pooled across every account · total {plStr(data.realized_pl)}</span>
                </div>
                <div className="flex gap-3 flex-wrap">
                    <CategoryCard
                        title="Market arbitrage" note="real, withdrawable cash"
                        icon={<Repeat size={15} className="text-emerald-400" />}
                        accent={{ border: 'border-emerald-500/20', title: 'text-emerald-300' }}
                        cat={market}
                    />
                    <CategoryCard
                        title="Steam arbitrage" note="locked Steam wallet balance"
                        icon={<Lock size={14} className="text-sky-400" />}
                        accent={{ border: 'border-sky-500/20', title: 'text-sky-300' }}
                        cat={steam}
                    />
                </div>
                <p className="text-xs text-slate-500 mt-3">
                    {data.items} items across {accounts.length || data.account_count} account{(accounts.length || data.account_count) === 1 ? '' : 's'}
                    {accounts.length > 0 && <span className="text-slate-600"> · {accounts.join(', ')}</span>}
                    {hasOpen && <span className="text-slate-400"> · {data.open_units} units open{data.open_unrealized != null ? ` (${plStr(data.open_unrealized)} unreal.)` : ''}</span>}
                </p>
            </div>

            {/* Shared search */}
            <div className="relative min-w-[200px] max-w-xs">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                <input
                    value={search} onChange={e => setSearch(e.target.value)} placeholder="Search item / account / platform…"
                    className="w-full pl-9 pr-3 py-1.5 text-sm bg-odin-blue/60 border border-white/10 rounded-lg text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-yellow-500/50"
                />
            </div>

            {/* Market arbitrage by item */}
            <section>
                <h3 className="flex items-center gap-1.5 text-sm font-semibold text-emerald-300 mb-2">
                    <Repeat size={13} /> Market arbitrage — by item
                    <span className="text-xs font-normal text-slate-500">({marketRows.length}{market.rows && marketRows.length !== market.rows.length ? ` of ${market.rows.length}` : ''})</span>
                </h3>
                <SortableTable
                    columns={itemColumns} rows={marketRows}
                    initialSort={{ key: 'realized_pl', dir: 'desc' }}
                    rowKey={r => r.item_name}
                    emptyText={(market.rows || []).length ? 'No items match your search.' : 'No market arbitrage deals tagged yet.'}
                    minWidth={720}
                />
            </section>

            {/* Steam arbitrage by item */}
            <section>
                <h3 className="flex items-center gap-1.5 text-sm font-semibold text-sky-300 mb-2">
                    <Lock size={12} /> Steam arbitrage — by item
                    <span className="text-xs font-normal text-slate-500">(locked wallet · {steamRows.length}{steam.rows && steamRows.length !== steam.rows.length ? ` of ${steam.rows.length}` : ''})</span>
                </h3>
                <SortableTable
                    columns={itemColumns} rows={steamRows}
                    initialSort={{ key: 'realized_pl', dir: 'desc' }}
                    rowKey={r => r.item_name}
                    emptyText={(steam.rows || []).length ? 'No items match your search.' : 'No Steam arbitrage deals — nothing sold into Steam wallet yet.'}
                    minWidth={720}
                />
            </section>

            {/* Per-deal ledger */}
            <section>
                <h3 className="text-sm font-semibold text-slate-200 mb-2">
                    Deals <span className="text-xs font-normal text-slate-500">({filteredLegs.length === legs.length ? legs.length : `${filteredLegs.length} of ${legs.length}`}) · every tagged leg, by account &amp; date</span>
                </h3>
                <SortableTable
                    columns={legColumns} rows={filteredLegs}
                    initialSort={{ key: 'date', dir: 'desc' }}
                    rowKey={(l, i) => l.id || `${l.item_name}-${l.date}-${i}`}
                    emptyText={legs.length ? 'No deals match your search.' : 'No arbitrage deals tagged yet — mark a buy/sell as Arbitrage on a portfolio to count it here.'}
                    minWidth={960}
                />
            </section>
        </div>
    );
};

export default ArbitrageDeals;
