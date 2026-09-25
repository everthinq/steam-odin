import { Fragment, useMemo, useState } from 'react';
import { ArrowUpDown, Info, ChevronRight, ChevronDown, BadgeCheck, CircleDashed, ExternalLink, ListOrdered, Clock } from 'lucide-react';
import InfoTip from '../gjallarhorn/InfoTip';

// Andvari card deals table. The backend pre-ranks by profit per copy; sorting
// here is client-side. A row expands into the per-account breakdown (which
// accounts can still buy it at their regional price, who already owns it and how
// many card drops they have left).
const money = (v) => (v == null ? '—' : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const signed = (v) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${money(Math.abs(v))}`);
const percent = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(0)}%`);
const signClass = (v) => (v == null ? 'text-slate-500' : v < 0 ? 'text-red-400' : v > 0 ? 'text-emerald-400' : 'text-slate-300');

// "ends in 5 h" for a sale end (epoch seconds); null when unknown or past.
const endsIn = (epochSeconds) => {
    if (!epochSeconds) return null;
    const hours = (epochSeconds - Date.now() / 1000) / 3600;
    if (hours <= 0) return null;
    if (hours < 1) return 'ends within the hour';
    if (hours < 48) return `ends in ${Math.floor(hours)} h`;
    return `ends in ${Math.floor(hours / 24)} days`;
};

// Where the card value comes from, most trustworthy first (mirrors the backend ranking).
const SOURCES = {
    buy_orders: { label: 'buy orders', rank: 0, className: 'text-emerald-400', Icon: BadgeCheck,
        tip: 'Every card of the set priced from its live buy orders: what the drops sell for right now.' },
    listings: { label: 'listings', rank: 1, className: 'text-sky-300', Icon: ListOrdered,
        tip: 'Every card priced from its lowest Market listing (odd one-off asks capped). Buy orders not fetched yet, or the Listing card value is selected.' },
    estimate: { label: 'estimate', rank: 2, className: 'text-slate-500', Icon: CircleDashed,
        tip: 'From the SteamCardExchange full-set price only — optimistic, not checked on the Market yet. Shortlisted games get checked during each scan.' },
};

const COLUMNS = [
    { key: 'name', label: 'Game', align: 'left' },
    { key: 'price', label: 'Price', align: 'right', hint: 'What the game costs in the store country most of your accounts are in, in US dollars. Other accounts pay their own country’s price — often very different (Turkey can be several times cheaper; Hong Kong and Norway pay in their own currency, converted at today’s exchange rate). Hover a price for every region.' },
    { key: 'card_drops', label: 'Drops', align: 'right', hint: 'Card drops you get for buying the game: half the card set, rounded up. Hover for the set size.' },
    { key: 'expected_net', label: 'Cards net', align: 'right', hint: 'Expected money back from one copy’s drops (drops × the average card), after Steam’s 5% + publisher’s 10% fee (each at least one cent). Instant card value (default): each card sold to its highest buy order. Listing card value: listed one cent under the lowest ask. The Source column says which prices were used. Wallet money.' },
    { key: 'worst_case_profit', label: 'Worst case', align: 'right', hint: 'Profit if every drop is the cheapest card in the set (at the same card value). Only known once the game has been checked card by card on the Steam Market.' },
    { key: 'profit', label: 'Profit', align: 'right', hint: 'Cards net minus the Price column, per copy, with the return on the price underneath. Can be negative while the game is still a deal for accounts in a cheaper country — see Best account.' },
    { key: 'best_profit', label: 'Best account', align: 'right', hint: 'Profit for the account that pays the least (its own country’s price) among those that do not own the game yet.' },
    { key: 'profitable_accounts', label: 'Accounts', align: 'right', hint: 'Accounts that can buy it at a profit at their own regional price / accounts that already own it. Expand the row for the list.' },
    { key: 'total_profit_all_accounts', label: 'All accounts', align: 'right', hint: 'Profit if every account that does not own the game yet buys one copy, at each account’s own regional price. With buy-order prices this already includes the market impact: all those copies are sold into the order book together, walking down the bids. Otherwise it does not — selling many copies of the same cards pushes their price down.' },
    { key: 'fewest_listings', label: 'Liquidity', align: 'right', hint: 'Fewest listings among the set’s cards on the Steam Market — a card with very few listings is thin: it may sell slowly or its price may be a one-off. Known once checked.' },
    { key: 'value_source', label: 'Source', align: 'center', hint: 'Where the card prices come from — buy orders (live, what the cards sell for right now), listings (lowest Market asks) or estimate (SteamCardExchange set price, not checked yet).' },
];

function PriceCell({ row }) {
    const regions = Object.entries(row.regional_prices || {});
    // Every priced account country, in USD (converted from the local currency where it isn't USD).
    const tip = regions.length
        ? regions.map(([country, p]) => `${country}: ${money(p.usd)}${p.currency !== 'USD' ? ` (${p.currency} ${Number(p.local).toFixed(2)})` : ''}`).join(' · ')
        : null;
    return (
        <InfoTip tip={tip}>
            <span className="inline-flex flex-col items-end">
                <span className="inline-flex items-center gap-1.5 justify-end">
                    {row.discount_percent > 0 && (
                        <span className="px-1 rounded bg-emerald-500/15 text-emerald-300 text-[10px] font-semibold">−{row.discount_percent}%</span>
                    )}
                    {row.original_price != null && row.discount_percent > 0 && (
                        <span className="text-slate-600 line-through text-xs">{money(row.original_price)}</span>
                    )}
                    <span className="text-slate-200">{money(row.price)}</span>
                </span>
                {row.price_low !== row.price_high && (
                    <span className="text-[10px] text-slate-500">{money(row.price_low)}–{money(row.price_high)}</span>
                )}
                {endsIn(row.discount_ends_at) && (
                    <span className="text-[10px] text-amber-300/90 inline-flex items-center gap-0.5"><Clock size={9} />sale {endsIn(row.discount_ends_at)}</span>
                )}
            </span>
        </InfoTip>
    );
}

// When the row leads with sell-price (list) numbers but buy orders are known too,
// show the sell-now figure underneath (and the other way round).
function OtherValuation({ row, field, main, signedValue }) {
    const other = row.value_source === 'buy_orders' ? row.list : row.sell_now;
    const label = row.value_source === 'buy_orders' ? 'list' : 'sell now';
    const value = other?.[field];
    return (
        <>
            {main != null && <div>{main}</div>}
            {other && value != null && (
                <InfoTip tip={label === 'sell now' ? 'Selling the drops straight to the highest buy orders.' : 'Listing the cards one cent under the lowest ask and waiting for buyers.'}>
                    <span className={`block text-[10px] font-normal ${signedValue ? signClass(value) : 'text-slate-400'}`}>
                        {label} {signedValue ? signed(value) : money(value)}
                    </span>
                </InfoTip>
            )}
        </>
    );
}

const DEAL_KINDS = {
    sell_now: { label: 'sell now', className: 'text-emerald-300', tip: 'Profitable even selling the drops straight to buy orders — the strongest kind of deal.' },
    list: { label: 'list only', className: 'text-sky-300', tip: 'Profitable only when the cards are listed at the sell price and you wait for buyers; selling straight to buy orders would not cover the game.' },
};

function DealKindBadge({ kind }) {
    const meta = DEAL_KINDS[kind];
    if (!meta) return null;
    return (
        <InfoTip tip={meta.tip}>
            <span className={`block text-[10px] font-semibold whitespace-nowrap ${meta.className}`}>{meta.label}</span>
        </InfoTip>
    );
}

function SourceBadge({ source }) {
    const meta = SOURCES[source] || SOURCES.estimate;
    const { Icon } = meta;
    return (
        <InfoTip tip={meta.tip}>
            <span className={`inline-flex items-center gap-1 text-xs whitespace-nowrap ${meta.className}`}><Icon size={12} />{meta.label}</span>
        </InfoTip>
    );
}

function AccountBreakdown({ row }) {
    const buyers = [...row.buyers].sort((a, b) => (b.profit ?? -1e9) - (a.profit ?? -1e9));
    return (
        <div className="grid md:grid-cols-2 gap-4 px-4 py-3 bg-black/20 text-xs">
            <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">Can buy ({buyers.length})</p>
                {buyers.length === 0 && <p className="text-slate-600">Every account already owns it.</p>}
                <div className="flex flex-wrap gap-1.5">
                    {buyers.map((b) => (
                        <span key={b.account_name} className="px-2 py-1 rounded-md bg-white/5 border border-white/10 text-slate-300">
                            {b.account_name}
                            <span className="text-slate-500"> · {b.country || '??'} {money(b.price)}</span>
                            {b.profit != null && <span className={`ml-1 ${signClass(b.profit)}`}>{signed(b.profit)}</span>}
                            {b.ownership_unknown && <span className="ml-1 text-amber-400/80" title="No fresh session: owned games unknown">?</span>}
                        </span>
                    ))}
                </div>
            </div>
            <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">Already owned ({row.owners.length})</p>
                {row.owners.length === 0 && <p className="text-slate-600">No account owns it yet.</p>}
                <div className="flex flex-wrap gap-1.5">
                    {row.owners.map((o) => (
                        <span key={o.account_name} className="px-2 py-1 rounded-md bg-white/5 border border-white/10 text-slate-400">
                            {o.account_name}
                            <span className={o.drops_remaining ? 'text-amber-300' : 'text-slate-600'}>
                                {' '}· {o.drops_remaining ? `${o.drops_remaining} drops left` : 'no drops left'}
                            </span>
                        </span>
                    ))}
                </div>
                <p className="mt-3 text-slate-500">
                    Set: {row.card_count} cards · average listing {money(row.average_card_price)}
                    {row.set_price != null && <> · full set {money(row.set_price)}</>}
                    {row.copies_per_card_all_accounts != null && <> · all accounts sell ~{row.copies_per_card_all_accounts} of each card</>}
                </p>
            </div>
            {row.cards && (
                <div className="md:col-span-2">
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1.5">Cards</p>
                    <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                        {row.cards.map((card) => (
                            <div key={card.name} className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/5">
                                <span className="text-slate-300 truncate" title={card.name}>{card.name}</span>
                                <span className="shrink-0 tabular-nums text-slate-500">
                                    ask <span className={card.capped ? 'text-amber-400 line-through' : 'text-slate-300'} title={card.capped ? `Odd one-off ask — valued at ${money(card.valued_at)} for listing prices` : ''}>{money(card.lowest_ask)}</span>
                                    {' '}· bid <span className="text-emerald-300">{money(card.highest_bid)}</span>
                                    {card.buy_orders != null && <span> ({card.buy_orders.toLocaleString()} orders)</span>}
                                    {' '}· {card.listings.toLocaleString()} listed
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

const DealsTable = ({ rows, emptyText }) => {
    const [sortKey, setSortKey] = useState('total_profit_all_accounts');
    const [sortDirection, setSortDirection] = useState('desc');
    const [expanded, setExpanded] = useState(() => new Set());

    const sorted = useMemo(() => {
        const value = (row, key) => {
            if (key === 'value_source') return SOURCES[row.value_source]?.rank;
            return row[key];
        };
        const direction = sortDirection === 'asc' ? 1 : -1;
        return [...rows].sort((a, b) => {
            const av = value(a, sortKey);
            const bv = value(b, sortKey);
            if (av == null && bv == null) return 0;
            if (av == null) return 1;      // unknowns always last
            if (bv == null) return -1;
            if (typeof av === 'string') return direction * av.localeCompare(bv);
            return direction * (av - bv);
        });
    }, [rows, sortKey, sortDirection]);

    const toggleSort = (key) => {
        if (key === sortKey) setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
        else { setSortKey(key); setSortDirection(key === 'name' || key === 'value_source' ? 'asc' : 'desc'); }
    };
    const toggleRow = (appId) => setExpanded((current) => {
        const next = new Set(current);
        if (next.has(appId)) next.delete(appId); else next.add(appId);
        return next;
    });

    if (!rows.length) {
        return (
            <div className="rounded-xl border border-white/10 bg-[#0b1119]/85 backdrop-blur-sm text-center text-slate-300 text-sm px-6 py-10">
                {emptyText}
            </div>
        );
    }

    return (
        <div className="flex-1 overflow-auto custom-scrollbar rounded-xl border border-white/10 bg-[#0b1119]/85 backdrop-blur-sm">
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
                    {sorted.map((r) => {
                        const open = expanded.has(r.app_id);
                        return (
                            <Fragment key={r.app_id}>
                                <tr className="border-t border-white/5 hover:bg-white/5">
                                    <td className="px-3 py-2 text-slate-200 max-w-[18rem]">
                                        <div className="flex items-center gap-1.5">
                                            <button type="button" onClick={() => toggleRow(r.app_id)} className="text-slate-500 hover:text-amber-300 shrink-0" aria-label={open ? 'Collapse' : 'Expand'}>
                                                {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                            </button>
                                            <a href={r.links.store} target="_blank" rel="noreferrer" className="truncate hover:text-amber-200" title={r.name}>{r.name}</a>
                                        </div>
                                        <div className="flex gap-2 pl-5 text-[10px] text-slate-500 whitespace-nowrap">
                                            <a href={r.links.steamdb} target="_blank" rel="noreferrer" className="hover:text-slate-300 inline-flex items-center gap-0.5">SteamDB<ExternalLink size={9} /></a>
                                            <a href={r.links.market} target="_blank" rel="noreferrer" className="hover:text-slate-300 inline-flex items-center gap-0.5">Market<ExternalLink size={9} /></a>
                                            <a href={r.links.card_exchange} target="_blank" rel="noreferrer" className="hover:text-slate-300 inline-flex items-center gap-0.5">Card Exchange<ExternalLink size={9} /></a>
                                        </div>
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap"><PriceCell row={r} /></td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-300" title={`${r.card_count} cards in the set`}>{r.card_drops}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-200">
                                        <OtherValuation row={r} field="expected_net" main={money(r.expected_net)} />
                                    </td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.worst_case_profit)}`}>{signed(r.worst_case_profit)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.profit)}`}>
                                        <div>{signed(r.profit)}</div>
                                        <div className="text-[10px] opacity-70">{percent(r.return_percent)}</div>
                                    </td>
                                    <td className={`px-3 py-2 text-right tabular-nums font-semibold ${signClass(r.best_profit)}`}>
                                        <div>{signed(r.best_profit)}</div>
                                        <div className="text-[10px] opacity-70 font-normal">{percent(r.best_return_percent)}</div>
                                        <OtherValuation row={r} field="best_profit" signedValue />
                                    </td>
                                    <td className="px-3 py-2 text-right tabular-nums">
                                        <span className="text-emerald-300">{r.profitable_accounts}</span>
                                        <span className="text-slate-600"> / </span>
                                        <span className="text-slate-400">{r.owners.length}</span>
                                    </td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${signClass(r.total_profit_all_accounts)}`}>{signed(r.total_profit_all_accounts)}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${r.fewest_listings != null && r.fewest_listings < 20 ? 'text-amber-400' : 'text-slate-400'}`}>{r.fewest_listings == null ? '—' : r.fewest_listings.toLocaleString()}</td>
                                    <td className="px-3 py-2 text-center">
                                        <SourceBadge source={r.value_source} />
                                        <DealKindBadge kind={r.deal_kind} />
                                        {r.awaiting_buy_orders && (
                                            <InfoTip tip="Profitable at listing prices, but its buy orders are not checked yet — not a confirmed deal. The next scan checks them.">
                                                <span className="block text-[10px] text-sky-300/80 whitespace-nowrap">awaiting buy orders</span>
                                            </InfoTip>
                                        )}
                                    </td>
                                </tr>
                                {open && (
                                    <tr className="border-t border-white/5">
                                        <td colSpan={COLUMNS.length} className="p-0"><AccountBreakdown row={r} /></td>
                                    </tr>
                                )}
                            </Fragment>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

export default DealsTable;
