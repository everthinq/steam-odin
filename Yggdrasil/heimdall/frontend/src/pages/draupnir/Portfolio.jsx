import React, { useState, useEffect, useCallback, useRef, useMemo, useDeferredValue } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { LayoutDashboard, Coins, RefreshCw, Pencil, Trash2, Plus, X, Check, Search, ChevronDown, Copy } from 'lucide-react';
import { matchesSearchQuery } from '../../utils/transferItems';
import { ConfirmDialog, PromptDialog } from '../../components/DraupnirDialog';
import { DateField } from '../../components/DateField';

const MARKETS = [
    { id: 'steam', label: 'Steam' },
    { id: 'csfloat', label: 'CSFloat' },
    { id: 'buff', label: 'Buff163' },
    { id: 'lowest', label: 'Lowest' },
];

// Tables can get very long (thousands of rows after an import), so each is
// collapsible and paginated — only PAGE_SIZE rows paint until "Load more".
const PAGE_SIZE = 10;

// Item icons are looked up by market_hash_name (same source Ratatoskr uses) —
// no icon hash needed, which suits Draupnir since CSV imports only give a name.
// loading="lazy" means only rows actually on screen fetch an image.
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

const todayStr = () => new Date().toISOString().slice(0, 10);   // YYYY-MM-DD
const blankForm = () => ({ item_name: '', type: 'buy', qty: 1, price: '', platform: '', date: todayStr(), note: '' });

const Tile = ({ label, value, cls = 'text-slate-100' }) => (
    <div className="bg-odin-blue/70 border border-white/10 rounded-xl px-4 py-3 shadow-sm">
        <p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase mb-1">{label}</p>
        <p className={`text-lg font-semibold tabular-nums ${cls}`}>{value}</p>
    </div>
);

// Collapsible section header with a filter box (the "best of" Huginn + Ratatoskr:
// token search + a count). Clicking the title toggles the body.
const SectionHead = ({ title, open, onToggle, count, total, search, setSearch, placeholder }) => (
    <div className="flex items-center gap-3 mb-2 flex-wrap">
        <button onClick={onToggle} className="flex items-center gap-1.5 text-[11px] font-bold tracking-widest text-slate-400 uppercase hover:text-slate-200 transition-colors">
            <ChevronDown size={14} className={`transition-transform ${open ? '' : '-rotate-90'}`} />
            {title}
            <span className="text-slate-600 font-normal normal-case tracking-normal">
                {count === total ? total : `${count} of ${total}`}
            </span>
        </button>
        {open && (
            <div className="relative w-full sm:w-64">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" size={14} />
                <input
                    value={search} onChange={e => setSearch(e.target.value)} placeholder={placeholder}
                    className="w-full bg-odin-dark/60 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-yellow-500/50 placeholder:text-slate-600"
                />
            </div>
        )}
    </div>
);

const LoadMore = ({ visible, total, onMore }) => visible < total && (
    <button onClick={onMore} className="w-full mt-2 py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-sm text-slate-300 transition-colors">
        Load {Math.min(PAGE_SIZE, total - visible)} more <span className="text-slate-500 ml-1">({total - visible} left)</span>
    </button>
);

const DraupnirPortfolio = () => {
    const { portfolioId } = useParams();
    const navigate = useNavigate();
    const [data, setData] = useState(null);
    const [market, setMarket] = useState('steam');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [form, setForm] = useState(blankForm());
    const [editingId, setEditingId] = useState(null);
    const [dialog, setDialog] = useState(null);   // styled confirm/prompt replacing native alerts
    const [formError, setFormError] = useState(null);
    const [checking, setChecking] = useState(false);
    const [itemSuggests, setItemSuggests] = useState([]);
    const [showSuggest, setShowSuggest] = useState(false);
    const [highlightIdx, setHighlightIdx] = useState(-1);   // keyboard-nav position in the suggestion list
    const [flashId, setFlashId] = useState(null);           // transaction row to flash green after an add
    const suggestTimer = useRef(null);
    const itemInputRef = useRef(null);
    const [fetchSeq, setFetchSeq] = useState(0);
    const pollRef = useRef(0);
    const MAX_POLLS = 8;

    const fetchDetail = useCallback(async (mkt) => {
        try {
            const res = await fetch(`/api/portfolios/${portfolioId}?market=${mkt}`);
            if (res.status === 404) { setError('Portfolio not found'); return; }
            if (!res.ok) throw new Error('Failed to load portfolio');
            setData(await res.json());
            setError(null);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
            setFetchSeq(s => s + 1);
        }
    }, [portfolioId]);

    useEffect(() => { pollRef.current = 0; fetchDetail(market); }, [market, fetchDetail]);

    // Re-poll while live prices warm in the background so value/P/L fill in.
    useEffect(() => {
        if (data?.pricing !== 'refreshing' || pollRef.current >= MAX_POLLS) return;
        const t = setTimeout(() => { pollRef.current += 1; fetchDetail(market); }, 3000);
        return () => clearTimeout(t);
    }, [fetchSeq, data, market, fetchDetail]);

    // ---- collapse + search + pagination for the two tables ----
    const [holdingsOpen, setHoldingsOpen] = useState(false);   // transactions matter most; start holdings collapsed
    const [txnsOpen, setTxnsOpen] = useState(true);
    const [holdingsSearch, setHoldingsSearch] = useState('');
    const [txnsSearch, setTxnsSearch] = useState('');
    const [holdingsVisible, setHoldingsVisible] = useState(PAGE_SIZE);
    const [txnsVisible, setTxnsVisible] = useState(PAGE_SIZE);
    const dHoldingsSearch = useDeferredValue(holdingsSearch);
    const dTxnsSearch = useDeferredValue(txnsSearch);

    const baseHoldings = useMemo(
        () => (data?.holdings || []).filter(h => h.net_qty > 0 || h.realized_pl),
        [data]
    );
    const filteredHoldings = useMemo(
        () => baseHoldings.filter(h => matchesSearchQuery([h.item_name], dHoldingsSearch)),
        [baseHoldings, dHoldingsSearch]
    );
    const filteredTxns = useMemo(
        () => (data?.transactions || []).filter(t => matchesSearchQuery([t.item_name, t.platform, t.note, t.type], dTxnsSearch)),
        [data, dTxnsSearch]
    );
    // Reset the paint window when the filter changes.
    useEffect(() => { setHoldingsVisible(PAGE_SIZE); }, [dHoldingsSearch]);
    useEffect(() => { setTxnsVisible(PAGE_SIZE); }, [dTxnsSearch]);

    // Platforms already used (for the Platform combobox) — includes distinct values
    // like buff163 vs buff163_buy, which are meaningfully different and kept as-is.
    const platformOptions = useMemo(() => {
        const set = new Set((data?.transactions || []).map(t => t.platform).filter(Boolean));
        return Array.from(set).sort();
    }, [data]);

    const resetForm = () => { setForm(blankForm()); setEditingId(null); setFormError(null); setShowSuggest(false); };

    // Debounced typeahead against the real CS item universe (+ names already used).
    const fetchSuggests = (q) => {
        if (suggestTimer.current) clearTimeout(suggestTimer.current);
        if (q.trim().length < 2) { setItemSuggests([]); return; }
        suggestTimer.current = setTimeout(async () => {
            try {
                const res = await fetch(`/api/portfolios/item-search?market=${market}&q=${encodeURIComponent(q.trim())}`);
                const d = await res.json();
                setItemSuggests(d.items || []);
                setHighlightIdx(-1);
            } catch { setItemSuggests([]); }
        }, 200);
    };

    const onItemNameChange = (v) => { setForm(f => ({ ...f, item_name: v })); setShowSuggest(true); setHighlightIdx(-1); fetchSuggests(v); };

    // Pick a suggestion → set the exact name and its current price.
    const pickItem = (s) => {
        setForm(f => ({ ...f, item_name: s.name, price: s.price != null ? String(s.price) : f.price }));
        setItemSuggests([]); setShowSuggest(false); setHighlightIdx(-1);
    };

    // "Drip" — Draupnir drips a copy of itself; refill the form from the most recent
    // transaction (newest is first) so you can log another, similar one fast.
    const dripLast = () => {
        const last = data?.transactions?.[0];
        if (!last) return;
        setEditingId(null); setFormError(null); setShowSuggest(false);
        setForm({
            item_name: last.item_name, type: last.type, qty: last.qty,
            price: String(last.price), platform: last.platform, date: last.date, note: last.note,
        });
        setTimeout(() => itemInputRef.current?.focus(), 0);
    };

    const requiredError = () => {
        if (!form.item_name.trim()) return 'Item name is required.';
        if (!String(form.qty).trim() || parseInt(form.qty, 10) < 1) return 'Quantity is required (min 1).';
        if (form.price === '' || isNaN(parseFloat(form.price)) || parseFloat(form.price) < 0) return 'Unit price is required.';
        if (!form.platform.trim()) return 'Platform is required.';
        if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date.trim())) return 'Date is required (format YYYY-MM-DD).';
        return null;
    };

    const checkItemKnown = async (name) => {
        try {
            const res = await fetch('/api/portfolios/validate-item', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            return await res.json();   // { valid: true|false|null, suggestions? }
        } catch {
            return { valid: null };
        }
    };

    const doSave = async () => {
        const payload = {
            ...form, item_name: form.item_name.trim(), platform: form.platform.trim(),
            date: form.date.trim(), price: parseFloat(form.price), qty: parseInt(form.qty, 10),
        };
        const wasAdd = !editingId;
        const url = editingId
            ? `/api/portfolios/${portfolioId}/transactions/${editingId}`
            : `/api/portfolios/${portfolioId}/transactions`;
        try {
            const res = await fetch(url, {
                method: editingId ? 'PATCH' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const d = await res.json().catch(() => ({}));
                throw new Error(d.error || `Couldn't save transaction (${res.status})`);
            }
            const saved = await res.json().catch(() => null);
            if (wasAdd) {
                // Keep Platform + Date for fast repeated entry; clear the rest and
                // refocus the item field. Make the new row visible (top, unfiltered).
                setForm(f => ({ ...blankForm(), platform: f.platform, date: f.date }));
                setEditingId(null); setFormError(null); setShowSuggest(false); setItemSuggests([]);
                setTxnsSearch(''); setTxnsVisible(PAGE_SIZE); setTxnsOpen(true);
                setTimeout(() => itemInputRef.current?.focus(), 0);
            } else {
                resetForm();
            }
            if (saved?.id) {   // flash the saved row green briefly
                setFlashId(saved.id);
                setTimeout(() => setFlashId(null), 1500);
            }
            fetchDetail(market);
        } catch (e) {
            setError(e.message);
        }
    };

    const submitForm = async () => {
        const reqErr = requiredError();
        if (reqErr) { setFormError(reqErr); return; }
        setFormError(null);
        setChecking(true);
        const check = await checkItemKnown(form.item_name.trim());
        setChecking(false);
        if (check.valid === false) {
            // Unknown name — could be a brand-new Armory item not indexed yet, or a
            // typo. Warn but allow an override so legit new items are never blocked.
            const sugg = check.suggestions?.length ? ` Closest matches: ${check.suggestions.slice(0, 3).join('  ·  ')}.` : '';
            setDialog({
                kind: 'confirm', confirmLabel: 'Add anyway',
                title: 'Not a recognized item',
                message: `"${form.item_name.trim()}" isn't in the current CS item list.${sugg} It may be a brand-new Armory item that isn't indexed yet — or a typo. Add it anyway?`,
                onConfirm: doSave,
            });
            return;
        }
        doSave();
    };

    const startEdit = (t) => {
        setEditingId(t.id);
        setForm({ item_name: t.item_name, type: t.type, qty: t.qty, price: t.price, platform: t.platform, date: t.date, note: t.note });
    };

    const deleteTxn = (t) => setDialog({
        kind: 'confirm', danger: true, confirmLabel: 'Delete',
        title: 'Delete this transaction?',
        message: `${t.type === 'sell' ? 'Sell' : 'Buy'} ${t.qty} × ${t.item_name} — this can't be undone.`,
        onConfirm: async () => {
            await fetch(`/api/portfolios/${portfolioId}/transactions/${t.id}`, { method: 'DELETE' });
            fetchDetail(market);
        },
    });

    const rename = () => setDialog({
        kind: 'prompt', title: 'Rename portfolio', label: 'Portfolio name',
        initial: data?.name || '', confirmLabel: 'Save',
        onConfirm: async (name) => {
            await fetch(`/api/portfolios/${portfolioId}`, {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            fetchDetail(market);
        },
    });

    const deletePortfolio = () => setDialog({
        kind: 'confirm', danger: true, confirmLabel: 'Delete portfolio',
        title: `Delete "${data?.name}"?`,
        message: 'This permanently deletes the portfolio and all of its transactions.',
        onConfirm: async () => {
            await fetch(`/api/portfolios/${portfolioId}`, { method: 'DELETE' });
            navigate('/draupnir');
        },
    });

    const inputCls = 'bg-odin-dark/60 border border-white/10 rounded px-2 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-yellow-500/50';

    return (
        <div className="min-h-screen bg-odin-dark flex flex-col">
            {/* Header */}
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <Link to="/draupnir" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors">
                    <Coins size={15} className="text-yellow-500" /> Draupnir
                </Link>
                <span className="text-white/20">/</span>
                <h1 className="text-lg font-bold font-serif text-yellow-100">{data?.name || '…'}</h1>
                {data && (
                    <>
                        <button onClick={rename} className="p-1.5 rounded text-slate-500 hover:text-white hover:bg-white/10 transition-colors" title="Rename"><Pencil size={13} /></button>
                        <button onClick={deletePortfolio} className="p-1.5 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors" title="Delete portfolio"><Trash2 size={13} /></button>
                    </>
                )}
                <div className="ml-auto flex items-center gap-2">
                    <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Value on</span>
                    <select value={market} onChange={e => setMarket(e.target.value)} className={inputCls}>
                        {MARKETS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </select>
                </div>
            </div>

            <div className="flex-1 flex flex-col gap-5 p-6 max-w-[1400px] w-full mx-auto">
                {error && <div className="bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">{error}</div>}

                {loading ? (
                    <div className="flex justify-center items-center h-64"><RefreshCw className="animate-spin text-yellow-500" size={36} /></div>
                ) : data && (
                    <>
                        {/* Summary tiles */}
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                            <Tile label="Current value" value={money(data.current_value)} />
                            <Tile label="Cost basis" value={money(data.cost_basis)} cls="text-slate-300" />
                            <Tile label="Unrealized P/L" value={plStr(data.unrealized_pl)} cls={plClass(data.unrealized_pl)} />
                            <Tile label="Realized P/L" value={plStr(data.realized_pl)} cls={plClass(data.realized_pl)} />
                            <Tile label="Total P/L" value={plStr(data.total_pl)} cls={plClass(data.total_pl)} />
                        </div>
                        {data.pricing === 'refreshing' && pollRef.current < MAX_POLLS && (
                            <p className="flex items-center gap-1.5 text-xs text-slate-400 -mt-2"><RefreshCw size={12} className="animate-spin" /> Fetching live prices…</p>
                        )}
                        {data.pricing === 'no_token' && (
                            <p className="text-xs text-amber-400/80 -mt-2">Live prices unavailable — set the Tradeon token (the same one Huginn uses) to value holdings; value/unrealized shown as cost basis only.</p>
                        )}
                        {((data.pricing === 'error' && !data.priced) || (data.pricing === 'refreshing' && pollRef.current >= MAX_POLLS)) && (
                            <p className="text-xs text-amber-400/80 -mt-2">Couldn't load live prices right now — value/unrealized shown as cost basis only.</p>
                        )}

                        {/* Add / edit transaction (top of page for quick entry) */}
                        <section>
                            <div className="flex items-center gap-3 mb-2">
                                <h2 className="text-[11px] font-bold tracking-widest text-slate-400 uppercase">
                                    {editingId ? 'Edit transaction' : 'Add transaction'}
                                </h2>
                                {!editingId && data.transactions.length > 0 && (
                                    <button
                                        type="button" onClick={dripLast}
                                        title="Drip — copy your last transaction into the form (Draupnir drips a new ring)"
                                        className="flex items-center gap-1 text-[11px] font-medium text-yellow-300/80 hover:text-yellow-200 transition-colors"
                                    >
                                        <Copy size={12} /> Drip last
                                    </button>
                                )}
                            </div>
                            <form onSubmit={e => { e.preventDefault(); submitForm(); }} className="flex flex-wrap items-end gap-2 bg-odin-blue/40 border border-white/10 rounded-xl p-3">
                                <div className="relative flex-1 min-w-[200px]">
                                    <input
                                        ref={itemInputRef}
                                        className={`${inputCls} w-full`} placeholder="Item name *" autoComplete="off"
                                        value={form.item_name}
                                        onChange={e => onItemNameChange(e.target.value)}
                                        onFocus={() => { if (itemSuggests.length) setShowSuggest(true); }}
                                        onKeyDown={e => {
                                            if (e.key === 'ArrowDown' && itemSuggests.length) { e.preventDefault(); setShowSuggest(true); setHighlightIdx(i => Math.min(i + 1, itemSuggests.length - 1)); }
                                            else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlightIdx(i => Math.max(i - 1, -1)); }
                                            else if (e.key === 'Enter' && showSuggest && highlightIdx >= 0 && itemSuggests[highlightIdx]) { e.preventDefault(); pickItem(itemSuggests[highlightIdx]); }
                                            else if (e.key === 'Escape') { setShowSuggest(false); setHighlightIdx(-1); }
                                        }}
                                        onBlur={() => setTimeout(() => setShowSuggest(false), 150)}
                                    />
                                    {showSuggest && itemSuggests.length > 0 && (
                                        <ul className="absolute z-30 mt-1 w-full max-h-72 overflow-auto bg-slate-900 border border-white/10 rounded-lg shadow-2xl">
                                            {itemSuggests.map((s, i) => (
                                                <li key={s.name}>
                                                    <button
                                                        type="button"
                                                        onMouseDown={e => { e.preventDefault(); pickItem(s); }}
                                                        onMouseEnter={() => setHighlightIdx(i)}
                                                        className={`flex items-center gap-2 w-full text-left px-2 py-1.5 transition-colors ${i === highlightIdx ? 'bg-white/10' : 'hover:bg-white/10'}`}
                                                    >
                                                        <ItemIcon name={s.name} />
                                                        <span className="flex-1 text-sm text-slate-200 truncate">{s.name}</span>
                                                        {s.price != null && <span className="text-xs tabular-nums text-slate-400 shrink-0">{money(s.price)}</span>}
                                                    </button>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                                <div className="flex rounded overflow-hidden border border-white/10">
                                    <button type="button" onClick={() => setForm(f => ({ ...f, type: 'buy' }))}
                                        className={`px-3 py-1.5 text-sm font-medium transition-colors ${form.type === 'buy' ? 'bg-emerald-500/20 text-emerald-300' : 'text-slate-400 hover:bg-white/5'}`}>Buy</button>
                                    <button type="button" onClick={() => setForm(f => ({ ...f, type: 'sell' }))}
                                        className={`px-3 py-1.5 text-sm font-medium transition-colors ${form.type === 'sell' ? 'bg-orange-500/20 text-orange-300' : 'text-slate-400 hover:bg-white/5'}`}>Sell</button>
                                </div>
                                <input className={`${inputCls} w-16`} type="number" min="1" placeholder="Qty *" value={form.qty} onChange={e => setForm({ ...form, qty: e.target.value })} />
                                <input className={`${inputCls} w-24`} type="number" step="0.01" placeholder="Unit $ *" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} />
                                <input className={`${inputCls} w-32`} placeholder="Platform *" list="draupnir-platforms" value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })} />
                                <datalist id="draupnir-platforms">
                                    {platformOptions.map(p => <option key={p} value={p} />)}
                                </datalist>
                                <DateField
                                    className={`${inputCls} w-36 pr-8`} placeholder="YYYY-MM-DD *"
                                    value={form.date} onChange={v => setForm(f => ({ ...f, date: v }))}
                                />
                                <input className={`${inputCls} flex-1 min-w-[120px]`} placeholder="Note" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
                                <button type="submit" disabled={checking} className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                                    {checking ? <><RefreshCw size={14} className="animate-spin" /> Checking…</> : editingId ? <><Check size={14} /> Save</> : <><Plus size={14} /> Add</>}
                                </button>
                                {editingId && (
                                    <button type="button" onClick={resetForm} className="flex items-center gap-1 px-2 py-1.5 rounded text-slate-400 hover:text-white hover:bg-white/10 text-sm transition-colors"><X size={14} /></button>
                                )}
                            </form>
                            {formError && (
                                <p className="mt-2 text-sm text-red-400">{formError}</p>
                            )}
                        </section>

                        {/* Holdings */}
                        <section>
                            <SectionHead
                                title="Holdings" open={holdingsOpen} onToggle={() => setHoldingsOpen(o => !o)}
                                count={filteredHoldings.length} total={baseHoldings.length}
                                search={holdingsSearch} setSearch={setHoldingsSearch}
                                placeholder="Search holdings…"
                            />
                            {holdingsOpen && (
                                <>
                                    <div className="overflow-x-auto rounded-xl border border-white/5">
                                        <table className="w-full text-sm min-w-[720px]">
                                            <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                                <tr>
                                                    <th className="text-left font-semibold px-3 py-2">Item</th>
                                                    <th className="text-right font-semibold px-3 py-2">Qty</th>
                                                    <th className="text-right font-semibold px-3 py-2">Avg cost</th>
                                                    <th className="text-right font-semibold px-3 py-2">Price</th>
                                                    <th className="text-right font-semibold px-3 py-2">Value</th>
                                                    <th className="text-right font-semibold px-3 py-2">Unreal. P/L</th>
                                                    <th className="text-right font-semibold px-3 py-2">Real. P/L</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-white/5">
                                                {filteredHoldings.slice(0, holdingsVisible).map(h => (
                                                    <tr key={h.item_name} className="hover:bg-white/[0.02]">
                                                        <td className="px-3 py-2 text-slate-200">
                                                            <div className="flex items-center gap-2">
                                                                <ItemIcon name={h.item_name} />
                                                                <span>{h.item_name}</span>
                                                            </div>
                                                        </td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-300">{h.net_qty}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(h.avg_cost)}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-300">{money(h.current_price)}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-100">{money(h.market_value)}</td>
                                                        <td className={`px-3 py-2 text-right tabular-nums ${plClass(h.unrealized_pl)}`}>{plStr(h.unrealized_pl)}</td>
                                                        <td className={`px-3 py-2 text-right tabular-nums ${plClass(h.realized_pl)}`}>{h.realized_pl ? plStr(h.realized_pl) : '—'}</td>
                                                    </tr>
                                                ))}
                                                {filteredHoldings.length === 0 && (
                                                    <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-600">{baseHoldings.length ? 'No holdings match your search.' : 'No holdings yet.'}</td></tr>
                                                )}
                                            </tbody>
                                        </table>
                                    </div>
                                    <LoadMore visible={holdingsVisible} total={filteredHoldings.length} onMore={() => setHoldingsVisible(c => c + PAGE_SIZE)} />
                                </>
                            )}
                        </section>

                        {/* Transactions */}
                        <section>
                            <SectionHead
                                title="Transactions" open={txnsOpen} onToggle={() => setTxnsOpen(o => !o)}
                                count={filteredTxns.length} total={data.transactions.length}
                                search={txnsSearch} setSearch={setTxnsSearch}
                                placeholder="Search item / platform / note…"
                            />

                            {txnsOpen && (
                                <>
                                    <div className="overflow-x-auto rounded-xl border border-white/5">
                                        <table className="w-full text-sm min-w-[720px]">
                                            <thead className="bg-odin-blue/50 text-slate-500 text-[11px] uppercase tracking-wider">
                                                <tr>
                                                    <th className="text-left font-semibold px-3 py-2">Item</th>
                                                    <th className="text-left font-semibold px-3 py-2">Type</th>
                                                    <th className="text-right font-semibold px-3 py-2">Qty</th>
                                                    <th className="text-right font-semibold px-3 py-2">Unit $</th>
                                                    <th className="text-right font-semibold px-3 py-2">Total</th>
                                                    <th className="text-left font-semibold px-3 py-2">Platform</th>
                                                    <th className="text-left font-semibold px-3 py-2">Date</th>
                                                    <th className="px-3 py-2"></th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-white/5">
                                                {filteredTxns.slice(0, txnsVisible).map(t => (
                                                    <tr key={t.id} className={`transition-colors duration-1000 ${flashId === t.id ? 'bg-emerald-500/25' : editingId === t.id ? 'bg-yellow-500/5' : 'hover:bg-white/[0.02]'}`}>
                                                        <td className="px-3 py-2 text-slate-200">
                                                            <div className="flex items-center gap-2">
                                                                <ItemIcon name={t.item_name} />
                                                                <span>{t.item_name}{t.note && <span className="block text-[11px] text-slate-600 truncate max-w-[220px]">{t.note}</span>}</span>
                                                            </div>
                                                        </td>
                                                        <td className="px-3 py-2">
                                                            <span className={`text-xs px-1.5 py-0.5 rounded ${t.type === 'buy' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-orange-500/15 text-orange-300'}`}>{t.type}</span>
                                                        </td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-300">{t.qty}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-300">{money(t.price)}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-400">{money(t.qty * t.price)}</td>
                                                        <td className="px-3 py-2 text-slate-400"><div className="max-w-[160px] truncate" title={t.platform}>{t.platform || '—'}</div></td>
                                                        <td className="px-3 py-2 text-slate-500 tabular-nums whitespace-nowrap">{t.date || '—'}</td>
                                                        <td className="px-3 py-2">
                                                            <div className="flex items-center justify-end gap-1">
                                                                <button onClick={() => startEdit(t)} className="p-1 rounded text-slate-500 hover:text-white hover:bg-white/10 transition-colors"><Pencil size={13} /></button>
                                                                <button onClick={() => deleteTxn(t)} className="p-1 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"><Trash2 size={13} /></button>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                ))}
                                                {filteredTxns.length === 0 && (
                                                    <tr><td colSpan={8} className="px-3 py-8 text-center text-slate-600">{data.transactions.length ? 'No transactions match your search.' : 'No transactions yet — add one above.'}</td></tr>
                                                )}
                                            </tbody>
                                        </table>
                                    </div>
                                    <LoadMore visible={txnsVisible} total={filteredTxns.length} onMore={() => setTxnsVisible(c => c + PAGE_SIZE)} />
                                </>
                            )}
                        </section>
                    </>
                )}
            </div>

            <ConfirmDialog
                open={dialog?.kind === 'confirm'}
                title={dialog?.title} message={dialog?.message}
                confirmLabel={dialog?.confirmLabel} danger={dialog?.danger}
                onCancel={() => setDialog(null)}
                onConfirm={() => { dialog?.onConfirm?.(); setDialog(null); }}
            />
            <PromptDialog
                open={dialog?.kind === 'prompt'}
                title={dialog?.title} label={dialog?.label}
                initial={dialog?.initial} confirmLabel={dialog?.confirmLabel}
                onCancel={() => setDialog(null)}
                onConfirm={(v) => { dialog?.onConfirm?.(v); setDialog(null); }}
            />
        </div>
    );
};

export default DraupnirPortfolio;
