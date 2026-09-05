import { useState, useEffect, useRef, useCallback } from 'react';
import { Target, ChevronDown, Plus, Trash2, Search } from 'lucide-react';

// The buy side: the freshly-limited case(s)/item(s) to rotate INTO. Enter a
// capital amount (what dumping the sell list raises) and each target shows how
// many units that buys at the current price. Persisted via POST
// /api/huginn/gjallarhorn/targets; priced via GET (?capital=).
const money = (v) => (v == null ? '—' : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

const TargetBasket = ({ market, defaultCapital }) => {
    const [open, setOpen] = useState(false);
    const [targets, setTargets] = useState([]);      // [{name, price, unitsForCapital}]
    const [capital, setCapital] = useState(defaultCapital || '');
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [count, setCount] = useState(0);
    const ref = useRef(null);

    const refresh = useCallback((cap) => {
        const c = cap ?? capital;
        const q = c ? `?capital=${encodeURIComponent(c)}&market=${market}` : `?market=${market}`;
        return fetch(`/api/huginn/gjallarhorn/targets${q}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
                if (data && Array.isArray(data.rows)) {
                    setTargets(data.rows.map((row) => ({ name: row.item_name, price: row.price, unitsForCapital: row.unitsForCapital })));
                    setCount(data.rows.length);
                }
            })
            .catch(() => {});
    }, [capital, market]);

    useEffect(() => { refresh(); }, [refresh]);

    useEffect(() => {
        const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Item-name typeahead reuses Draupnir's search (whole pulse item universe).
    useEffect(() => {
        if (!open) return undefined;
        const id = setTimeout(() => {
            if (query.trim().length < 2) { setResults([]); return; }
            fetch(`/api/draupnir/portfolios/item-search?q=${encodeURIComponent(query.trim())}`)
                .then((r) => (r.ok ? r.json() : null))
                .then((data) => setResults((data && data.items) ? data.items.slice(0, 8) : []))
                .catch(() => setResults([]));
        }, 200);
        return () => clearTimeout(id);
    }, [query, open]);

    const persist = async (names) => {
        setSaving(true);
        setError(null);
        try {
            const res = await fetch('/api/huginn/gjallarhorn/targets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ targets: names.map((n) => ({ name: n })) }),
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Save failed');
            await refresh();
        } catch (e) {
            setError(e.message);
        } finally {
            setSaving(false);
        }
    };

    const addTarget = (name) => {
        if (targets.some((t) => t.name.toLowerCase() === name.toLowerCase())) return;
        setQuery('');
        setResults([]);
        persist([...targets.map((t) => t.name), name]);
    };
    const removeTarget = (name) => persist(targets.filter((t) => t.name !== name).map((t) => t.name));

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-odin-blue/60 border border-amber-500/20 hover:border-amber-500/40 hover:bg-odin-blue/80 transition-all text-sm text-slate-300"
            >
                <Target size={13} className="text-amber-500/70" /> Target basket
                {count > 0 && <span className="text-[10px] tabular-nums text-amber-400/80">({count})</span>}
                <ChevronDown size={13} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>

            {open && (
                <div className="absolute top-full right-0 mt-1.5 z-50 w-[30rem] bg-[#0d1520] border border-white/10 rounded-xl shadow-2xl shadow-black/60">
                    <div className="px-3 py-2.5 border-b border-white/10">
                        <p className="text-[11px] leading-snug text-slate-400 mb-2">
                            The freshly-limited item(s) to rotate into. Enter your liquidatable capital to
                            see how many each buys now.
                        </p>
                        <label className="flex items-center gap-2 text-xs text-slate-400">
                            Capital $
                            <input
                                type="number" min="0" step="1" value={capital}
                                onChange={(e) => setCapital(e.target.value)}
                                onBlur={() => refresh()}
                                placeholder="e.g. 5000"
                                className="w-28 bg-black/30 border border-white/10 rounded px-2 py-1 text-sm text-right text-slate-200 tabular-nums outline-none focus:border-amber-500/40"
                            />
                        </label>
                    </div>

                    <div className="max-h-[40vh] overflow-y-auto custom-scrollbar py-1">
                        {targets.length === 0 && <div className="px-3 py-3 text-xs text-slate-500">No targets yet — search below to add one.</div>}
                        {targets.map((t) => (
                            <div key={t.name} className="flex items-center gap-2 px-3 py-1.5">
                                <span className="flex-1 text-sm text-slate-200 truncate" title={t.name}>{t.name}</span>
                                <span className="text-xs text-slate-400 tabular-nums w-16 text-right">{money(t.price)}</span>
                                <span className="text-xs tabular-nums w-16 text-right text-amber-300">
                                    {t.unitsForCapital == null ? '—' : `×${t.unitsForCapital.toLocaleString()}`}
                                </span>
                                <button type="button" onClick={() => removeTarget(t.name)} className="text-slate-600 hover:text-red-400"><Trash2 size={13} /></button>
                            </div>
                        ))}
                    </div>

                    <div className="border-t border-white/10 p-2 relative">
                        <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-black/30 border border-white/10">
                            <Search size={13} className="text-slate-500" />
                            <input
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                placeholder="Add a target (e.g. Kilowatt Case)"
                                className="flex-1 bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-600"
                            />
                        </div>
                        {results.length > 0 && (
                            <div className="absolute left-2 right-2 bottom-full mb-1 bg-[#0d1520] border border-white/10 rounded-lg shadow-xl overflow-hidden">
                                {results.map((it) => (
                                    <button
                                        key={it.name}
                                        type="button"
                                        onClick={() => addTarget(it.name)}
                                        className="flex items-center justify-between w-full px-3 py-1.5 text-left text-sm text-slate-300 hover:bg-amber-500/10"
                                    >
                                        <span className="truncate flex items-center gap-1.5"><Plus size={11} className="text-amber-500/70" />{it.name}</span>
                                        {it.price != null && <span className="text-xs text-slate-500 tabular-nums">{money(it.price)}</span>}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    {error && <div className="px-3 py-2 text-xs text-red-400">{error}</div>}
                    {saving && <div className="px-3 py-1.5 text-[11px] text-slate-500">Saving…</div>}
                </div>
            )}
        </div>
    );
};

export default TargetBasket;
