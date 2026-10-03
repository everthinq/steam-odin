import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Tags, LayoutDashboard, Search, RefreshCw, AlertTriangle, Loader2, X, Archive, ShoppingCart } from 'lucide-react';
import { useColumnSort, sortRows } from '../../components/draupnir/columnSort';
import SortHeader from '../../components/ratatoskr/SortHeader';
import SteamMarketLink from '../../components/SteamMarketLink';

// Ratatoskr "Store Catalogue": every item the Counter-Strike 2 in-game store sells, with its
// US dollar price, from the game store's own price sheet. Nothing is bought on this page: an
// item's Buy button opens its buy page (/store-catalogue/buy/<entry>, the guarded purchase of
// Buy Storage Units for that item).
// Backend: /api/ratatoskr/store-catalogue. The sheet is refreshed whenever Buy Storage Units
// reads it; "Read prices again" reads it now (one account logs in to Ratatoskr and out again).

// Sold here: everything but the game license and the Armory Pass (storage_shop_service.py).
const NOT_FOR_SALE = new Set(['Game License', 'XpShopTicket1']);

const SORT_VALUES = {
    item: (i) => i.name,
    category: (i) => i.category,
    store_front: (i) => (i.on_store_front ? 1 : 0),
    price: (i) => i.usd,
};

const dollars = (usd) => (usd == null ? '—' : `$${usd.toFixed(2)}`);
const ago = (epochSeconds) => {
    if (!epochSeconds) return null;
    const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};
// What the shared job is doing when it is not this page's price sheet read.
const BUSY = { plan: 'checking wallets', purchase: 'buying Storage Units', delivery: 'delivering Storage Units' };

const StoreCatalogue = () => {
    const [state, setState] = useState(null);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState('all');
    const [storeFrontOnly, setStoreFrontOnly] = useState(false);
    const [readStartedAt, setReadStartedAt] = useState(null);   // this page's "Read prices again" job
    const sort = useColumnSort();
    const loads = useRef(0);               // only the newest answer is kept

    const load = useCallback(() => {
        const number = ++loads.current;
        return fetch('/api/ratatoskr/store-catalogue')
            .then((r) => r.json())
            .then((d) => { if (number === loads.current) setState(d); })
            .catch(() => setError('Could not reach the backend.'));
    }, []);
    const job = state?.job || {};
    const reading = Boolean(job.running && job.kind === 'price sheet');
    useEffect(() => {
        load();
        const timer = setInterval(load, job.running ? 1500 : 60000);
        return () => clearInterval(timer);
    }, [load, job.running]);

    // The read started here failed: say why (the job keeps its error until the next one).
    const readError = readStartedAt != null && job.started_at === readStartedAt && !job.running ? job.error : null;

    const readAgain = () => {
        setError(null);
        fetch('/api/ratatoskr/store-catalogue/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
            .then((r) => r.json())
            .then((d) => { if (d.started) setReadStartedAt(d.started_at); else setError(d.error); })
            .catch(() => setError('Could not reach the backend.'))
            .finally(load);
    };

    const items = useMemo(() => state?.items || [], [state]);
    const categories = useMemo(() => state?.categories || [], [state]);
    // The table's own order: by category (the backend's order), then as the store lists them.
    const ordered = useMemo(() => {
        const rank = Object.fromEntries(categories.map((c, index) => [c.category, index]));
        return items.map((item, index) => [item, index])
            .sort((a, b) => (rank[a[0].category] ?? 99) - (rank[b[0].category] ?? 99) || a[1] - b[1])
            .map(([item]) => item);
    }, [items, categories]);
    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const matching = ordered.filter((i) => (category === 'all' || i.category === category)
            && (!storeFrontOnly || i.on_store_front)
            && (!needle || i.name.toLowerCase().includes(needle) || i.entry.toLowerCase().includes(needle)));
        return sortRows(matching, sort.sortKey, sort.sortDir, SORT_VALUES);
    }, [ordered, search, category, storeFrontOnly, sort.sortKey, sort.sortDir]);

    if (!state) {
        return <div className="min-h-screen flex items-center justify-center text-slate-400"><Loader2 className="animate-spin mr-2" size={18} /> Loading the Store Catalogue…</div>;
    }
    const busyWith = job.running && !reading ? (BUSY[job.kind] || job.kind) : null;

    return (
        <div className="min-h-screen p-4 md:p-8 text-sm">
            <header className="flex flex-wrap items-center gap-3 mb-6">
                <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30"><Tags size={22} className="text-amber-500" /></div>
                <div className="mr-auto">
                    <h1 className="text-2xl font-bold text-amber-100 font-serif">Store Catalogue</h1>
                    <p className="text-xs text-slate-300 [text-shadow:0_1px_3px_rgb(0_0_0)]">Every item the Counter-Strike 2 in-game store sells, with its US dollar price. Buy opens the item&apos;s own buy page for any of your accounts.</p>
                </div>
                <Link to="/buy-storage-units" className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <Archive size={14} /> Buy Storage Units
                </Link>
                <Link to="/" className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
                    <LayoutDashboard size={14} /> Dashboard
                </Link>
            </header>

            {(error || readError) && (
                <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-950/80 px-3 py-2 text-red-300" role="alert">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span className="flex-1">{error || `The prices could not be read: ${readError}`}</span>
                    <button type="button" onClick={() => { setError(null); setReadStartedAt(null); }} aria-label="Dismiss" className="text-red-300/70 hover:text-red-200"><X size={14} /></button>
                </div>
            )}

            <section className="rounded-xl border border-white/10 bg-slate-950/85 backdrop-blur-md shadow-xl min-w-0" aria-label="Store items">
                <div className="flex flex-wrap items-center gap-2 p-3 border-b border-white/10">
                    <label className="relative flex-1 min-w-[12rem]">
                        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                        <span className="sr-only">Search items</span>
                        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search items"
                            className="w-full bg-black/30 border border-white/10 rounded-lg pl-8 pr-2 py-1.5 text-sm focus:border-amber-500/60 focus:outline-none" />
                    </label>
                    <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer"
                        title="Only what the in-game store front shows (case keys, the game license and the Armory Pass are sold but not shown there)">
                        <input type="checkbox" className="accent-amber-500" checked={storeFrontOnly} onChange={(e) => setStoreFrontOnly(e.target.checked)} />
                        Store front only
                    </label>
                    <span className="text-xs text-slate-500">
                        {state.read_at ? `Prices read ${ago(state.read_at)}` : 'Prices never read'}
                    </span>
                    <button type="button" onClick={readAgain} disabled={Boolean(job.running)}
                        title={busyWith ? `Buy Storage Units is busy (${busyWith}): try again when it finishes`
                            : 'Logs one account in to Ratatoskr, reads the game store price list and logs out (about 15 seconds); nothing is bought'}
                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-white/15 text-slate-300 hover:bg-white/5 disabled:opacity-50 text-xs">
                        <RefreshCw size={13} className={reading ? 'animate-spin' : ''} />
                        {reading ? 'Reading prices…' : 'Read prices again'}
                    </button>
                </div>

                <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-white/10 text-xs" role="tablist" aria-label="Category">
                    {[{ category: 'all', count: items.length }, ...categories].map((c) => (
                        <button key={c.category} type="button" role="tab" aria-selected={category === c.category} onClick={() => setCategory(c.category)}
                            className={`px-2.5 py-1 rounded-lg border ${category === c.category ? 'border-amber-500/40 bg-amber-600/20 text-amber-200' : 'border-white/10 text-slate-400 hover:text-white hover:bg-white/5'}`}>
                            {c.category === 'all' ? 'All' : c.category} <span className="opacity-60">{c.count}</span>
                        </button>
                    ))}
                </div>

                <div className="overflow-auto custom-scrollbar max-h-[70vh]">
                    <table className="w-full">
                        <thead className="sticky top-0 z-10 bg-slate-950 text-[11px] uppercase tracking-wider text-slate-500">
                            <tr>
                                <SortHeader column="item" label="Item" sort={sort} className="py-2 pl-3" resetLabel="the category order" />
                                <SortHeader column="category" label="Category" sort={sort} resetLabel="the category order" />
                                <SortHeader column="store_front" label="Store front" sort={sort} resetLabel="the category order"
                                    title="Whether the in-game store front shows the item" />
                                <SortHeader column="price" label="Price" sort={sort} align="right" resetLabel="the category order"
                                    title="The game store's price in US dollars" />
                                <th className="font-medium text-right pr-3 w-28"><span className="sr-only">Market and buy</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.length === 0 && (
                                <tr><td colSpan={5} className="py-8 text-center text-slate-500">
                                    {items.length ? 'No item matches.' : 'No prices yet: press “Read prices again”.'}
                                </td></tr>
                            )}
                            {visible.map((i) => (
                                <tr key={i.entry} className="border-t border-white/5 hover:bg-white/[0.03]">
                                    <td className="py-1.5 pl-3">
                                        <span className="text-slate-100">{i.name}</span>
                                        {!i.named && <span className="ml-2 text-[10px] text-slate-500" title={`Not in Ratatoskr's item list yet; the store calls it “${i.entry}”`}>new</span>}
                                    </td>
                                    <td className="text-slate-400 text-xs">{i.category}</td>
                                    <td className="text-xs">{i.on_store_front ? <span className="text-emerald-300">shown</span> : <span className="text-slate-500">not shown</span>}</td>
                                    <td className="text-right tabular-nums text-slate-100">{dollars(i.usd)}</td>
                                    <td className="text-right pr-3 whitespace-nowrap">
                                        {i.market_url && <SteamMarketLink itemName={i.name} className="mr-2 align-middle" />}
                                        {!NOT_FOR_SALE.has(i.entry) && i.definition_index != null && (
                                            <Link to={`/store-catalogue/buy/${encodeURIComponent(i.entry)}`} title={`Buy ${i.name} on any of your accounts`}
                                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-amber-500/30 text-amber-200 text-xs hover:bg-amber-500/10">
                                                <ShoppingCart size={11} /> Buy
                                            </Link>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="px-3 py-2 border-t border-white/10 text-[11px] text-slate-500">
                    {visible.length} of {items.length} items. The game store also has a price in each wallet currency; this page shows the US dollar one.
                    Cases on the store front only link to the Steam Market: the store does not sell them.
                </p>
            </section>
        </div>
    );
};

export default StoreCatalogue;
