import { useState, useEffect, useRef } from 'react';
import { ArrowRight, ChevronDown, Pin, Search, Zap } from 'lucide-react';
import MarketBadge from './MarketBadge';
import NotInPulseNote from './NotInPulseNote';
import ReliabilityMark from './ReliabilityMark';

// Group profiles by their buy market ("from"), preserving array order, so the picker
// can show tidy sections instead of one long flat list as profiles multiply.
const groupProfiles = (profiles) => {
    const groups = [];
    const byFrom = {};
    for (const p of profiles) {
        if (!byFrom[p.from]) { byFrom[p.from] = { from: p.from, notInPulseUi: !!p.fromNotInPulseUi, priceNote: p.fromPriceNote, reliability: p.fromReliability, items: [] }; groups.push(byFrom[p.from]); }
        byFrom[p.from].items.push(p);
    }
    return groups;
};

// While searching, the sections whose buy market (or Harvest account) matches the
// query come first: typing "skinswap (t" should land on "Buy on SkinSwap (Trade)",
// not on every "Buy on X → SkinSwap (Trade)" pair above it. A name that starts with
// the query beats one that only contains it; ties keep their original order.
const groupRank = (from, q) => {
    const name = from.toLowerCase();
    if (name.startsWith(q)) return 0;
    if (name.includes(q)) return 1;
    return 2;
};

const rankGroups = (groups, q) => q
    ? groups.map((g, i) => ({ g, i, rank: groupRank(g.from, q) }))
        .sort((a, b) => a.rank - b.rank || a.i - b.i)
        .map(x => x.g)
    : groups;

// Selling into a buy order (autobuy) is instant; selling at the lowest listing (min)
// means listing the item and waiting for a buyer.
const isInstant = (p) => p.toSub === 'autobuy';

const SubHeading = ({ instant }) => (
    <div className={`flex items-center gap-1 pl-5 pr-4 pt-1.5 pb-0.5 text-[9px] font-semibold uppercase tracking-wider ${instant ? 'text-emerald-400/70' : 'text-slate-600'}`}>
        {instant && <Zap size={10} />}
        {instant ? 'Sell instantly · buy orders' : 'List for sale · lowest price'}
    </div>
);

// Dropdown picker of buy→sell arbitrage profiles, grouped by buy market. From Arbitrage.jsx.
// `groupLabel` names each section (default "Buy on <market>"); Harvest uses the account.
// A section holding both kinds is split into "sell instantly" (the target has buy
// orders) and "list for sale"; the "Instant sell only" switch hides the second kind.
// `activeIds` (optional): the active profiles, marked with a pin and listed first under "Active".
const ProfilePicker = ({ profiles, value, onChange, groupLabel = (from) => `Buy on ${from}`, searchPlaceholder = 'Search markets…', activeIds = null }) => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [instantOnly, setInstantOnly] = useState(false);
    const ref = useRef(null);
    const listRef = useRef(null);
    const active = profiles.find(p => p.id === value) ?? profiles[0];
    const hasBothKinds = profiles.some(isInstant) && profiles.some(p => !isInstant(p));

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Filter across buy/sell names + subs; the generated list runs to hundreds of pairs.
    const q = query.trim().toLowerCase();
    const filtered = profiles.filter(p =>
        (!q || `${p.from} ${p.fromSub} ${p.to} ${p.toSub}`.toLowerCase().includes(q))
        && (!instantOnly || !hasBothKinds || isInstant(p)));

    const pick = (id) => { onChange(id); setOpen(false); setQuery(''); };

    const activeProfiles = filtered.filter(p => activeIds?.has(p.id));

    const row = (p, withFrom = false) => {
        const isActive = p.id === value;
        return (
            <button
                key={p.id}
                type="button"
                onClick={() => pick(p.id)}
                className={`w-full flex items-center gap-2 pl-5 pr-4 py-2 text-sm transition-colors text-left ${withFrom ? 'whitespace-nowrap' : ''} ${isActive ? 'bg-amber-500/10 text-white' : 'hover:bg-white/[0.04] text-slate-300'}`}
            >
                {withFrom && <MarketBadge name={p.from} sub={p.fromSub} dim={!isActive} />}
                <ArrowRight size={12} className={isActive ? 'text-amber-500/60 shrink-0' : 'text-slate-600 shrink-0'} />
                <MarketBadge name={p.to} sub={p.toSub} dim={!isActive} />
                {p.toNotInPulseUi && <NotInPulseNote extra={p.toPriceNote} />}
                <ReliabilityMark mark={p.toReliability} side={p.toReliabilitySide} />
                <span className="ml-auto flex items-center gap-1.5 shrink-0">
                    {activeIds?.has(p.id) && <Pin size={11} className="text-sky-400" aria-label="Active" />}
                    {isActive && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />}
                </span>
            </button>
        );
    };

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
                className="flex items-center gap-2.5 px-3 py-2 rounded-xl bg-odin-blue/60 border border-amber-500/20 hover:border-amber-500/40 hover:bg-odin-blue/80 transition-all text-sm"
            >
                <MarketBadge name={active.from} sub={active.fromSub} />
                {active.fromNotInPulseUi && <NotInPulseNote extra={active.fromPriceNote} />}
                <ReliabilityMark mark={active.fromReliability} side="listings" />
                <ArrowRight size={13} className="text-amber-500/60 shrink-0" />
                {hasBothKinds && isInstant(active) && <Zap size={12} className="text-emerald-400/70 shrink-0" />}
                <MarketBadge name={active.to} sub={active.toSub} />
                {active.toNotInPulseUi && <NotInPulseNote extra={active.toPriceNote} />}
                <ReliabilityMark mark={active.toReliability} side={active.toReliabilitySide} />
                <ChevronDown size={13} className={`text-slate-500 ml-1 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full left-0 mt-1.5 z-50 min-w-[17rem] w-max max-w-[min(32rem,90vw)] bg-[#0d1520] border border-white/10 rounded-xl shadow-2xl shadow-black/60">
                    <div className="p-1.5 border-b border-white/10 space-y-1.5">
                        <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-black/30">
                            <Search size={13} className="text-slate-500 shrink-0" />
                            <input
                                autoFocus
                                type="text"
                                value={query}
                                onChange={e => { setQuery(e.target.value); if (listRef.current) listRef.current.scrollTop = 0; }}
                                placeholder={searchPlaceholder}
                                className="w-full bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
                            />
                        </div>
                        {hasBothKinds && (
                            <button
                                type="button"
                                onClick={() => setInstantOnly(v => !v)}
                                title="Show only markets you can sell into instantly (they have buy orders)"
                                className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs transition-colors ${instantOnly ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30' : 'text-slate-400 border border-white/10 hover:text-slate-200'}`}
                            >
                                <Zap size={11} /> Instant sell only
                            </button>
                        )}
                    </div>
                    <div ref={listRef} className="max-h-[60vh] overflow-y-auto custom-scrollbar py-1">
                        {filtered.length === 0 && (
                            <div className="px-4 py-3 text-xs text-slate-500">No matching pairs</div>
                        )}
                        {activeProfiles.length > 0 && (
                            <div className="pb-1 mb-1 border-b border-white/5">
                                <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-sky-400/80"
                                    title="The profiles you trade on: only their prices are downloaded, in the background, so they open at once">
                                    <Pin size={10} /> Active
                                </div>
                                {activeProfiles.map(p => row(p, true))}
                            </div>
                        )}
                        {rankGroups(groupProfiles(filtered), q).map(group => {
                            const instant = group.items.filter(isInstant);
                            const listing = group.items.filter(p => !isInstant(p));
                            const split = instant.length > 0 && listing.length > 0;
                            return (
                                <div key={group.from}>
                                    <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                                        {groupLabel(group.from)}
                                        {group.notInPulseUi && <NotInPulseNote size={11} extra={group.priceNote} />}
                                        <ReliabilityMark mark={group.reliability} side="listings" size={11} />
                                    </div>
                                    {split ? (
                                        <>
                                            <SubHeading instant />
                                            {instant.map(p => row(p))}
                                            <SubHeading instant={false} />
                                            {listing.map(p => row(p))}
                                        </>
                                    ) : group.items.map(p => row(p))}
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ProfilePicker;
