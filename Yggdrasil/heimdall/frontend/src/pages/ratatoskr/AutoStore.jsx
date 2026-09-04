import React, { useState, useEffect, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Boxes, Plus, X, Play, History, PackageCheck, Info, User, Lock } from 'lucide-react';

// Handy quick-add suggestions the user is actively buying.
const SUGGESTED_ITEMS = ['Fracture Case', 'Recoil Case', 'Snakebite Case', 'Clutch Case'];

// Module-scope so its identity is stable — defining it inside the component
// would remount every <Switch> on each render.
const Switch = ({ on, onClick, disabled }) => (
    <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        role="switch"
        aria-checked={on}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
            on ? 'bg-emerald-500' : 'bg-white/15'
        }`}
    >
        <span
            className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                on ? 'translate-x-5' : 'translate-x-0.5'
            }`}
        />
    </button>
);

const timeAgo = (iso) => {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
};

const RatatoskrAutoStore = () => {
    const { steamid, account } = useOutletContext();

    const [config, setConfig] = useState({
        enabled: false,
        items: [],
        accounts: [],
        history: [],
        moved_total: 0,
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [sweeping, setSweeping] = useState(false);
    const [invNames, setInvNames] = useState([]);
    const [draft, setDraft] = useState('');
    const [error, setError] = useState(null);

    const accountLabel = account?.account_name || steamid;
    const thisAccountOn = config.accounts.map(String).includes(String(steamid));

    useEffect(() => {
        fetchConfig();
        fetchInventoryNames();
    }, [steamid]);

    const fetchConfig = async () => {
        try {
            const res = await fetch('/api/ratatoskr/auto-store');
            const data = await res.json();
            if (res.ok) setConfig(data);
        } catch (err) {
            console.error('Failed to load auto-store config', err);
        } finally {
            setLoading(false);
        }
    };

    const fetchInventoryNames = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/inventory/${steamid}`);
            const data = await res.json();
            const names = [
                ...new Set(
                    (data.items || [])
                        .filter((i) => i.def_index !== 1201)
                        .map((i) => i.item_name)
                        .filter(Boolean)
                ),
            ].sort();
            setInvNames(names);
        } catch (err) {
            console.error('Failed to load inventory names', err);
        }
    };

    const patch = async (body) => {
        setSaving(true);
        setError(null);
        try {
            const res = await fetch('/api/ratatoskr/auto-store', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Failed to save');
            setConfig(data);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    };

    const toggleEnabled = () => patch({ enabled: !config.enabled });

    const toggleThisAccount = () => {
        const set = new Set(config.accounts.map(String));
        if (set.has(String(steamid))) set.delete(String(steamid));
        else set.add(String(steamid));
        patch({ accounts: [...set] });
    };

    const addItem = (name) => {
        const clean = (name || '').trim();
        if (!clean) return;
        setDraft('');
        if (config.items.some((i) => i.toLowerCase() === clean.toLowerCase())) return;
        patch({ items: [...config.items, clean] });
    };

    const removeItem = (name) =>
        patch({ items: config.items.filter((i) => i !== name) });

    const runNow = async () => {
        setSweeping(true);
        setError(null);
        try {
            const res = await fetch('/api/ratatoskr/auto-store/sweep', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Sweep failed');
            // Give the sweep a moment, then refresh history.
            setTimeout(fetchConfig, 4000);
        } catch (err) {
            setError(err.message);
        } finally {
            setTimeout(() => setSweeping(false), 4000);
        }
    };

    const suggestions = useMemo(() => {
        const chosen = new Set(config.items.map((i) => i.toLowerCase()));
        const pool = [...SUGGESTED_ITEMS, ...invNames];
        const seen = new Set();
        const out = [];
        for (const name of pool) {
            const key = name.toLowerCase();
            if (chosen.has(key) || seen.has(key)) continue;
            if (draft && !key.includes(draft.toLowerCase())) continue;
            seen.add(key);
            out.push(name);
            if (out.length >= 8) break;
        }
        return out;
    }, [config.items, invNames, draft]);

    if (loading) {
        return (
            <div className="flex justify-center py-24">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500" />
            </div>
        );
    }


    return (
        <div className="animate-in fade-in duration-500 max-w-3xl">
            {/* Header */}
            <div className="flex items-start gap-3 mb-8">
                <div className="p-2.5 bg-amber-900/30 rounded-xl border border-amber-600/30 mt-0.5">
                    <Boxes className="text-amber-500" size={22} />
                </div>
                <div>
                    <h1 className="text-3xl font-bold text-white font-serif mb-1">Auto-Store</h1>
                    <p className="text-sm text-slate-400 leading-relaxed">
                        Watched items are swept out of inventory into a random storage unit with room,
                        as soon as they arrive from your buy orders.
                    </p>
                    <p className="text-xs text-slate-500 leading-relaxed mt-1.5 flex items-center gap-1.5">
                        <Lock size={12} className="shrink-0" />
                        Items received via a trade can't enter storage and are skipped. Market
                        buys are stored normally — even inside their purchase cooldown.
                    </p>
                </div>
            </div>

            {error && (
                <div className="mb-4 bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">
                    {error}
                </div>
            )}

            {/* Master toggle + stats */}
            <div className="rounded-2xl border border-white/10 bg-odin-blue/30 p-5 mb-4">
                <div className="flex items-center justify-between gap-4">
                    <div>
                        <h2 className="text-base font-semibold text-white">Auto-store watcher</h2>
                        <p className="text-xs text-slate-400 mt-0.5">
                            Master switch. Runs on every confirmation-check cycle.
                        </p>
                    </div>
                    <Switch on={config.enabled} onClick={toggleEnabled} disabled={saving} />
                </div>

                <div className="flex flex-wrap gap-6 mt-5 pt-5 border-t border-white/5">
                    <div>
                        <p className="text-2xl font-bold text-white tabular-nums">{config.items.length}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Watched items</p>
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-emerald-400 tabular-nums">{config.moved_total}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Items moved</p>
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-white tabular-nums">{config.accounts.length}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Active accounts</p>
                    </div>
                </div>
            </div>

            {/* This account */}
            <div className="rounded-2xl border border-white/10 bg-odin-blue/30 p-5 mb-4">
                <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                        <h2 className="text-base font-semibold text-white truncate">
                            Act on this account — {accountLabel}
                        </h2>
                        <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-1.5">
                            <Info size={12} className="shrink-0" />
                            If offline, it will be auto-connected headlessly to run the sweep.
                        </p>
                    </div>
                    <Switch on={thisAccountOn} onClick={toggleThisAccount} disabled={saving} />
                </div>
            </div>

            {/* Watchlist */}
            <div className="rounded-2xl border border-white/10 bg-odin-blue/30 p-5 mb-4">
                <h2 className="text-base font-semibold text-white mb-1">Watchlist</h2>
                <p className="text-xs text-slate-400 mb-4">Item names to auto-store (exact name match).</p>

                {/* Chips */}
                {config.items.length > 0 ? (
                    <div className="flex flex-wrap gap-2 mb-4">
                        {config.items.map((name) => (
                            <span
                                key={name}
                                className="inline-flex items-center gap-1.5 pl-3 pr-1.5 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/25 text-sm text-amber-100"
                            >
                                {name}
                                <button
                                    type="button"
                                    onClick={() => removeItem(name)}
                                    disabled={saving}
                                    className="p-0.5 rounded hover:bg-white/10 text-amber-300/70 hover:text-white transition-colors"
                                    aria-label={`Remove ${name}`}
                                >
                                    <X size={13} />
                                </button>
                            </span>
                        ))}
                    </div>
                ) : (
                    <p className="text-sm text-slate-500 mb-4">Nothing watched yet.</p>
                )}

                {/* Add box */}
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && addItem(draft)}
                        placeholder="Type an item name…"
                        className="flex-1 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                    />
                    <button
                        type="button"
                        onClick={() => addItem(draft)}
                        disabled={saving || !draft.trim()}
                        className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-600/80 hover:bg-amber-600 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                    >
                        <Plus size={15} /> Add
                    </button>
                </div>

                {/* Suggestions */}
                {suggestions.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                        {suggestions.map((name) => (
                            <button
                                key={name}
                                type="button"
                                onClick={() => addItem(name)}
                                disabled={saving}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-white/10 text-xs text-slate-400 hover:text-white hover:border-amber-500/40 transition-colors"
                            >
                                <Plus size={11} /> {name}
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* Run now */}
            <div className="flex justify-end mb-8">
                <button
                    type="button"
                    onClick={runNow}
                    disabled={sweeping || saving || !config.enabled || !thisAccountOn || config.items.length === 0}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium border border-bifrost-cyan/30 bg-bifrost-cyan/10 text-bifrost-cyan hover:bg-bifrost-cyan/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title={
                        !config.enabled
                            ? 'Enable the watcher first'
                            : !thisAccountOn
                              ? 'Enable this account first'
                              : config.items.length === 0
                                ? 'Add at least one item'
                                : 'Sweep now'
                    }
                >
                    {sweeping ? (
                        <div className="w-4 h-4 border-2 border-bifrost-cyan/30 border-t-bifrost-cyan rounded-full animate-spin" />
                    ) : (
                        <Play size={15} />
                    )}
                    {sweeping ? 'Sweeping…' : 'Sweep now'}
                </button>
            </div>

            {/* History */}
            <div className="rounded-2xl border border-white/10 bg-odin-blue/30 overflow-hidden">
                <div className="flex items-center gap-2 px-5 py-3.5 border-b border-white/5">
                    <History size={16} className="text-slate-400" />
                    <h2 className="text-sm font-semibold text-white">Move history</h2>
                    <span className="text-xs text-slate-500">({config.history.length})</span>
                </div>
                {config.history.length === 0 ? (
                    <p className="text-sm text-slate-500 text-center py-10">
                        No moves yet. When a watched item arrives, it shows up here.
                    </p>
                ) : (
                    <ul className="divide-y divide-white/5 max-h-96 overflow-y-auto custom-scrollbar">
                        {config.history.map((rec, i) => (
                            <li key={`${rec.ts}-${i}`} className="flex items-center gap-3 px-5 py-3">
                                <div className="p-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 shrink-0">
                                    <PackageCheck size={15} className="text-emerald-400" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <p className="text-sm text-white">
                                        Moved <span className="font-semibold text-emerald-300">{rec.count}</span>{' '}
                                        item{rec.count === 1 ? '' : 's'} →{' '}
                                        <span className="text-amber-200">{rec.casket_name}</span>
                                    </p>
                                    {rec.items && Object.keys(rec.items).length > 0 && (
                                        <div className="flex flex-wrap gap-1 mt-1">
                                            {Object.entries(rec.items).map(([name, qty]) => (
                                                <span
                                                    key={name}
                                                    className="inline-flex items-center gap-1 text-[11px] text-slate-300 bg-white/5 border border-white/10 rounded px-1.5 py-0.5"
                                                >
                                                    <span className="font-semibold text-emerald-300">{qty}×</span>
                                                    {name}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    <div className="flex items-center gap-2 mt-1">
                                        <span
                                            className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-300 bg-sky-500/10 border border-sky-500/20 rounded px-1.5 py-0.5 max-w-[220px] truncate"
                                            title={`${rec.account_name} · ${rec.steamid || ''}`}
                                        >
                                            <User size={10} className="shrink-0" />
                                            {rec.account_name}
                                        </span>
                                        <span className="text-[11px] text-slate-500">{timeAgo(rec.ts)}</span>
                                    </div>
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

export default RatatoskrAutoStore;
