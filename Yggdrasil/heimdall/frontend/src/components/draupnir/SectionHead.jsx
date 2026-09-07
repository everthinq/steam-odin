import { ChevronDown, Search } from 'lucide-react';
import CollectionFilter from '../CollectionFilter';

/**
 * Shared collapsible section header for Draupnir's Holdings / Transactions tables.
 *
 * The per-account page (Portfolio.jsx) is the visual reference: the search box sits
 * immediately after the title on the LEFT, with the optional collection filter right
 * beside it. The combined ledger imports this very same component, so the two pages
 * can never drift apart again (they used to keep separate copies — the combined one
 * pushed its search to the right with `ml-auto`).
 *
 * Collection filter is opt-in: pass `collections` (even an empty array) to render it.
 * When the array is empty the underlying CollectionFilter disables itself and shows
 * `collectionHint` — the same "no data yet" behaviour Huginn Arbitrage uses before an
 * inventory scan exists.
 */
export default function SectionHead({
    title, open, onToggle, count, total, search, setSearch, placeholder,
    collections, selectedCollections, onCollectionsChange, collectionHint,
}) {
    return (
        <div className="flex items-center gap-3 mb-2 flex-wrap">
            <button onClick={onToggle} className="flex items-center gap-1.5 text-[11px] font-bold tracking-widest text-slate-400 uppercase hover:text-slate-200 transition-colors">
                <ChevronDown size={14} className={`transition-transform ${open ? '' : '-rotate-90'}`} />
                {title}
                <span className="text-slate-600 font-normal normal-case tracking-normal">
                    {count === total ? total : `${count} of ${total}`}
                </span>
            </button>
            {open && (
                <>
                    <div className="relative w-full sm:w-64">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                        <input
                            value={search} onChange={e => setSearch(e.target.value)} placeholder={placeholder}
                            className="w-full bg-odin-dark/60 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-yellow-500/50 placeholder:text-slate-600"
                        />
                    </div>
                    {collections && (
                        <CollectionFilter
                            collections={collections}
                            selected={selectedCollections}
                            onChange={onCollectionsChange}
                            disabledHint={collectionHint}
                        />
                    )}
                </>
            )}
        </div>
    );
}
