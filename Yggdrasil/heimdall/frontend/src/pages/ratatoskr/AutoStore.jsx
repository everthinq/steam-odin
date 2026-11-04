import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Boxes, Plus, X, Play, History, PackageCheck, Info, User, Lock, Layers } from 'lucide-react';
import { readJson } from '../../utils/readJson';
import { followJob, postCommand } from '../../utils/moveCommands';

// The backend decides (auto_store.py): each account has its own watchlist, every change is one
// command for one item or one account, and the master view combines every account's watchlist.

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
    const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 60) return `${seconds} seconds ago`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} minutes ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} hours ago`;
    return `${Math.round(hours / 24)} days ago`;
};

const Card = ({ children, className = '' }) => (
    <div className={`rounded-2xl border border-white/10 bg-odin-blue/30 p-5 mb-4 ${className}`}>{children}</div>
);

const Chip = ({ label, detail, onRemove, disabled, muted }) => (
    <span
        className={`inline-flex items-center gap-1.5 pl-3 pr-1.5 py-1.5 rounded-lg border text-sm ${
            muted ? 'bg-white/5 border-white/10 text-slate-400' : 'bg-amber-500/10 border-amber-500/25 text-amber-100'
        }`}
    >
        {label}
        {detail != null && <span className="text-[11px] text-emerald-300 tabular-nums">{detail}</span>}
        <button
            type="button"
            onClick={onRemove}
            disabled={disabled}
            className="p-0.5 rounded hover:bg-white/10 text-amber-300/70 hover:text-white transition-colors"
            aria-label={`Remove ${label}`}
        >
            <X size={13} />
        </button>
    </span>
);

const HistoryList = ({ history }) => (
    <div className="rounded-2xl border border-white/10 bg-odin-blue/30 overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-3.5 border-b border-white/5">
            <History size={16} className="text-slate-400" />
            <h2 className="text-sm font-semibold text-white">Move history</h2>
            <span className="text-xs text-slate-500">({history.length})</span>
        </div>
        {history.length === 0 ? (
            <p className="text-sm text-slate-500 text-center py-10">
                No moves yet. When a watched item arrives, it shows up here.
            </p>
        ) : (
            <ul className="divide-y divide-white/5 max-h-96 overflow-y-auto custom-scrollbar">
                {history.map((record, index) => (
                    <li key={`${record.ts}-${index}`} className="flex items-center gap-3 px-5 py-3">
                        <div className="p-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 shrink-0">
                            <PackageCheck size={15} className="text-emerald-400" />
                        </div>
                        <div className="min-w-0 flex-1">
                            <p className="text-sm text-white">
                                Moved <span className="font-semibold text-emerald-300">{record.count}</span>{' '}
                                item{record.count === 1 ? '' : 's'} →{' '}
                                <span className="text-amber-200">{record.casket_name}</span>
                            </p>
                            {record.items && Object.keys(record.items).length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                    {Object.entries(record.items).map(([name, quantity]) => (
                                        <span
                                            key={name}
                                            className="inline-flex items-center gap-1 text-[11px] text-slate-300 bg-white/5 border border-white/10 rounded px-1.5 py-0.5"
                                        >
                                            <span className="font-semibold text-emerald-300">{quantity}×</span>
                                            {name}
                                        </span>
                                    ))}
                                </div>
                            )}
                            <div className="flex items-center gap-2 mt-1">
                                <span
                                    className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-300 bg-sky-500/10 border border-sky-500/20 rounded px-1.5 py-0.5 max-w-[220px] truncate"
                                    title={`${record.account_name} · ${record.steamid || ''}`}
                                >
                                    <User size={10} className="shrink-0" />
                                    {record.account_name}
                                </span>
                                <span className="text-[11px] text-slate-500">{timeAgo(record.ts)}</span>
                            </div>
                        </div>
                    </li>
                ))}
            </ul>
        )}
    </div>
);

const RatatoskrAutoStore = () => {
    const { steamid } = useOutletContext();

    const [status, setStatus] = useState(null);
    const [view, setView] = useState('account');
    const [busy, setBusy] = useState(false);
    const [sweeping, setSweeping] = useState(false);
    const [sweepNote, setSweepNote] = useState(null);
    const [suggestions, setSuggestions] = useState([]);
    const [draft, setDraft] = useState('');
    const [masterDraft, setMasterDraft] = useState('');
    const [masterAccount, setMasterAccount] = useState('');
    const [error, setError] = useState(null);
    const active = useRef(true);
    // The account open now: answers that arrive for an account left meanwhile are dropped.
    const current = useRef(steamid);
    current.current = steamid;
    const isCurrent = (account) => active.current && String(current.current) === String(account);

    useEffect(() => {
        active.current = true;
        return () => {
            active.current = false;
        };
    }, []);

    const fetchStatus = useCallback(async () => {
        try {
            const res = await fetch('/api/ratatoskr/auto-store');
            const data = await readJson(res);
            if (!res.ok) throw new Error(data.error || 'Could not load Auto-Store');
            if (active.current) setStatus(data);
        } catch (err) {
            if (active.current) setError(err.message);
        }
    }, []);

    const fetchSuggestions = useCallback(async () => {
        try {
            const res = await fetch(`/api/ratatoskr/auto-store/suggestions/${steamid}`);
            const data = await readJson(res);
            // Only the open account's answer counts (the page may have moved to another account).
            if (res.ok && active.current && String(current.current) === String(steamid)) {
                setSuggestions(data.suggestions || []);
            }
        } catch (err) {
            console.error('Failed to load Auto-Store suggestions', err);
        }
    }, [steamid]);

    useEffect(() => {
        setSuggestions([]);
        setSweepNote(null);
        setSweeping(false);
        fetchStatus();
        fetchSuggestions();
    }, [fetchStatus, fetchSuggestions]);

    // One command at a time; the backend answers the new status.
    const send = async (path, body) => {
        setBusy(true);
        setError(null);
        try {
            const data = await postCommand(`/api/ratatoskr/auto-store/${path}`, body);
            if (active.current) setStatus(data);
            return true;
        } catch (err) {
            if (active.current) setError(err.message);
            return false;
        } finally {
            if (active.current) setBusy(false);
        }
    };

    const addItem = async (account, name, clear) => {
        if (busy || !name.trim()) return;
        if (await send('items/add', { steamid: account, name })) {
            clear?.();
            if (String(account) === String(steamid)) fetchSuggestions();
        }
    };

    const removeItem = async (account, name) => {
        if (await send('items/remove', { steamid: account, name }) && String(account) === String(steamid)) {
            fetchSuggestions();
        }
    };

    const sweepNow = async () => {
        const account = steamid;
        setSweeping(true);
        setError(null);
        setSweepNote(null);
        try {
            const answer = await postCommand('/api/ratatoskr/auto-store/sweep', { steamid: account });
            if (!answer.job) {
                if (isCurrent(account)) {
                    setSweepNote(answer.nothing === 'no_room'
                        ? 'The Storage Units have no room left for the watched items.'
                        : 'Nothing to store right now.');
                }
                return;
            }
            // Leaving the account stops following; the move itself keeps running in the backend.
            const job = await followJob(answer.job.id, { isActive: () => isCurrent(account) });
            if (job.error) setError(job.error);
            await fetchStatus();
        } catch (err) {
            if (!err.cancelled && isCurrent(account)) setError(err.message);
        } finally {
            if (isCurrent(account)) setSweeping(false);
        }
    };

    if (!status) {
        return error ? (
            <div className="bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">{error}</div>
        ) : (
            <div className="flex justify-center py-24">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500" />
            </div>
        );
    }

    const account = status.accounts.find((a) => String(a.steamid) === String(steamid)) || {
        steamid, account_name: steamid, on: false, moved: 0, items: [], sweep_refused: null,
    };
    const typed = draft.trim().toLowerCase();
    const shownSuggestions = typed ? suggestions.filter((name) => name.toLowerCase().includes(typed)) : suggestions;
    const accountHistory = status.history.filter((record) => String(record.steamid) === String(steamid));
    const accountsOn = status.accounts.filter((a) => a.on).length;

    const tabClass = (name) =>
        `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
            view === name ? 'bg-amber-600/80 text-white' : 'text-slate-400 hover:text-white'
        }`;

    return (
        <div className="animate-in fade-in duration-500 max-w-3xl">
            {/* Header */}
            <div className="flex items-start gap-3 mb-6">
                <div className="p-2.5 bg-amber-900/30 rounded-xl border border-amber-600/30 mt-0.5">
                    <Boxes className="text-amber-500" size={22} />
                </div>
                <div>
                    <h1 className="text-3xl font-bold text-white font-serif mb-1">Auto-Store</h1>
                    <p className="text-sm text-slate-400 leading-relaxed">
                        Each account has its own watchlist. Watched items are moved out of that account's
                        inventory into its Storage Units as one storage (the fullest unit with room first),
                        as soon as they arrive.
                    </p>
                    <p className="text-xs text-slate-500 leading-relaxed mt-1.5 flex items-center gap-1.5">
                        <Lock size={12} className="shrink-0" />
                        Items received in a trade can't enter storage until their trade protection ends.
                        Market purchases are stored normally, even during their trade hold.
                    </p>
                </div>
            </div>

            {error && (
                <div className="mb-4 bg-red-500/15 border border-red-500/30 text-red-300 px-4 py-2.5 rounded-xl text-sm">
                    {error}
                </div>
            )}

            {/* Master switch */}
            <Card>
                <div className="flex items-center justify-between gap-4">
                    <div>
                        <h2 className="text-base font-semibold text-white">Master switch</h2>
                        <p className="text-xs text-slate-400 mt-0.5">
                            Pauses or resumes Auto-Store on every account at once. Runs on every
                            confirmation-check cycle.
                        </p>
                    </div>
                    <Switch on={status.enabled} onClick={() => send('enabled', { enabled: !status.enabled })} disabled={busy} />
                </div>
                <div className="flex flex-wrap gap-6 mt-5 pt-5 border-t border-white/5">
                    <div>
                        <p className="text-2xl font-bold text-white tabular-nums">{accountsOn}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Accounts switched on</p>
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-white tabular-nums">{status.master.length}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Items watched (all accounts)</p>
                    </div>
                    <div>
                        <p className="text-2xl font-bold text-emerald-400 tabular-nums">{status.moved_total}</p>
                        <p className="text-[11px] uppercase tracking-wider text-slate-500">Items moved</p>
                    </div>
                </div>
            </Card>

            {/* View tabs */}
            <div className="flex gap-1 mb-4 p-1 rounded-xl border border-white/10 bg-black/20 w-fit">
                <button type="button" className={tabClass('account')} onClick={() => setView('account')}>
                    <User size={14} /> {account.account_name}
                </button>
                <button type="button" className={tabClass('master')} onClick={() => setView('master')}>
                    <Layers size={14} /> All accounts
                </button>
            </div>

            {view === 'account' ? (
                <>
                    {/* This account */}
                    <Card>
                        <div className="flex items-center justify-between gap-4">
                            <div className="min-w-0">
                                <h2 className="text-base font-semibold text-white truncate">
                                    Auto-Store on {account.account_name}
                                </h2>
                                <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-1.5">
                                    <Info size={12} className="shrink-0" />
                                    If offline, it is connected automatically to run the sweep.
                                </p>
                            </div>
                            <Switch on={account.on} onClick={() => send('account', { steamid, on: !account.on })} disabled={busy} />
                        </div>
                    </Card>

                    {/* Watchlist */}
                    <Card>
                        <h2 className="text-base font-semibold text-white mb-1">Watchlist of {account.account_name}</h2>
                        <p className="text-xs text-slate-400 mb-4">
                            Item names to store on this account only (exact name, any letter case).
                        </p>
                        {account.items.length > 0 ? (
                            <div className="flex flex-wrap gap-2 mb-4">
                                {account.items.map((item) => (
                                    <Chip
                                        key={item.name}
                                        label={item.name}
                                        detail={item.moved ? `${item.moved} moved` : null}
                                        onRemove={() => removeItem(steamid, item.name)}
                                        disabled={busy}
                                    />
                                ))}
                            </div>
                        ) : (
                            <p className="text-sm text-slate-500 mb-4">Nothing watched on this account yet.</p>
                        )}

                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && addItem(steamid, draft, () => setDraft(''))}
                                placeholder="Type an item name…"
                                className="flex-1 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                            />
                            <button
                                type="button"
                                onClick={() => addItem(steamid, draft, () => setDraft(''))}
                                disabled={busy || !draft.trim()}
                                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-600/80 hover:bg-amber-600 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                            >
                                <Plus size={15} /> Add
                            </button>
                        </div>

                        {/* Suggestions: this account's inventory items a Storage Unit can hold */}
                        {shownSuggestions.length > 0 && (
                            <>
                                <p className="text-[11px] text-slate-500 mt-3 mb-1.5">In this account's inventory:</p>
                                <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto custom-scrollbar">
                                    {shownSuggestions.map((name) => (
                                        <button
                                            key={name}
                                            type="button"
                                            onClick={() => addItem(steamid, name)}
                                            disabled={busy}
                                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-white/10 text-xs text-slate-400 hover:text-white hover:border-amber-500/40 transition-colors"
                                        >
                                            <Plus size={11} /> {name}
                                        </button>
                                    ))}
                                </div>
                            </>
                        )}
                    </Card>

                    {/* Sweep now: this account only */}
                    <div className="flex items-center justify-end gap-3 mb-8">
                        {sweepNote && <span className="text-xs text-slate-400">{sweepNote}</span>}
                        <button
                            type="button"
                            onClick={sweepNow}
                            disabled={sweeping || busy || Boolean(account.sweep_refused)}
                            className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium border border-bifrost-cyan/30 bg-bifrost-cyan/10 text-bifrost-cyan hover:bg-bifrost-cyan/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                            title={account.sweep_refused?.reason || `Store the watched items on ${account.account_name} now`}
                        >
                            {sweeping ? (
                                <div className="w-4 h-4 border-2 border-bifrost-cyan/30 border-t-bifrost-cyan rounded-full animate-spin" />
                            ) : (
                                <Play size={15} />
                            )}
                            {sweeping ? 'Sweeping…' : 'Sweep now'}
                        </button>
                    </div>

                    <HistoryList history={accountHistory} />
                </>
            ) : (
                <>
                    {/* Master view: every account's watchlist combined */}
                    <Card>
                        <h2 className="text-base font-semibold text-white mb-1">Master watchlist</h2>
                        <p className="text-xs text-slate-400 mb-4">
                            Every account's watchlist combined. Removing an account from an item removes the
                            item from that account's watchlist only.
                        </p>
                        {status.master.length === 0 ? (
                            <p className="text-sm text-slate-500 mb-4">No account watches anything yet.</p>
                        ) : (
                            <ul className="divide-y divide-white/5 mb-4">
                                {status.master.map((entry) => (
                                    <li key={entry.name} className="py-3">
                                        <div className="flex items-baseline justify-between gap-3 mb-2">
                                            <p className="text-sm font-medium text-white">{entry.name}</p>
                                            <span className="text-[11px] text-emerald-300 tabular-nums">{entry.moved} moved</span>
                                        </div>
                                        <div className="flex flex-wrap gap-1.5">
                                            {entry.accounts.map((a) => (
                                                <Chip
                                                    key={a.steamid}
                                                    label={a.account_name}
                                                    detail={a.moved ? a.moved : null}
                                                    muted={!a.on}
                                                    onRemove={() => removeItem(a.steamid, entry.name)}
                                                    disabled={busy}
                                                />
                                            ))}
                                        </div>
                                    </li>
                                ))}
                            </ul>
                        )}

                        <div className="flex flex-wrap gap-2">
                            <input
                                type="text"
                                value={masterDraft}
                                onChange={(e) => setMasterDraft(e.target.value)}
                                placeholder="Item name…"
                                className="flex-1 min-w-[12rem] bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40 placeholder:text-slate-600"
                            />
                            <select
                                value={masterAccount}
                                onChange={(e) => setMasterAccount(e.target.value)}
                                className="bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/40"
                            >
                                <option value="">Account…</option>
                                {status.accounts.map((a) => (
                                    <option key={a.steamid} value={a.steamid}>{a.account_name}</option>
                                ))}
                            </select>
                            <button
                                type="button"
                                onClick={() => addItem(masterAccount, masterDraft, () => setMasterDraft(''))}
                                disabled={busy || !masterDraft.trim() || !masterAccount}
                                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-600/80 hover:bg-amber-600 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                            >
                                <Plus size={15} /> Add
                            </button>
                        </div>
                        <p className="text-[11px] text-slate-500 mt-2">Greyed accounts have Auto-Store switched off.</p>
                    </Card>

                    {/* Accounts: one switch each */}
                    <Card>
                        <h2 className="text-base font-semibold text-white mb-3">Accounts</h2>
                        <ul className="divide-y divide-white/5">
                            {status.accounts.map((a) => (
                                <li key={a.steamid} className="flex items-center justify-between gap-3 py-2.5">
                                    <div className="min-w-0">
                                        <p className="text-sm text-white truncate">{a.account_name}</p>
                                        <p className="text-[11px] text-slate-500">
                                            {a.items.length} watched · {a.moved} moved
                                        </p>
                                    </div>
                                    <Switch on={a.on} onClick={() => send('account', { steamid: a.steamid, on: !a.on })} disabled={busy} />
                                </li>
                            ))}
                        </ul>
                    </Card>

                    <HistoryList history={status.history} />
                </>
            )}
        </div>
    );
};

export default RatatoskrAutoStore;
