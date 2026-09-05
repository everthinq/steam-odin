import { useState, useEffect, useRef } from 'react';
import { ArrowRight, ChevronDown, Search } from 'lucide-react';
import MarketBadge from './MarketBadge';

// Group profiles by their buy market ("from"), preserving array order, so the picker
// can show tidy sections instead of one long flat list as profiles multiply.
const groupProfiles = (profiles) => {
    const groups = [];
    const byFrom = {};
    for (const p of profiles) {
        if (!byFrom[p.from]) { byFrom[p.from] = { from: p.from, items: [] }; groups.push(byFrom[p.from]); }
        byFrom[p.from].items.push(p);
    }
    return groups;
};

// Dropdown picker of buy→sell arbitrage profiles, grouped by buy market. From Arbitrage.jsx.
const ProfilePicker = ({ profiles, value, onChange }) => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const ref = useRef(null);
    const active = profiles.find(p => p.id === value) ?? profiles[0];

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Filter across buy/sell names + subs; the generated list runs to hundreds of pairs.
    const q = query.trim().toLowerCase();
    const filtered = q
        ? profiles.filter(p => `${p.from} ${p.fromSub} ${p.to} ${p.toSub}`.toLowerCase().includes(q))
        : profiles;

    const pick = (id) => { onChange(id); setOpen(false); setQuery(''); };

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                onClick={() => setOpen(o => !o)}
                className="flex items-center gap-2.5 px-3 py-2 rounded-xl bg-odin-blue/60 border border-amber-500/20 hover:border-amber-500/40 hover:bg-odin-blue/80 transition-all text-sm"
            >
                <MarketBadge name={active.from} sub={active.fromSub} />
                <ArrowRight size={13} className="text-amber-500/60 shrink-0" />
                <MarketBadge name={active.to} sub={active.toSub} />
                <ChevronDown size={13} className={`text-slate-500 ml-1 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full left-0 mt-1.5 z-50 min-w-[17rem] bg-[#0d1520] border border-white/10 rounded-xl shadow-2xl shadow-black/60">
                    <div className="p-1.5 border-b border-white/10">
                        <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-black/30">
                            <Search size={13} className="text-slate-500 shrink-0" />
                            <input
                                autoFocus
                                type="text"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="Search markets…"
                                className="w-full bg-transparent text-sm text-slate-200 placeholder:text-slate-600 outline-none"
                            />
                        </div>
                    </div>
                    <div className="max-h-[60vh] overflow-y-auto custom-scrollbar py-1">
                        {filtered.length === 0 && (
                            <div className="px-4 py-3 text-xs text-slate-500">No matching pairs</div>
                        )}
                        {groupProfiles(filtered).map(group => (
                            <div key={group.from}>
                                <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                                    Buy on {group.from}
                                </div>
                                {group.items.map(p => {
                                    const isActive = p.id === value;
                                    return (
                                        <button
                                            key={p.id}
                                            type="button"
                                            onClick={() => pick(p.id)}
                                            className={`w-full flex items-center gap-2 pl-5 pr-4 py-2 text-sm transition-colors text-left ${isActive ? 'bg-amber-500/10 text-white' : 'hover:bg-white/[0.04] text-slate-300'}`}
                                        >
                                            <ArrowRight size={12} className={isActive ? 'text-amber-500/60 shrink-0' : 'text-slate-600 shrink-0'} />
                                            <MarketBadge name={p.to} sub={p.toSub} dim={!isActive} />
                                            {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />}
                                        </button>
                                    );
                                })}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ProfilePicker;
