import { useState, useMemo, useRef, useEffect } from 'react';
import { Filter } from 'lucide-react';
import { matchesSearchQuery } from '../utils/transferItems';

/**
 * Searchable, multi-select collection dropdown. Extracted from Ratatoskr Transfer
 * so Huginn uses the exact same control.
 *
 * `CollectionFilterPanel` is the raw dropdown (caller owns open/search state).
 * `CollectionFilter` (default) wraps it with a Filter button + its own open/search
 * state and outside-click handling — drop-in for a toolbar.
 */
export function CollectionFilterPanel({
    collections,
    selected,
    search,
    onSearchChange,
    onToggle,
    onSelectAll,
    onClearSelection,
}) {
    const filtered = useMemo(() => {
        if (!search.trim()) return collections;
        return collections.filter((c) => matchesSearchQuery([c], search));
    }, [collections, search]);

    return (
        <div className="absolute top-full left-0 mt-2 z-50 w-80 flex flex-col bg-[#1a1d24] border border-white/10 rounded-xl shadow-2xl overflow-hidden">
            <div className="px-3 py-2.5 border-b border-white/10">
                <p className="text-xs font-medium text-white">Collections</p>
                <p className="text-[10px] text-slate-500 mt-0.5">
                    {selected.length === 0
                        ? 'All collections shown. Check to narrow the list.'
                        : `Showing ${selected.length} collection${selected.length === 1 ? '' : 's'}.`}
                </p>
            </div>
            <div className="px-3 py-2 border-b border-white/5">
                <input
                    type="text"
                    placeholder="Search collections"
                    value={search}
                    onChange={(e) => onSearchChange(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                />
            </div>
            <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 text-[10px]">
                <button
                    type="button"
                    onClick={onSelectAll}
                    disabled={filtered.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Select all
                </button>
                <button
                    type="button"
                    onClick={onClearSelection}
                    disabled={selected.length === 0}
                    className="text-slate-400 hover:text-white disabled:opacity-40"
                >
                    Clear
                </button>
            </div>
            <div className="overflow-y-auto custom-scrollbar max-h-56 py-1">
                {filtered.length === 0 ? (
                    <p className="text-xs text-slate-500 text-center py-6 px-3">No collections match.</p>
                ) : (
                    filtered.map((name) => {
                        const checked = selected.includes(name);
                        return (
                            <label
                                key={name}
                                className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer text-xs transition-colors ${checked ? 'bg-amber-500/10 text-white' : 'text-slate-300 hover:bg-white/5'}`}
                            >
                                <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => onToggle(name)}
                                    className="rounded border-white/20 bg-black/40 text-amber-500 focus:ring-amber-500/40 focus:ring-offset-0"
                                />
                                <span className="truncate" title={name}>{name}</span>
                            </label>
                        );
                    })
                )}
            </div>
        </div>
    );
}

export default function CollectionFilter({ collections, selected, onChange, disabledHint }) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const ref = useRef(null);
    const disabled = collections.length === 0;

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open]);

    const toggle = (name) =>
        onChange(selected.includes(name) ? selected.filter((c) => c !== name) : [...selected, name]);

    const selectAllVisible = () => {
        const visible = search.trim()
            ? collections.filter((c) => matchesSearchQuery([c], search))
            : collections;
        onChange(Array.from(new Set([...selected, ...visible])));
    };

    return (
        <div className="relative shrink-0" ref={ref}>
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                disabled={disabled}
                title={disabled ? (disabledHint || 'No collections available') : 'Filter by collection'}
                aria-expanded={open}
                className={`flex items-center gap-1.5 text-xs border rounded-lg px-2.5 py-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${selected.length > 0
                    ? 'border-amber-500/40 text-amber-300 bg-amber-500/10 hover:bg-amber-500/15'
                    : 'text-slate-400 border-white/10 hover:text-white hover:bg-white/5'}`}
            >
                <Filter size={14} />
                {selected.length} Filter{selected.length === 1 ? '' : 's'}
            </button>
            {open && !disabled && (
                <CollectionFilterPanel
                    collections={collections}
                    selected={selected}
                    search={search}
                    onSearchChange={setSearch}
                    onToggle={toggle}
                    onSelectAll={selectAllVisible}
                    onClearSelection={() => onChange([])}
                />
            )}
        </div>
    );
}
