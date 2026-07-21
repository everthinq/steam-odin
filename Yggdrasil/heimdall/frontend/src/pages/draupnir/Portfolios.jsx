import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { LayoutDashboard, Coins, Plus, Upload, Download, RefreshCw, ArrowUpDown, Trash2 } from 'lucide-react';
import { ConfirmDialog, PromptDialog } from '../../components/DraupnirDialog';

const MARKETS = [
    { id: 'steam', label: 'Steam' },
    { id: 'csfloat', label: 'CSFloat' },
    { id: 'buff', label: 'Buff163' },
    { id: 'lowest', label: 'Lowest' },
];

const SORTS = [
    { id: 'created', label: 'Created (newest)', fn: (a, b) => b.created_at.localeCompare(a.created_at) },
    { id: 'name', label: 'Name (A–Z)', fn: (a, b) => a.name.localeCompare(b.name) },
    { id: 'value', label: 'Value (high→low)', fn: (a, b) => (b.current_value ?? -1) - (a.current_value ?? -1) },
    { id: 'pl', label: 'Total P/L (high→low)', fn: (a, b) => (b.total_pl ?? 0) - (a.total_pl ?? 0) },
    { id: 'invested', label: 'Invested (high→low)', fn: (a, b) => b.invested - a.invested },
];

const money = (v) => v == null ? '—' : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const plClass = (v) => v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
const plStr = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${money(v)}`;

const DraupnirPortfolios = () => {
    const [portfolios, setPortfolios] = useState([]);
    const [priced, setPriced] = useState(false);
    const [pricing, setPricing] = useState('refreshing');   // no_token | fresh | refreshing | error
    const [market, setMarket] = useState('steam');
    const [sortId, setSortId] = useState('value');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [importing, setImporting] = useState(false);
    const [dialog, setDialog] = useState(null);   // styled confirm/prompt replacing native alerts
    const [fetchSeq, setFetchSeq] = useState(0);
    const fileRef = useRef(null);
    const pollRef = useRef(0);
    const MAX_POLLS = 8;   // ~24s of background price warming, then give up quietly

    const fetchList = async (mkt = market) => {
        try {
            const res = await fetch(`/api/portfolios?market=${mkt}`);
            if (!res.ok) throw new Error('Failed to load portfolios');
            const data = await res.json();
            setPortfolios(data.portfolios || []);
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

    // Load on mount / market change (renders instantly from cost basis).
    useEffect(() => { pollRef.current = 0; setLoading(true); fetchList(market); }, [market]);

    // While prices warm in the background, re-poll a few times so they fill in.
    useEffect(() => {
        if (pricing !== 'refreshing' || pollRef.current >= MAX_POLLS) return;
        const t = setTimeout(() => { pollRef.current += 1; fetchList(market); }, 3000);
        return () => clearTimeout(t);
    }, [fetchSeq, pricing, market]);

    const sorted = useMemo(() => {
        const fn = (SORTS.find(s => s.id === sortId) || SORTS[0]).fn;
        return [...portfolios].sort(fn);
    }, [portfolios, sortId]);

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
                        onClick={handleCreate}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 text-white text-sm font-medium transition-colors"
                    >
                        <Plus size={14} /> New portfolio
                    </button>
                </div>
            </div>

            <div className="flex-1 flex flex-col gap-4 p-6 max-w-6xl w-full mx-auto">
                {error && (
                    <div className="bg-red-500/20 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg text-sm">{error}</div>
                )}

                {/* Controls */}
                <div className="flex items-center gap-3 flex-wrap text-sm">
                    <div className="flex items-center gap-2">
                        <ArrowUpDown size={14} className="text-slate-500" />
                        <select
                            value={sortId} onChange={e => setSortId(e.target.value)}
                            className="bg-odin-blue/60 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 focus:outline-none focus:ring-1 focus:ring-yellow-500/50"
                        >
                            {SORTS.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                        </select>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold tracking-widest text-slate-600 uppercase">Value on</span>
                        <select
                            value={market} onChange={e => setMarket(e.target.value)}
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

                {loading ? (
                    <div className="flex justify-center items-center h-64">
                        <RefreshCw className="animate-spin text-yellow-500" size={36} />
                    </div>
                ) : sorted.length === 0 ? (
                    <div className="text-center py-20 bg-odin-blue/30 border border-white/5 rounded-2xl">
                        <div className="inline-block p-4 bg-yellow-900/20 rounded-full mb-4">
                            <Coins size={36} className="text-yellow-600" />
                        </div>
                        <h2 className="text-xl font-semibold mb-2">No portfolios yet</h2>
                        <p className="text-slate-400 mb-6">Create one, or import a CSV export to get started.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {sorted.map(p => (
                            <Link
                                key={p.id} to={`/draupnir/${p.id}`}
                                className="group relative block bg-odin-blue/40 border border-white/5 rounded-xl p-4 hover:border-yellow-500/40 hover:bg-odin-blue/60 transition-all"
                            >
                                <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-all">
                                    <button
                                        onClick={(e) => handleExport(e, p)}
                                        className="p-1.5 rounded-lg text-slate-600 hover:text-yellow-300 hover:bg-yellow-500/10 transition-all"
                                        title="Export as CSV"
                                    >
                                        <Download size={14} />
                                    </button>
                                    <button
                                        onClick={(e) => handleDelete(e, p)}
                                        className="p-1.5 rounded-lg text-slate-600 hover:text-red-400 hover:bg-red-500/10 transition-all"
                                        title="Delete portfolio"
                                    >
                                        <Trash2 size={14} />
                                    </button>
                                </div>
                                <h3 className="font-serif font-semibold text-yellow-100 pr-16 truncate">{p.name}</h3>
                                <p className="text-xs text-slate-500 mb-3">
                                    {p.holdings_count} holdings · {p.txn_count} transactions
                                </p>
                                <div className="grid grid-cols-2 gap-y-1.5 text-sm">
                                    <span className="text-slate-500">Value</span>
                                    <span className="text-right tabular-nums text-slate-100">{money(p.current_value)}</span>
                                    <span className="text-slate-500">Invested</span>
                                    <span className="text-right tabular-nums text-slate-300">{money(p.cost_basis)}</span>
                                    <span className="text-slate-500">Unrealized</span>
                                    <span className={`text-right tabular-nums ${plClass(p.unrealized_pl)}`}>{plStr(p.unrealized_pl)}</span>
                                    <span className="text-slate-500">Realized</span>
                                    <span className={`text-right tabular-nums ${plClass(p.realized_pl)}`}>{plStr(p.realized_pl)}</span>
                                </div>
                            </Link>
                        ))}
                    </div>
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
                initial={dialog?.initial} confirmLabel={dialog?.confirmLabel} placeholder={dialog?.placeholder}
                onCancel={() => setDialog(null)}
                onConfirm={(v) => { dialog?.onConfirm?.(v); setDialog(null); }}
            />
        </div>
    );
};

export default DraupnirPortfolios;
