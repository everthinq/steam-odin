import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, Coins, Plus, Upload, RefreshCw, Search, LayoutGrid, Layers, History, Repeat } from 'lucide-react';
import { ConfirmDialog, PromptDialog } from '../../components/DraupnirDialog';
import PortfolioCard from '../../components/PortfolioCard';
import CombinedLedger from '../../components/CombinedLedger';
import ArbitrageDeals from '../../components/ArbitrageDeals';
import BackupsPanel from '../../components/BackupsPanel';
import {
    loadPortfolioLayout,
    savePortfolioLayout,
    mergeLayoutWithPortfolios,
    sortPortfoliosForDashboard,
    togglePortfolioPin,
    reorderPortfolioList,
} from '../../utils/portfolioLayout';

const MARKETS = [
    { id: 'steam', label: 'Steam' },
    { id: 'csfloat', label: 'CSFloat' },
    { id: 'buff', label: 'Buff163' },
    { id: 'lowest', label: 'Lowest' },
];
const marketLabel = (id) => MARKETS.find(m => m.id === id)?.label || id;

const DraupnirPortfolios = () => {
    const [portfolios, setPortfolios] = useState([]);
    const [priced, setPriced] = useState(false);
    const [pricing, setPricing] = useState('refreshing');   // no_token | fresh | refreshing | error
    const [market, setMarket] = useState('steam');
    const [viewMode, setViewMode] = useState('accounts');   // 'accounts' | 'combined' | 'arbitrage'
    const [combined, setCombined] = useState(null);
    // Arbitrage view: count + value your tagged deals across all accounts.
    const [arbitrage, setArbitrage] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [importing, setImporting] = useState(false);
    const [dialog, setDialog] = useState(null);   // styled confirm/prompt replacing native alerts
    const [showBackups, setShowBackups] = useState(false);
    const [fetchSeq, setFetchSeq] = useState(0);
    const [layout, setLayout] = useState(() => loadPortfolioLayout());
    const [dragId, setDragId] = useState(null);
    const fileRef = useRef(null);
    const pollRef = useRef(0);
    const MAX_POLLS = 8;   // ~24s of background price warming, then give up quietly

    const persistLayout = (next) => { setLayout(next); savePortfolioLayout(next); };

    const fetchList = async (mkt = market, mode = viewMode) => {
        try {
            let url;
            if (mode === 'combined') url = `/api/portfolios/combined?market=${mkt}`;
            else if (mode === 'arbitrage') url = `/api/portfolios/arbitrage?market=${mkt}`;
            else url = `/api/portfolios?market=${mkt}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error('Failed to load portfolios');
            const data = await res.json();
            if (mode === 'combined') setCombined(data);
            else if (mode === 'arbitrage') setArbitrage(data);
            else setPortfolios(data.portfolios || []);
            setPriced(!!data.priced);
            setPricing(data.pricing || 'fresh');
            setError(null);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
            setFetchSeq(s => s + 1);
        }
    };

    // Load on mount / market / view-mode / spread-param change (renders instantly from cost basis).
    useEffect(() => { pollRef.current = 0; setLoading(true); fetchList(market, viewMode); }, [market, viewMode]);

    // While prices warm in the background, re-poll a few times so they fill in.
    useEffect(() => {
        if (pricing !== 'refreshing' || pollRef.current >= MAX_POLLS) return;
        const t = setTimeout(() => { pollRef.current += 1; fetchList(market, viewMode); }, 3000);
        return () => clearTimeout(t);
    }, [fetchSeq, pricing, market, viewMode]);

    // Keep the saved layout in sync as portfolios are created/deleted.
    const idsKey = useMemo(() => portfolios.map(p => String(p.id)).sort().join(','), [portfolios]);
    useEffect(() => {
        if (!idsKey) return;
        setLayout((prev) => {
            const merged = mergeLayoutWithPortfolios(prev, portfolios);
            const sameOrder = merged.order.length === prev.order.length && merged.order.every((id, i) => id === prev.order[i]);
            const samePinned = merged.pinned.length === prev.pinned.length && merged.pinned.every((id, i) => id === prev.pinned[i]);
            if (sameOrder && samePinned) return prev;
            savePortfolioLayout(merged);
            return merged;
        });
    }, [idsKey, portfolios]);

    const filtered = useMemo(
        () => portfolios.filter(p => p.name.toLowerCase().includes(searchQuery.toLowerCase())),
        [portfolios, searchQuery]
    );
    const displayList = useMemo(() => sortPortfoliosForDashboard(filtered, layout), [filtered, layout]);
    const canReorder = !searchQuery.trim();

    const handleCreate = () => setDialog({
        kind: 'prompt', title: 'New portfolio', label: 'Portfolio name',
        confirmLabel: 'Create', placeholder: 'e.g. Main hoard',
        onConfirm: async (name) => {
            await fetch('/api/portfolios', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            fetchList();
        },
    });

    // Picking files opens a styled confirm listing them; import runs on confirm.
    const handleFilePick = (e) => {
        const files = Array.from(e.target.files || []);
        if (fileRef.current) fileRef.current.value = '';
        if (!files.length) return;
        const names = files.map(f => f.name.replace(/\.csv$/i, ''));
        setDialog({
            kind: 'confirm', confirmLabel: 'Import',
            title: `Import ${files.length} file${files.length > 1 ? 's' : ''}?`,
            message: `Creates ${files.length} new portfolio${files.length > 1 ? 's' : ''}: ${names.join(', ')}.`,
            onConfirm: () => runImport(files),
        });
    };

    const runImport = async (files) => {
        setImporting(true);
        try {
            for (const file of files) {
                const csv = await file.text();
                const name = file.name.replace(/\.csv$/i, '');
                await fetch('/api/portfolios/import', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, csv }),
                });
            }
            await fetchList();
        } catch (e) {
            setError(e.message);
        } finally {
            setImporting(false);
        }
    };

    // Download this portfolio's transactions as CSV (re-importable). Anchor to the
    // API route so the browser handles the download with the server's filename.
    const handleExport = (e, p) => {
        e.preventDefault();
        e.stopPropagation();
        const a = document.createElement('a');
        a.href = `/api/portfolios/${p.id}/export`;
        a.download = `${p.name}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    };

    const handleTogglePin = (e, p) => {
        e.preventDefault();
        e.stopPropagation();
        persistLayout(togglePortfolioPin(layout, p.id));
    };

    const handleDelete = (e, p) => {
        e.preventDefault();
        e.stopPropagation();
        setDialog({
            kind: 'confirm', danger: true, confirmLabel: 'Delete portfolio',
            title: `Delete "${p.name}"?`,
            message: 'This permanently deletes the portfolio and all of its transactions.',
            onConfirm: async () => {
                await fetch(`/api/portfolios/${p.id}`, { method: 'DELETE' });
                fetchList();
            },
        });
    };

    // ---- drag-to-reorder (disabled while searching) ----
    const handleDragStart = (id) => { if (canReorder) setDragId(String(id)); };
    const handleDragEnd = () => setDragId(null);
    const handleDropOn = (targetId) => {
        if (!canReorder || !dragId || dragId === String(targetId)) return;
        const list = [...displayList];
        const from = list.findIndex(p => String(p.id) === dragId);
        const to = list.findIndex(p => String(p.id) === String(targetId));
        if (from < 0 || to < 0) return;
        const [moved] = list.splice(from, 1);
        list.splice(to, 0, moved);
        persistLayout(reorderPortfolioList(layout, list));
        setDragId(null);
    };

    return (
        <div className="min-h-screen bg-odin-dark flex flex-col">
            {/* Header */}
            <div className="shrink-0 border-b border-white/5 bg-odin-blue/50 px-6 py-4 flex items-center gap-3 flex-wrap">
                <Link to="/" className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white transition-colors shrink-0">
                    <LayoutDashboard size={15} /> Dashboard
                </Link>
                <span className="text-white/20">/</span>
                <Coins size={18} className="text-yellow-500" />
                <h1 className="text-lg font-bold font-serif text-yellow-100">Draupnir — The Hoard</h1>

                <div className="ml-auto flex items-center gap-2 flex-wrap">
                    <input ref={fileRef} type="file" accept=".csv" multiple onChange={handleFilePick} className="hidden" />
                    <button
                        onClick={() => fileRef.current?.click()}
                        disabled={importing}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-slate-200 text-sm disabled:opacity-50 transition-colors"
                    >
                        <Upload size={14} className={importing ? 'animate-pulse' : ''} />
                        {importing ? 'Importing…' : 'Import CSV'}
                    </button>
                    <button
                        onClick={() => setShowBackups(true)}
                        title="Point-in-time backups & restore"
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-slate-200 text-sm transition-colors"
                    >
                        <History size={14} /> Backups
                    </button>
                    <button
                        onClick={handleCreate}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition-colors"
                    >
                        <Plus size={14} /> New portfolio
                    </button>
                </div>
            </div>

            {showBackups && (
                <BackupsPanel
                    onClose={() => setShowBackups(false)}
                    onRestored={() => fetchList()}
                />
            )}

            <div className="flex-1 p-4 md:p-8">
              <div className="max-w-7xl mx-auto flex flex-col gap-4">
                {error && (
                    <div className="bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">{error}</div>
                )}

                {/* Controls: view toggle + search + market + pricing status */}
                <div className="flex items-center gap-3 flex-wrap text-sm">
                    <div className="flex rounded-lg overflow-hidden border border-white/10 shrink-0">
                        <button
                            onClick={() => setViewMode('accounts')}
                            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors ${viewMode === 'accounts' ? 'bg-yellow-600 text-white' : 'text-slate-400 hover:bg-white/5'}`}
                        >
                            <LayoutGrid size={14} /> Per account
                        </button>
                        <button
                            onClick={() => setViewMode('combined')}
                            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors ${viewMode === 'combined' ? 'bg-yellow-600 text-white' : 'text-slate-400 hover:bg-white/5'}`}
                        >
                            <Layers size={14} /> Combined
                        </button>
                        <button
                            onClick={() => setViewMode('arbitrage')}
                            title="Your tagged arbitrage deals — count and profit, pooled across all accounts"
                            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors ${viewMode === 'arbitrage' ? 'bg-yellow-600 text-white' : 'text-slate-400 hover:bg-white/5'}`}
                        >
                            <Repeat size={14} /> Arbitrage
                        </button>
                    </div>
                    {viewMode === 'accounts' && (
                        <div className="relative flex-1 min-w-[220px] max-w-md">
                            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 text-slate-400" size={20} />
                            <input
                                type="text" placeholder="Search portfolios..."
                                value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                                className="w-full pl-12 pr-16 py-3 glass-panel rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all"
                            />
                            {searchQuery && (
                                <span className="absolute right-4 top-1/2 transform -translate-y-1/2 text-xs text-slate-400">
                                    {filtered.length} of {portfolios.length}
                                </span>
                            )}
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">
                            {viewMode === 'arbitrage' ? 'Open at' : 'Value on'}
                        </span>
                        <select
                            value={market} onChange={e => setMarket(e.target.value)}
                            title={viewMode === 'arbitrage' ? 'Market used to value any still-open tagged inventory' : 'Market used to value holdings'}
                            className="bg-odin-blue/60 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 focus:outline-none focus:ring-1 focus:ring-yellow-500/50"
                        >
                            {MARKETS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                        </select>
                    </div>
                    {pricing === 'refreshing' && pollRef.current < MAX_POLLS && (
                        <span className="flex items-center gap-1.5 text-xs text-slate-400">
                            <RefreshCw size={12} className="animate-spin" /> Fetching live prices…
                        </span>
                    )}
                    {pricing === 'no_token' && (
                        <span className="text-xs text-amber-400/80">
                            Live prices unavailable — set the Tradeon token (the same one Huginn uses) to value holdings; showing cost basis only
                        </span>
                    )}
                    {((pricing === 'error' && !priced) || (pricing === 'refreshing' && pollRef.current >= MAX_POLLS)) && (
                        <span className="text-xs text-amber-400/80">
                            Couldn't load live prices right now — showing cost basis only
                        </span>
                    )}
                </div>

                {viewMode === 'accounts' && portfolios.length > 0 && canReorder && (
                    <p className="-mt-1 text-xs text-slate-600">Drag cards to reorder · Pin to keep at the top</p>
                )}

                {viewMode === 'arbitrage' ? (
                    <ArbitrageDeals data={arbitrage} loading={loading} pricing={pricing} marketLabel={marketLabel} />
                ) : viewMode === 'combined' ? (
                    <CombinedLedger data={combined} loading={loading} pricing={pricing} />
                ) : loading ? (
                    <div className="flex justify-center items-center h-64">
                        <RefreshCw className="animate-spin text-yellow-500" size={36} />
                    </div>
                ) : portfolios.length === 0 ? (
                    <div className="text-center py-20 bg-odin-blue/30 border border-white/5 rounded-2xl">
                        <div className="inline-block p-4 bg-yellow-900/20 rounded-full mb-4">
                            <Coins size={36} className="text-yellow-600" />
                        </div>
                        <h2 className="text-xl font-semibold mb-2">No portfolios yet</h2>
                        <p className="text-slate-400 mb-6">Create one, or import a CSV export to get started.</p>
                    </div>
                ) : displayList.length === 0 ? (
                    <div className="text-center py-20 bg-odin-blue/30 border border-white/5 rounded-2xl">
                        <div className="inline-block p-4 bg-slate-800 rounded-full mb-4">
                            <Search size={32} className="text-slate-500" />
                        </div>
                        <h2 className="text-lg font-semibold mb-2">No matches</h2>
                        <p className="text-slate-400 mb-4">Nothing matches “{searchQuery}”.</p>
                        <button onClick={() => setSearchQuery('')} className="text-yellow-400 hover:text-yellow-300 transition-colors text-sm">Clear search</button>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-6">
                        {displayList.map(p => {
                            const pid = String(p.id);
                            const isPinned = layout.pinned.includes(pid);
                            const isDragging = dragId === pid;
                            return (
                                <div
                                    key={p.id}
                                    draggable={canReorder}
                                    onDragStart={() => handleDragStart(p.id)}
                                    onDragEnd={handleDragEnd}
                                    onDragOver={(e) => { if (canReorder) e.preventDefault(); }}
                                    onDrop={(e) => { e.preventDefault(); handleDropOn(p.id); }}
                                    className={`transition-opacity ${isDragging ? 'opacity-40' : ''}`}
                                >
                                    <PortfolioCard
                                        portfolio={p}
                                        isPinned={isPinned}
                                        draggable={canReorder}
                                        onTogglePin={(e) => handleTogglePin(e, p)}
                                        onExport={(e) => handleExport(e, p)}
                                        onDelete={(e) => handleDelete(e, p)}
                                    />
                                </div>
                            );
                        })}
                    </div>
                )}
              </div>
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
                initial={dialog?.initial} confirmLabel={dialog?.confirmLabel} placeholder={dialog?.placeholder}
                onCancel={() => setDialog(null)}
                onConfirm={(v) => { dialog?.onConfirm?.(v); setDialog(null); }}
            />
        </div>
    );
};

export default DraupnirPortfolios;
