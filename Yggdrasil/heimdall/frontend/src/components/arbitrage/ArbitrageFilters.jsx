import { useState, useMemo, useRef, useEffect } from 'react';
import { Filter, X, Search, DollarSign, Users, Layers } from 'lucide-react';
import { matchesSearchQuery } from '../../utils/transferItems';
import { EMPTY_FILTERS, DEFAULT_PRICE_SIDES, parsePrice, hasPriceFilter, activeFilterCount, priceRangeLabel } from '../../utils/arbitrageFilters';

// Filters for the Huginn tables (Arbitrage, Case Arbitrage, Cross-Profile, Harvest):
// a price range (one of two price sides), the accounts that hold the item and its
// collection. One "Filters" button opens a tabbed panel; the active filters also
// show as removable chips under the toolbar (ActiveFilterChips), so a narrowed
// table never looks like missing data. Each tab names its own price sides
// (`priceSides`, see DEFAULT_PRICE_SIDES) and what an option's count means.

const PRICE_PRESETS = [
    { label: 'Under $1', min: '', max: '1' },
    { label: '$1 – $10', min: '1', max: '10' },
    { label: '$10 – $100', min: '10', max: '100' },
    { label: '$100 +', min: '100', max: '' },
];

// Searchable checkbox list with "Select all" / "Clear"; each option shows how many
// rows of the current table it matches (`countTitle` says what they are).
const CheckList = ({ options, selected, onChange, placeholder, emptyHint, countTitle }) => {
    const [search, setSearch] = useState('');
    const visible = useMemo(
        () => (search.trim() ? options.filter(o => matchesSearchQuery([o.name], search)) : options),
        [options, search]);
    const toggle = (name) => onChange(selected.includes(name) ? selected.filter(n => n !== name) : [...selected, name]);

    if (options.length === 0) {
        return <p className="text-xs text-slate-500 text-center py-8 px-4">{emptyHint}</p>;
    }
    return (
        <>
            <div className="px-3 py-2 border-b border-white/5">
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-black/40 border border-white/10 focus-within:border-amber-500/40">
                    <Search size={12} className="text-slate-500 shrink-0" />
                    <input
                        type="text"
                        autoFocus
                        placeholder={placeholder}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="w-full bg-transparent text-xs text-white outline-none placeholder:text-slate-600"
                    />
                </div>
            </div>
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 text-[10px]">
                <button
                    type="button"
                    onClick={() => onChange([...new Set([...selected, ...visible.map(o => o.name)])])}
                    disabled={visible.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Select all{search.trim() ? ' shown' : ''}
                </button>
                <button
                    type="button"
                    onClick={() => onChange([])}
                    disabled={selected.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Clear
                </button>
            </div>
            <div className="overflow-y-auto custom-scrollbar max-h-64 py-1">
                {visible.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-6 px-3">Nothing matches.</p>
                ) : visible.map(({ name, count }) => {
                    const checked = selected.includes(name);
                    return (
                        <label
                            key={name}
                            className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer text-xs transition-colors ${checked ? 'bg-amber-500/10 text-white' : 'text-slate-300 hover:bg-white/5'}`}
                        >
                            <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggle(name)}
                                className="rounded border-white/20 bg-black/40 text-amber-500 focus:ring-amber-500/40 focus:ring-offset-0"
                            />
                            <span className="truncate flex-1" title={name}>{name}</span>
                            <span className={`tabular-nums shrink-0 ${count ? 'text-slate-400' : 'text-slate-600'}`}
                                title={countTitle}>{count}</span>
                        </label>
                    );
                })}
            </div>
        </>
    );
};

const PriceSection = ({ filters, onChange, realDollarsNote, priceSides }) => {
    const set = (patch) => onChange({ ...filters, ...patch });
    const min = parsePrice(filters.minPrice);
    const max = parsePrice(filters.maxPrice);
    const inverted = min !== null && max !== null && min > max;
    const input = (key, placeholder) => (
        <div className="flex-1 flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-black/40 border border-white/10 focus-within:border-amber-500/40">
            <DollarSign size={12} className="text-slate-500 shrink-0" />
            <input
                type="text"
                inputMode="decimal"
                placeholder={placeholder}
                value={filters[key]}
                onChange={(e) => set({ [key]: e.target.value })}
                className="w-full bg-transparent text-sm text-white tabular-nums outline-none placeholder:text-slate-600"
            />
        </div>
    );
    return (
        <div className="p-3 space-y-3">
            <div>
                <p className="text-[10px] font-bold tracking-widest uppercase text-slate-500 mb-1.5">Price of</p>
                <div className="grid grid-cols-2 gap-1 p-0.5 rounded-lg bg-black/40 border border-white/10">
                    {priceSides.map(([side, label, tip]) => (
                        <button
                            key={side}
                            type="button"
                            title={tip}
                            onClick={() => set({ priceSide: side })}
                            className={`px-2 py-1 rounded-md text-xs font-medium transition-colors ${filters.priceSide === side ? 'bg-amber-500/20 text-amber-200' : 'text-slate-400 hover:text-white'}`}
                        >
                            {label}
                        </button>
                    ))}
                </div>
            </div>
            <div>
                <p className="text-[10px] font-bold tracking-widest uppercase text-slate-500 mb-1.5">Range</p>
                <div className="flex items-center gap-2">
                    {input('minPrice', 'Min')}
                    <span className="text-slate-600 text-xs">to</span>
                    {input('maxPrice', 'Max')}
                </div>
                {inverted && <p className="text-[11px] text-red-400 mt-1.5">Min is above max, so nothing can match.</p>}
            </div>
            <div className="flex flex-wrap gap-1.5">
                {PRICE_PRESETS.map(p => {
                    const on = filters.minPrice === p.min && filters.maxPrice === p.max;
                    return (
                        <button
                            key={p.label}
                            type="button"
                            onClick={() => set(on ? { minPrice: '', maxPrice: '' } : { minPrice: p.min, maxPrice: p.max })}
                            className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${on ? 'bg-amber-500/15 border-amber-500/40 text-amber-200' : 'border-white/10 text-slate-400 hover:text-white hover:border-white/20'}`}
                        >
                            {p.label}
                        </button>
                    );
                })}
            </div>
            <div className="flex items-center justify-between">
                <p className="text-[10px] text-slate-500 leading-snug pr-2">
                    {realDollarsNote ? 'In real dollars (SkinSwap Trade page prices divided by 1.4).' : 'US dollars, both ends included.'}
                </p>
                <button
                    type="button"
                    onClick={() => set({ minPrice: '', maxPrice: '' })}
                    disabled={!hasPriceFilter(filters)}
                    className="text-[10px] text-slate-400 hover:text-white disabled:opacity-40 shrink-0"
                >
                    Clear
                </button>
            </div>
        </div>
    );
};

const DEFAULT_SCAN_HINT = 'Press "Get all items" on the Arbitrage tab first: accounts and collections come from the inventory scan.';

const ArbitrageFilters = ({
    filters, onChange, accountOptions, collectionOptions, hasScan, realDollarsNote,
    priceSides = DEFAULT_PRICE_SIDES,
    countTitle = 'Deals of this profile it holds',
    scanHint = DEFAULT_SCAN_HINT,
    accountsEmptyHint = 'No account holds anything yet.',
    accountsNeedScan = true,
}) => {
    const [open, setOpen] = useState(false);
    const [tab, setTab] = useState('price');
    const ref = useRef(null);
    const count = activeFilterCount(filters);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);

    const tabs = [
        { id: 'price', label: 'Price', icon: DollarSign, badge: hasPriceFilter(filters) ? 1 : 0 },
        { id: 'accounts', label: 'Accounts', icon: Users, badge: filters.accounts.length },
        { id: 'collections', label: 'Collections', icon: Layers, badge: filters.collections.length },
    ];

    return (
        <div className="relative shrink-0" ref={ref}>
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
                title="Filter by price, account or collection"
                className={`flex items-center gap-1.5 text-xs border rounded-lg px-2.5 py-1.5 transition-colors ${count > 0
                    ? 'border-amber-500/40 text-amber-300 bg-amber-500/10 hover:bg-amber-500/15'
                    : 'text-slate-400 border-white/10 hover:text-white hover:bg-white/5'}`}
            >
                <Filter size={14} />
                Filters
                {count > 0 && <span className="min-w-[1.1rem] px-1 rounded-full bg-amber-500/30 text-amber-100 text-[10px] font-bold tabular-nums">{count}</span>}
            </button>
            {open && (
                <div className="absolute top-full left-0 mt-2 z-50 w-[22rem] flex flex-col bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl overflow-hidden">
                    <div className="grid grid-cols-3 border-b border-white/10">
                        {tabs.map(({ id, label, icon: Icon, badge }) => (
                            <button
                                key={id}
                                type="button"
                                onClick={() => setTab(id)}
                                className={`flex items-center justify-center gap-1.5 px-2 py-2.5 text-xs font-medium border-b-2 transition-colors ${tab === id ? 'border-amber-400 text-white bg-white/[0.03]' : 'border-transparent text-slate-400 hover:text-white'}`}
                            >
                                <Icon size={12} />
                                {label}
                                {badge > 0 && <span className="px-1 rounded-full bg-amber-500/30 text-amber-100 text-[10px] font-bold tabular-nums">{badge}</span>}
                            </button>
                        ))}
                    </div>
                    {tab === 'price' && <PriceSection filters={filters} onChange={onChange} realDollarsNote={realDollarsNote} priceSides={priceSides} />}
                    {tab === 'accounts' && (
                        <CheckList
                            key="accounts"
                            options={accountOptions}
                            selected={filters.accounts}
                            onChange={(accounts) => onChange({ ...filters, accounts })}
                            placeholder="Search accounts"
                            emptyHint={hasScan || !accountsNeedScan ? accountsEmptyHint : scanHint}
                            countTitle={countTitle}
                        />
                    )}
                    {tab === 'collections' && (
                        <CheckList
                            key="collections"
                            options={collectionOptions}
                            selected={filters.collections}
                            onChange={(collections) => onChange({ ...filters, collections })}
                            placeholder="Search collections"
                            emptyHint={hasScan ? 'No collections in your inventory scan.' : scanHint}
                            countTitle={countTitle}
                        />
                    )}
                    {count > 0 && (
                        <div className="flex justify-end px-3 py-2 border-t border-white/10">
                            <button type="button" onClick={() => onChange({ ...EMPTY_FILTERS, priceSide: filters.priceSide })}
                                className="text-xs text-slate-400 hover:text-white">
                                Clear all filters
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

const Chip = ({ children, onRemove, title }) => (
    <span title={title} className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 border border-amber-500/30 pl-2.5 pr-1 py-0.5 text-xs text-amber-200">
        <span className="truncate max-w-[14rem]">{children}</span>
        <button type="button" onClick={onRemove} aria-label="Remove filter" className="p-0.5 rounded-full text-amber-300/70 hover:text-white hover:bg-white/10">
            <X size={11} />
        </button>
    </span>
);

// One chip per active filter (collections fold into one chip past three), each removable.
export const ActiveFilterChips = ({ filters, onChange, priceSides = DEFAULT_PRICE_SIDES }) => {
    if (activeFilterCount(filters) === 0) return null;
    const without = (key, name) => onChange({ ...filters, [key]: filters[key].filter(n => n !== name) });
    const manyCollections = filters.collections.length > 3;
    return (
        <div className="shrink-0 flex items-center gap-1.5 flex-wrap px-4 py-2 border-b border-white/5 bg-amber-500/[0.03]">
            <span className="text-[10px] font-bold tracking-widest uppercase text-slate-500 mr-1">Filtered by</span>
            {hasPriceFilter(filters) && (
                <Chip onRemove={() => onChange({ ...filters, minPrice: '', maxPrice: '' })}>{priceRangeLabel(filters, priceSides)}</Chip>
            )}
            {filters.accounts.map(name => (
                <Chip key={`account-${name}`} title="Held on this account" onRemove={() => without('accounts', name)}>
                    <Users size={10} className="inline -mt-0.5 mr-1" />{name}
                </Chip>
            ))}
            {manyCollections ? (
                <Chip title={filters.collections.join('\n')} onRemove={() => onChange({ ...filters, collections: [] })}>
                    {filters.collections.length} collections
                </Chip>
            ) : filters.collections.map(name => (
                <Chip key={`collection-${name}`} onRemove={() => without('collections', name)}>{name}</Chip>
            ))}
            <button type="button" onClick={() => onChange({ ...EMPTY_FILTERS, priceSide: filters.priceSide })}
                className="ml-1 text-xs text-slate-400 hover:text-white underline-offset-2 hover:underline">
                Clear all
            </button>
        </div>
    );
};

export default ArbitrageFilters;
